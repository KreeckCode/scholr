from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from ..mixins import HODOrSeniorMixin, StaffMixin
from ..models.curriculum import AssessmentTask, SubjectOffering
from ..models.marks import (
    AssessmentRecord,
    FinalMark,
    SubjectEnrollment,
    TermMark,
)
from ..models.learner import LearnerEnrollment
from ..models.school import AcademicYear, Term
from ..models.structure import Grade, GradeStream
from ..services.marks_calculator import MarksCalculatorService


# ==================== DASHBOARD ====================

class MarksDashboardView(StaffMixin, View):
    """
    Entry point for marks management.
    Teachers see their homeroom classes.
    HODs see subjects in their department.
    Admin and Principal see everything.
    """

    template_name = "academics/marks/dashboard.html"

    def get(self, request):
        current_year = AcademicYear.get_current()
        current_term = Term.get_current()
        user = request.user

        # I'm building the available offering+stream combinations
        # scoped to the user's role
        sessions = self._get_sessions(user, current_year)

        return render(request, self.template_name, {
            "sessions": sessions,
            "current_year": current_year,
            "current_term": current_term,
            "can_calculate_finals": user.role in {"admin", "principal"},
        })

    def _get_sessions(self, user, current_year):
        """
        Build a list of capture sessions the user can access.
        Each session is a dict: {offering, stream, task_count, captured_count}
        """
        if not current_year:
            return []

        streams = self._get_accessible_streams(user, current_year)
        sessions = []

        for stream in streams:
            grade = stream.grade
            offerings = SubjectOffering.objects.filter(
                grade=grade, is_active=True
            ).select_related("subject", "grade")

            # HODs only see their department's subjects
            if user.role == "hod":
                staff = getattr(user, "staff_profile", None)
                if staff and staff.department_id:
                    offerings = offerings.filter(
                        subject__department_id=staff.department_id
                    )

            for offering in offerings:
                # Task count for current term
                current_term = Term.get_current()
                term_num = current_term.term_number if current_term else 1
                task_count = offering.tasks.filter(
                    term_number=term_num, is_active=True
                ).count()

                # Learner count in stream
                learner_count = LearnerEnrollment.objects.filter(
                    stream=stream,
                    academic_year=current_year,
                    status=LearnerEnrollment.STATUS_ACTIVE,
                    is_active=True,
                ).count()

                if learner_count == 0:
                    continue

                sessions.append({
                    "offering": offering,
                    "stream": stream,
                    "task_count": task_count,
                    "learner_count": learner_count,
                    "term_number": term_num,
                })

        return sessions

    def _get_accessible_streams(self, user, current_year):
        base_qs = GradeStream.objects.filter(
            academic_year=current_year, is_active=True
        ).select_related("grade", "homeroom_teacher")

        if user.role in {"admin", "principal"}:
            return base_qs.order_by("grade__grade_number", "name")

        if user.role in {"hod", "teacher"}:
            return base_qs.filter(
                homeroom_teacher=user
            ).order_by("grade__grade_number", "name")

        return GradeStream.objects.none()


# ==================== MARKS CAPTURE ====================

class MarksCaptureView(StaffMixin, View):
    """
    Spreadsheet-style marks capture for a subject offering and stream.
    GET: Render the grid with existing marks pre-filled.
    POST: Save marks, validate, recalculate term marks.

    Access rules:
      - Admin / Principal: any offering + stream
      - HOD: any stream, subjects in their department only
      - Teacher: their homeroom stream, any subject offered there
    """

    template_name = "academics/marks/capture.html"

    def _get_offering(self, pk):
        return get_object_or_404(
            SubjectOffering.objects.select_related(
                "subject", "grade", "subject__curriculum", "subject__department"
            ),
            pk=pk,
        )

    def _get_stream(self, pk):
        return get_object_or_404(
            GradeStream.objects.select_related(
                "grade", "academic_year", "homeroom_teacher"
            ),
            pk=pk,
        )

    def _assert_access(self, user, offering, stream):
        if user.role in {"admin", "principal"}:
            return

        if user.role == "hod":
            staff = getattr(user, "staff_profile", None)
            if not staff or offering.subject.department_id != staff.department_id:
                raise PermissionDenied(
                    "HODs can only capture marks for subjects in their department."
                )
            return

        if user.role == "teacher":
            if stream.homeroom_teacher_id != user.pk:
                raise PermissionDenied(
                    "Teachers can only capture marks for their homeroom class."
                )
            return

        raise PermissionDenied

    def _get_term_number(self, request):
        """Use the query param if provided, otherwise fall back to current term."""
        param = request.GET.get("term", "")
        if param.isdigit() and int(param) in (1, 2, 3, 4):
            return int(param)
        current = Term.get_current()
        return current.term_number if current else 1

    def _ensure_subject_enrollments(self, offering, stream, academic_year):
        """
        Auto-create SubjectEnrollment records for all active learners in the
        stream who don't yet have one for this offering.
        Using bulk_create with ignore_conflicts for atomicity.
        """
        learner_enrollments = LearnerEnrollment.objects.filter(
            stream=stream,
            academic_year=academic_year,
            status=LearnerEnrollment.STATUS_ACTIVE,
            is_active=True,
        ).select_related("learner")

        existing_ids = set(
            SubjectEnrollment.objects.filter(
                subject_offering=offering,
                academic_year=academic_year,
            ).values_list("learner_id", flat=True)
        )

        to_create = [
            SubjectEnrollment(
                learner=le.learner,
                subject_offering=offering,
                academic_year=academic_year,
                stream=stream,
            )
            for le in learner_enrollments
            if le.learner_id not in existing_ids
        ]

        if to_create:
            SubjectEnrollment.objects.bulk_create(to_create, ignore_conflicts=True)

    def _build_grid(self, enrollments, tasks, records_map):
        """
        Build row-based grid for the template.
        Each row: {enrollment, learner, cells: [{task, record}]}
        Django templates cannot do dict[variable] lookups so I'm pre-building
        the structure here.
        """
        rows = []
        for enrollment in enrollments:
            cells = []
            for task in tasks:
                record = records_map.get((enrollment.pk, task.pk))
                cells.append({"task": task, "record": record})
            rows.append({
                "enrollment": enrollment,
                "learner": enrollment.learner,
                "cells": cells,
            })
        return rows

    def get(self, request, offering_pk, stream_pk):
        offering = self._get_offering(offering_pk)
        stream = self._get_stream(stream_pk)
        self._assert_access(request.user, offering, stream)

        current_year = stream.academic_year
        term_number = self._get_term_number(request)
        current_term = Term.get_current()
        marks_open = current_term.marks_open if current_term else False

        # Auto-create enrollments for learners in this stream
        self._ensure_subject_enrollments(offering, stream, current_year)

        enrollments = SubjectEnrollment.objects.filter(
            subject_offering=offering,
            academic_year=current_year,
            stream=stream,
            is_active=True,
        ).select_related("learner").order_by(
            "learner__last_name", "learner__first_name"
        )

        tasks = list(
            offering.tasks.filter(
                term_number=term_number, is_active=True
            ).order_by("sequence")
        )

        # Pre-fetch all records for this grid in a single query
        records = AssessmentRecord.objects.filter(
            subject_enrollment__in=enrollments,
            assessment_task__in=tasks,
        ).select_related("assessment_task")

        records_map = {
            (r.subject_enrollment_id, r.assessment_task_id): r
            for r in records
        }

        rows = self._build_grid(enrollments, tasks, records_map)

        # Term selector for the header
        all_terms = [
            {"number": n, "label": f"Term {n}", "selected": n == term_number}
            for n in (1, 2, 3, 4)
        ]

        return render(request, self.template_name, {
            "offering": offering,
            "stream": stream,
            "tasks": tasks,
            "rows": rows,
            "term_number": term_number,
            "all_terms": all_terms,
            "marks_open": marks_open,
            "current_year": current_year,
        })

    def post(self, request, offering_pk, stream_pk):
        offering = self._get_offering(offering_pk)
        stream = self._get_stream(stream_pk)
        self._assert_access(request.user, offering, stream)

        current_year = stream.academic_year
        current_term = Term.get_current()
        term_number = self._get_term_number(request)

        # Enforce marks-open gate
        if not current_term or not current_term.marks_open:
            messages.error(
                request,
                "Mark capture is currently closed for this term. "
                "Contact the principal to open mark capture.",
            )
            return redirect(
                reverse("academics:marks_capture", args=[offering_pk, stream_pk])
                + f"?term={term_number}"
            )

        enrollments = SubjectEnrollment.objects.filter(
            subject_offering=offering,
            academic_year=current_year,
            stream=stream,
            is_active=True,
        ).select_related("learner")

        tasks = list(
            offering.tasks.filter(
                term_number=term_number, is_active=True
            )
        )

        errors = []
        records_to_upsert = []

        for enrollment in enrollments:
            for task in tasks:
                absent_key = f"absent_{task.pk}_{enrollment.pk}"
                mark_key = f"mark_{task.pk}_{enrollment.pk}"
                is_absent = absent_key in request.POST
                raw_str = request.POST.get(mark_key, "").strip()

                if not raw_str and not is_absent:
                    continue  # Nothing entered - skip

                raw_mark = None
                if not is_absent:
                    try:
                        raw_mark = Decimal(raw_str)
                    except InvalidOperation:
                        errors.append(
                            f"{enrollment.learner.get_full_name()} / "
                            f"{task.name}: '{raw_str}' is not a valid number."
                        )
                        continue

                    if raw_mark < 0 or raw_mark > task.max_marks:
                        errors.append(
                            f"{enrollment.learner.get_full_name()} / "
                            f"{task.name}: Mark must be between 0 and {task.max_marks}."
                        )
                        continue

                records_to_upsert.append({
                    "enrollment": enrollment,
                    "task": task,
                    "raw_mark": raw_mark,
                    "is_absent": is_absent,
                })

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            # Save all valid records in a single transaction
            with transaction.atomic():
                for item in records_to_upsert:
                    record, created = AssessmentRecord.objects.get_or_create(
                        subject_enrollment=item["enrollment"],
                        assessment_task=item["task"],
                        defaults={
                            "raw_mark": item["raw_mark"],
                            "is_absent": item["is_absent"],
                            "captured_by": request.user,
                        },
                    )
                    if not created:
                        record.raw_mark = item["raw_mark"]
                        record.is_absent = item["is_absent"]
                        record.captured_by = request.user
                        record.save(
                            update_fields=[
                                "raw_mark", "is_absent",
                                "captured_by", "updated_at",
                            ]
                        )

                # Recalculate term marks for all affected enrollments
                calculator = MarksCalculatorService()
                for enrollment in enrollments:
                    calculator.calculate_and_save_term_mark(enrollment, term_number)

            messages.success(request, "Marks saved and term marks recalculated.")

        redirect_url = (
            reverse("academics:marks_capture", args=[offering_pk, stream_pk])
            + f"?term={term_number}"
        )
        return redirect(redirect_url)


# ==================== TERM SUMMARY ====================

class TermMarksSummaryView(HODOrSeniorMixin, View):
    """
    Summary of term marks for all learners in a stream for a subject.
    Shows per-term SBA and exam percentages plus completion status.
    Provides a button to calculate final marks.
    """

    template_name = "academics/marks/term_summary.html"

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

    def get(self, request, offering_pk, stream_pk):
        offering = self._get_offering(offering_pk)
        stream = self._get_stream(stream_pk)

        enrollments = SubjectEnrollment.objects.filter(
            subject_offering=offering,
            academic_year=stream.academic_year,
            stream=stream,
            is_active=True,
        ).select_related("learner").order_by(
            "learner__last_name", "learner__first_name"
        )

        # Pre-fetch term marks and final marks in two queries
        term_marks_qs = TermMark.objects.filter(
            subject_enrollment__in=enrollments
        )
        term_map = {}
        for tm in term_marks_qs:
            term_map.setdefault(tm.subject_enrollment_id, {})[tm.term_number] = tm

        final_marks_qs = FinalMark.objects.filter(
            subject_enrollment__in=enrollments
        )
        final_map = {fm.subject_enrollment_id: fm for fm in final_marks_qs}

        rows = []
        for enrollment in enrollments:
            terms_data = term_map.get(enrollment.pk, {})
            rows.append({
                "enrollment": enrollment,
                "learner": enrollment.learner,
                "term1": terms_data.get(1),
                "term2": terms_data.get(2),
                "term3": terms_data.get(3),
                "term4": terms_data.get(4),
                "final": final_map.get(enrollment.pk),
            })

        return render(request, self.template_name, {
            "offering": offering,
            "stream": stream,
            "rows": rows,
            "can_publish": request.user.role in {"admin", "principal"},
        })

    def post(self, request, offering_pk, stream_pk):
        """Recalculate final marks for all learners in this stream."""
        offering = self._get_offering(offering_pk)
        stream = self._get_stream(stream_pk)

        enrollments = SubjectEnrollment.objects.filter(
            subject_offering=offering,
            academic_year=stream.academic_year,
            stream=stream,
            is_active=True,
        )

        calculator = MarksCalculatorService()
        count = 0
        with transaction.atomic():
            for enrollment in enrollments:
                calculator.calculate_and_save_final_mark(enrollment)
                count += 1

        messages.success(
            request,
            f"Final marks calculated for {count} learners.",
        )
        return redirect(
            reverse("academics:term_summary", args=[offering_pk, stream_pk])
        )