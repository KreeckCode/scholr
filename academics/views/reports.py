from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView

from ..forms.reports import ConductRatingForm, GenerateReportCardsForm
from ..mixins import HODOrSeniorMixin, SeniorManagementMixin, StaffMixin
from ..models.curriculum import SubjectOffering
from ..models.learner import LearnerEnrollment
from ..models.marks import SubjectEnrollment, TermMark
from ..models.reports import (
    ConductRating, ReportCard, ReportCardTemplate, TeacherComment,
)
from ..models.school import AcademicYear, Term
from ..models.structure import Grade, GradeStream
from ..services.report_generator import ReportCardGeneratorService


class ReportsDashboardView(SeniorManagementMixin, View):
    """
    Report card management overview.
    Shows streams with generation and release status per term.
    """

    template_name = "academics/reports/dashboard.html"

    def get(self, request):
        current_year = AcademicYear.get_current()
        current_term = Term.get_current()

        streams = GradeStream.objects.filter(
            academic_year=current_year, is_active=True
        ).select_related("grade", "homeroom_teacher").order_by(
            "grade__grade_number", "name"
        ) if current_year else []

        form = GenerateReportCardsForm(academic_year=current_year)

        # Completion stats per stream per current term
        term_num = current_term.term_number if current_term else 1
        stream_stats = []
        for stream in streams:
            total = LearnerEnrollment.objects.filter(
                stream=stream, academic_year=current_year,
                status=LearnerEnrollment.STATUS_ACTIVE, is_active=True,
            ).count()
            generated = ReportCard.objects.filter(
                stream=stream, academic_year=current_year,
                term_number=term_num, is_generated=True,
            ).count()
            released = ReportCard.objects.filter(
                stream=stream, academic_year=current_year,
                term_number=term_num, is_released=True,
            ).count()
            stream_stats.append({
                "stream": stream,
                "total": total,
                "generated": generated,
                "released": released,
            })

        return render(request, self.template_name, {
            "stream_stats": stream_stats,
            "current_year": current_year,
            "current_term": current_term,
            "form": form,
            "term_num": term_num,
        })


class CommentsCaptureView(StaffMixin, View):
    """
    Capture teacher comments for one subject across all learners in a stream.
    Teachers see only their homeroom stream's subjects.
    HODs and senior management see all.
    """

    template_name = "academics/reports/comments_capture.html"

    def _get_offering(self, pk):
        return get_object_or_404(
            SubjectOffering.objects.select_related("subject", "grade"),
            pk=pk,
        )

    def _get_stream(self, pk):
        return get_object_or_404(
            GradeStream.objects.select_related("grade", "academic_year"),
            pk=pk,
        )

    def _assert_access(self, user, stream):
        if user.role in {"admin", "principal", "hod"}:
            return
        if user.role == "teacher" and stream.homeroom_teacher_id == user.pk:
            return
        raise PermissionDenied

    def _get_term(self, request):
        t = request.GET.get("term", "")
        if t.isdigit() and int(t) in (1, 2, 3, 4):
            return int(t)
        ct = Term.get_current()
        return ct.term_number if ct else 1

    def _build_rows(self, offering, stream, academic_year, term_number):
        """
        Build rows for the template: one row per learner with their
        existing comment pre-filled.
        """
        enrollments = SubjectEnrollment.objects.filter(
            subject_offering=offering,
            academic_year=academic_year,
            stream=stream,
            is_active=True,
        ).select_related("learner").order_by(
            "learner__last_name", "learner__first_name"
        )

        existing_comments = {
            tc.subject_enrollment_id: tc.comment
            for tc in TeacherComment.objects.filter(
                subject_enrollment__in=enrollments,
                term_number=term_number,
            )
        }

        return [
            {
                "enrollment": e,
                "learner": e.learner,
                "comment": existing_comments.get(e.pk, ""),
            }
            for e in enrollments
        ]

    def get(self, request, offering_pk, stream_pk):
        offering = self._get_offering(offering_pk)
        stream = self._get_stream(stream_pk)
        self._assert_access(request.user, stream)
        term_number = self._get_term(request)
        rows = self._build_rows(offering, stream, stream.academic_year, term_number)

        return render(request, self.template_name, {
            "offering": offering,
            "stream": stream,
            "term_number": term_number,
            "rows": rows,
            "all_terms": [
                {"number": n, "label": f"Term {n}", "selected": n == term_number}
                for n in (1, 2, 3, 4)
            ],
        })

    def post(self, request, offering_pk, stream_pk):
        offering = self._get_offering(offering_pk)
        stream = self._get_stream(stream_pk)
        self._assert_access(request.user, stream)
        term_number = self._get_term(request)
        academic_year = stream.academic_year

        enrollments = SubjectEnrollment.objects.filter(
            subject_offering=offering,
            academic_year=academic_year,
            stream=stream,
            is_active=True,
        )

        with transaction.atomic():
            for enrollment in enrollments:
                comment_text = request.POST.get(
                    f"comment_{enrollment.pk}", ""
                ).strip()
                if comment_text:
                    TeacherComment.objects.update_or_create(
                        subject_enrollment=enrollment,
                        term_number=term_number,
                        defaults={
                            "comment": comment_text,
                            "captured_by": request.user,
                        },
                    )

        messages.success(request, "Comments saved.")
        return redirect(
            f"{request.path}?term={term_number}"
        )


class ConductCaptureView(StaffMixin, View):
    """
    Capture conduct ratings, effort, and attendance for all learners in a stream.
    Only accessible to the homeroom teacher, HODs, and senior management.
    """

    template_name = "academics/reports/conduct_capture.html"

    def _get_stream(self, pk):
        return get_object_or_404(
            GradeStream.objects.select_related("grade", "academic_year"),
            pk=pk,
        )

    def _assert_access(self, user, stream):
        if user.role in {"admin", "principal", "hod"}:
            return
        if user.role == "teacher" and stream.homeroom_teacher_id == user.pk:
            return
        raise PermissionDenied

    def _get_term(self, request):
        t = request.GET.get("term", "")
        if t.isdigit() and int(t) in (1, 2, 3, 4):
            return int(t)
        ct = Term.get_current()
        return ct.term_number if ct else 1

    def _build_rows(self, stream, academic_year, term_number):
        enrollments = LearnerEnrollment.objects.filter(
            stream=stream,
            academic_year=academic_year,
            status=LearnerEnrollment.STATUS_ACTIVE,
            is_active=True,
        ).select_related("learner").order_by(
            "learner__last_name", "learner__first_name"
        )

        existing = {
            cr.learner_id: cr
            for cr in ConductRating.objects.filter(
                stream=stream,
                academic_year=academic_year,
                term_number=term_number,
            )
        }

        return [
            {"learner": e.learner, "conduct": existing.get(e.learner_id)}
            for e in enrollments
        ]

    def get(self, request, stream_pk):
        stream = self._get_stream(stream_pk)
        self._assert_access(request.user, stream)
        term_number = self._get_term(request)
        rows = self._build_rows(stream, stream.academic_year, term_number)

        return render(request, self.template_name, {
            "stream": stream,
            "term_number": term_number,
            "rows": rows,
            "rating_choices": ConductRating.RATING_CHOICES,
            "all_terms": [
                {"number": n, "label": f"Term {n}", "selected": n == term_number}
                for n in (1, 2, 3, 4)
            ],
        })

    def post(self, request, stream_pk):
        stream = self._get_stream(stream_pk)
        self._assert_access(request.user, stream)
        term_number = self._get_term(request)
        academic_year = stream.academic_year

        learner_ids = LearnerEnrollment.objects.filter(
            stream=stream,
            academic_year=academic_year,
            status=LearnerEnrollment.STATUS_ACTIVE,
            is_active=True,
        ).values_list("learner_id", flat=True)

        with transaction.atomic():
            for learner_id in learner_ids:
                ConductRating.objects.update_or_create(
                    learner_id=learner_id,
                    stream=stream,
                    academic_year=academic_year,
                    term_number=term_number,
                    defaults={
                        "effort_rating": request.POST.get(f"effort_{learner_id}", ""),
                        "conduct_rating": request.POST.get(f"conduct_{learner_id}", ""),
                        "days_absent": int(request.POST.get(f"absent_{learner_id}", 0) or 0),
                        "days_late": int(request.POST.get(f"late_{learner_id}", 0) or 0),
                        "homeroom_comment": request.POST.get(f"comment_{learner_id}", "").strip(),
                        "principal_comment": request.POST.get(f"principal_{learner_id}", "").strip(),
                        "captured_by": request.user,
                    },
                )

        messages.success(request, "Conduct ratings saved.")
        return redirect(f"{request.path}?term={term_number}")


class GenerateReportCardsView(SeniorManagementMixin, View):
    """
    Generate PDF report cards for a stream and term.
    Requires WeasyPrint. Generation is synchronous - for large classes
    this will take 20-40 seconds. Celery integration is a future enhancement.
    """

    def post(self, request, stream_pk):
        stream = get_object_or_404(GradeStream, pk=stream_pk, is_active=True)
        academic_year = stream.academic_year
        term_number = int(request.POST.get("term_number", 1))
        template_id = request.POST.get("template_id")

        template = None
        if template_id:
            template = ReportCardTemplate.objects.filter(pk=template_id).first()
        if not template:
            template = ReportCardTemplate.get_default()

        if not template:
            messages.error(
                request,
                "No report card template configured. "
                "Create one in admin under Report Card Templates.",
            )
            return redirect("academics:reports_dashboard")

        service = ReportCardGeneratorService()
        success, errors_count, error_msgs = service.generate_for_stream(
            stream, academic_year, term_number, template, request.user
        )

        if error_msgs:
            for msg in error_msgs[:5]:
                messages.error(request, msg)

        messages.success(
            request,
            f"Generated {success} report cards for "
            f"{stream.get_display_name()} Term {term_number}."
            + (f" {errors_count} failed." if errors_count else ""),
        )
        return redirect(
            reverse_lazy(
                "academics:report_card_list",
                kwargs={"stream_pk": stream_pk, "term_number": term_number},
            )
        )


class ReportCardListView(SeniorManagementMixin, View):
    """List generated report cards for a stream and term."""

    template_name = "academics/reports/list.html"

    def get(self, request, stream_pk, term_number):
        stream = get_object_or_404(GradeStream, pk=stream_pk, is_active=True)

        report_cards = ReportCard.objects.filter(
            stream=stream,
            academic_year=stream.academic_year,
            term_number=term_number,
        ).select_related("learner").order_by(
            "learner__last_name", "learner__first_name"
        )

        return render(request, self.template_name, {
            "stream": stream,
            "term_number": term_number,
            "report_cards": report_cards,
            "all_generated": report_cards.filter(is_generated=True).count(),
            "all_released": report_cards.filter(is_released=True).count(),
            "total": report_cards.count(),
        })

    def post(self, request, stream_pk, term_number):
        """Release all generated report cards for this stream+term."""
        stream = get_object_or_404(GradeStream, pk=stream_pk, is_active=True)

        updated = ReportCard.objects.filter(
            stream=stream,
            academic_year=stream.academic_year,
            term_number=term_number,
            is_generated=True,
            is_released=False,
        ).update(
            is_released=True,
            released_at=timezone.now(),
            released_by=request.user,
        )

        messages.success(
            request,
            f"{updated} report cards released to the parent portal.",
        )
        return redirect(
            "academics:report_card_list",
            stream_pk=stream_pk,
            term_number=term_number,
        )


class ReportCardPreviewView(StaffMixin, View):
    """
    Preview a single report card as HTML in the browser.
    Does not require WeasyPrint - renders the same template used for PDF.
    """

    def get(self, request, learner_pk, term_number):
        from ..models.learner import Learner

        learner = get_object_or_404(Learner, pk=learner_pk, is_active=True)
        academic_year = AcademicYear.get_current()
        if not academic_year:
            raise Http404("No current academic year set.")

        # Find the learner's current stream
        enrollment = LearnerEnrollment.objects.filter(
            learner=learner,
            academic_year=academic_year,
            is_active=True,
        ).select_related("stream").first()

        if not enrollment or not enrollment.stream:
            raise Http404("Learner has no active stream enrollment.")

        stream = enrollment.stream
        template = ReportCardTemplate.get_default()
        if not template:
            raise Http404("No report card template configured.")

        service = ReportCardGeneratorService()
        context = service.build_context(
            learner, stream, academic_year, term_number, template
        )
        # Render HTML and return directly - preview mode
        html = service.render_html(context)
        return HttpResponse(html)


class ReportCardDownloadView(SeniorManagementMixin, View):
    """Download the stored PDF for a specific report card."""

    def get(self, request, pk):
        report_card = get_object_or_404(ReportCard, pk=pk, is_generated=True)

        if not report_card.pdf_file:
            messages.error(request, "PDF file not found. Please regenerate.")
            return redirect("academics:reports_dashboard")

        try:
            return FileResponse(
                report_card.pdf_file.open("rb"),
                content_type="application/pdf",
                as_attachment=True,
                filename=(
                    f"{report_card.learner.admission_number}_"
                    f"T{report_card.term_number}_{report_card.academic_year.year}.pdf"
                ),
            )
        except (OSError, FileNotFoundError):
            raise Http404("PDF file not found on disk. Please regenerate.")