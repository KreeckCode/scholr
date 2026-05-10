"""
Report card generation service.

Builds the full context for each learner's report card,
renders it as HTML, and converts to PDF using WeasyPrint.

The generated PDF is saved to disk and referenced in the ReportCard model.
"""

import io
from django.template.loader import render_to_string
from django.utils import timezone

from ..models.marks import SubjectEnrollment, FinalMark, TermMark
from ..models.reports import ConductRating, ReportCard, ReportCardTemplate, TeacherComment
from ..models.learner import LearnerEnrollment
from ..models.school import School
from ..utils.caps_marks import get_symbol_and_description


class ReportCardGeneratorService:
    """
    Generates report cards for learners.
    Call generate_for_stream() for bulk generation or
    build_context() + render_html() for preview.
    """

    def build_context(self, learner, stream, academic_year, term_number, template=None):
        """
        Build the complete context dict for one learner's report card.
        This is the single source of truth for all data on the card.
        """
        if template is None:
            template = ReportCardTemplate.get_default()

        school = School.get_current()

        # Learner's grade enrollment for this year
        learner_enrollment = LearnerEnrollment.objects.filter(
            learner=learner,
            academic_year=academic_year,
            is_active=True,
        ).select_related("grade", "stream").first()

        # All subject enrollments for this learner this year
        subject_enrollments = SubjectEnrollment.objects.filter(
            learner=learner,
            academic_year=academic_year,
            is_active=True,
        ).select_related(
            "subject_offering__subject",
            "subject_offering",
        ).order_by("subject_offering__subject__sort_order")

        # Pre-fetch term marks and final marks in two queries
        term_marks_qs = TermMark.objects.filter(
            subject_enrollment__in=subject_enrollments,
            term_number=term_number,
        )
        term_mark_map = {tm.subject_enrollment_id: tm for tm in term_marks_qs}

        final_marks_qs = FinalMark.objects.filter(
            subject_enrollment__in=subject_enrollments
        )
        final_mark_map = {fm.subject_enrollment_id: fm for fm in final_marks_qs}

        # Pre-fetch teacher comments for this term
        comments_qs = TeacherComment.objects.filter(
            subject_enrollment__in=subject_enrollments,
            term_number=term_number,
        )
        comment_map = {tc.subject_enrollment_id: tc.comment for tc in comments_qs}

        # Build subject rows with CAPS symbols
        subject_rows = []
        for enrollment in subject_enrollments:
            offering = enrollment.subject_offering
            term_mark = term_mark_map.get(enrollment.pk)
            final_mark = final_mark_map.get(enrollment.pk)

            # Use term SBA mark for term reports; final for year-end
            if term_number == 4 and final_mark and final_mark.final_percentage is not None:
                percentage = final_mark.final_percentage
            elif term_mark:
                percentage = term_mark.sba_percentage or term_mark.exam_percentage
            else:
                percentage = None

            symbol, description = get_symbol_and_description(percentage)

            subject_rows.append({
                "subject_name": offering.subject.name,
                "subject_code": offering.subject.code,
                "percentage": percentage,
                "symbol": symbol,
                "description": description,
                "pass_mark": offering.pass_mark,
                "passed": percentage is not None and percentage >= offering.pass_mark,
                "teacher_comment": comment_map.get(enrollment.pk, ""),
                "sba_weighting": offering.sba_weighting,
                "exam_weighting": offering.exam_weighting,
            })

        # Conduct and attendance
        conduct = ConductRating.objects.filter(
            learner=learner,
            stream=stream,
            academic_year=academic_year,
            term_number=term_number,
        ).first()

        return {
            "school": school,
            "learner": learner,
            "stream": stream,
            "learner_enrollment": learner_enrollment,
            "academic_year": academic_year,
            "term_number": term_number,
            "term_label": f"Term {term_number}",
            "subject_rows": subject_rows,
            "conduct": conduct,
            "template": template,
            "generated_at": timezone.now(),
        }

    def render_html(self, context):
        """Render the report card HTML from the standard template."""
        return render_to_string(
            "academics/reports/report_card_pdf.html", context
        )

    def render_to_pdf(self, html_string):
        """
        Convert HTML string to PDF bytes using WeasyPrint.
        Returns bytes object. Raises ImportError if WeasyPrint not installed.
        """
        try:
            from weasyprint import HTML, CSS
        except ImportError:
            raise ImportError(
                "WeasyPrint is required for PDF generation. "
                "Install it with: pip install weasyprint"
            )

        pdf_bytes = HTML(string=html_string).write_pdf(
            stylesheets=[
                CSS(string=(
                    "@page { size: A4; margin: 12mm 15mm 12mm 15mm; } "
                    "body { font-family: Arial, sans-serif; font-size: 9pt; } "
                ))
            ]
        )
        return pdf_bytes

    def generate_for_learner(
        self, learner, stream, academic_year, term_number,
        template=None, generated_by=None
    ):
        """
        Generate and save a single report card PDF.
        Returns the ReportCard instance.
        """
        from django.core.files.base import ContentFile

        if template is None:
            template = ReportCardTemplate.get_default()

        context = self.build_context(learner, stream, academic_year, term_number, template)
        html = self.render_html(context)
        pdf_bytes = self.render_to_pdf(html)

        filename = (
            f"{learner.admission_number}_"
            f"T{term_number}_{academic_year.year}.pdf"
        )

        report_card, _ = ReportCard.objects.get_or_create(
            learner=learner,
            academic_year=academic_year,
            term_number=term_number,
            defaults={"stream": stream, "template": template},
        )
        report_card.stream = stream
        report_card.template = template
        report_card.is_generated = True
        report_card.generated_at = timezone.now()
        report_card.generated_by = generated_by
        report_card.pdf_file.save(filename, ContentFile(pdf_bytes), save=False)
        report_card.save()

        return report_card

    def generate_for_stream(
        self, stream, academic_year, term_number,
        template=None, generated_by=None
    ):
        """
        Generate report cards for all active learners in a stream.
        Returns (success_count, error_count, errors).
        """
        learner_enrollments = LearnerEnrollment.objects.filter(
            stream=stream,
            academic_year=academic_year,
            status=LearnerEnrollment.STATUS_ACTIVE,
            is_active=True,
        ).select_related("learner")

        success_count = 0
        error_count = 0
        errors = []

        for le in learner_enrollments:
            try:
                self.generate_for_learner(
                    le.learner, stream, academic_year,
                    term_number, template, generated_by
                )
                success_count += 1
            except Exception as exc:
                error_count += 1
                errors.append(f"{le.learner.get_full_name()}: {exc}")

        return success_count, error_count, errors