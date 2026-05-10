import datetime

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from ..mixins import ManagementMixin, StaffMixin
from ..models.attendance import (
    AbsenceReason,
    AttendanceRecord,
    AttendanceSession,
    AttendanceSummary,
)
from ..models.learner import Learner, LearnerEnrollment
from ..models.school import AcademicYear, Term
from ..models.structure import Grade, GradeStream
from ..services.attendance_calculator import AttendanceCalculatorService


class AttendanceDashboardView(StaffMixin, View):
    """
    Today's register status across all streams the user can access.
    Shows which streams are done, which need attention.
    """

    template_name = "academics/attendance/dashboard.html"

    def _get_streams(self, user, current_year):
        qs = GradeStream.objects.filter(
            academic_year=current_year, is_active=True
        ).select_related("grade", "homeroom_teacher").order_by(
            "grade__grade_number", "name"
        )
        if user.role in {"admin", "principal", "admin_clerk"}:
            return qs
        if user.role in {"teacher", "hod"}:
            return qs.filter(homeroom_teacher=user)
        return GradeStream.objects.none()

    def get(self, request):
        current_year = AcademicYear.get_current()
        current_term = Term.get_current()
        today = datetime.date.today()
        user = request.user

        streams = self._get_streams(user, current_year) if current_year else []

        # Check which streams have had their register taken today
        sessions_today = {
            s.stream_id
            for s in AttendanceSession.objects.filter(
                date=today,
                session_type=AttendanceSession.SESSION_AM,
                stream__in=streams,
            )
        }

        stream_status = []
        for stream in streams:
            taken = stream.pk in sessions_today
            learner_count = LearnerEnrollment.objects.filter(
                stream=stream,
                academic_year=current_year,
                status=LearnerEnrollment.STATUS_ACTIVE,
                is_active=True,
            ).count()
            stream_status.append({
                "stream": stream,
                "register_taken": taken,
                "learner_count": learner_count,
            })

        # Count today's absences across all visible streams
        absent_today = AttendanceRecord.objects.filter(
            session__date=today,
            session__stream__in=streams,
            status__in=[AttendanceRecord.STATUS_ABSENT, AttendanceRecord.STATUS_LATE],
        ).count()

        # Learners at-risk (below 80%) for current term
        at_risk_count = 0
        if current_year and current_term:
            at_risk_count = AttendanceSummary.objects.filter(
                academic_year=current_year,
                term_number=current_term.term_number,
                is_at_risk=True,
                stream__in=streams,
            ).count()

        return render(request, self.template_name, {
            "stream_status": stream_status,
            "today": today,
            "current_year": current_year,
            "current_term": current_term,
            "absent_today": absent_today,
            "at_risk_count": at_risk_count,
        })


class TakeRegisterView(StaffMixin, View):
    """
    Take or edit the morning register for a stream on a given date.

    Access rules:
    - Teachers: own homeroom stream, today only
    - HODs: any stream, today only
    - Admin / Principal / Clerk: any stream, any date (corrections)

    GET: Show the register form, pre-filled with any existing records.
    POST: Save records, recalculate summaries.
    """

    template_name = "academics/attendance/register.html"

    def _get_stream(self, pk):
        return get_object_or_404(
            GradeStream.objects.select_related("grade", "academic_year"),
            pk=pk,
            is_active=True,
        )

    def _get_date(self, request):
        date_str = request.GET.get("date") or request.POST.get("register_date")
        if date_str:
            try:
                return datetime.date.fromisoformat(date_str)
            except ValueError:
                pass
        return datetime.date.today()

    def _assert_access(self, user, stream, register_date):
        today = datetime.date.today()
        can_edit_past = user.role in {"admin", "principal", "admin_clerk"}

        if register_date < today and not can_edit_past:
            raise PermissionDenied(
                "Only admin, principal, and admin clerks can edit past registers."
            )

        if user.role in {"admin", "principal", "admin_clerk"}:
            return

        if user.role in {"teacher", "hod"}:
            if stream.homeroom_teacher_id != user.pk:
                raise PermissionDenied(
                    "You can only take the register for your own homeroom class."
                )
            return

        raise PermissionDenied

    def _get_learners(self, stream):
        return LearnerEnrollment.objects.filter(
            stream=stream,
            academic_year=stream.academic_year,
            status=LearnerEnrollment.STATUS_ACTIVE,
            is_active=True,
        ).select_related("learner").order_by(
            "learner__last_name", "learner__first_name"
        )

    def _build_rows(self, learner_enrollments, session):
        """
        Build rows for the template. Each row has the learner and
        their existing record (or None if not yet taken).
        """
        if session:
            existing = {
                r.learner_id: r
                for r in AttendanceRecord.objects.filter(
                    session=session
                ).select_related("reason")
            }
        else:
            existing = {}

        return [
            {
                "learner": le.learner,
                "record": existing.get(le.learner_id),
            }
            for le in learner_enrollments
        ]

    def get(self, request, stream_pk):
        stream = self._get_stream(stream_pk)
        register_date = self._get_date(request)
        self._assert_access(request.user, stream, register_date)

        session = AttendanceSession.objects.filter(
            stream=stream,
            date=register_date,
            session_type=AttendanceSession.SESSION_AM,
        ).first()

        learner_enrollments = self._get_learners(stream)
        rows = self._build_rows(learner_enrollments, session)
        reasons = AbsenceReason.objects.filter(is_active=True).order_by("sort_order")

        return render(request, self.template_name, {
            "stream": stream,
            "register_date": register_date,
            "today": datetime.date.today(),
            "session": session,
            "rows": rows,
            "reasons": reasons,
            "status_choices": AttendanceRecord.STATUS_CHOICES,
            "can_edit_past": request.user.role in {"admin", "principal", "admin_clerk"},
        })

    def post(self, request, stream_pk):
        stream = self._get_stream(stream_pk)
        register_date = self._get_date(request)
        self._assert_access(request.user, stream, register_date)

        learner_enrollments = self._get_learners(stream)
        reasons_map = {str(r.pk): r for r in AbsenceReason.objects.all()}
        calculator = AttendanceCalculatorService()

        with transaction.atomic():
            # Create or get the session for this date
            session, session_created = AttendanceSession.objects.get_or_create(
                stream=stream,
                date=register_date,
                session_type=AttendanceSession.SESSION_AM,
                defaults={
                    "academic_year": stream.academic_year,
                    "taken_by": request.user,
                },
            )
            if not session_created:
                session.taken_by = request.user
                session.save(update_fields=["taken_by", "updated_at"])

            for le in learner_enrollments:
                learner = le.learner
                status = request.POST.get(
                    f"status_{learner.pk}",
                    AttendanceRecord.STATUS_PRESENT,
                )
                reason_id = request.POST.get(f"reason_{learner.pk}", "")
                arrival_str = request.POST.get(f"arrival_{learner.pk}", "")
                note = request.POST.get(f"note_{learner.pk}", "").strip()

                reason = reasons_map.get(reason_id)

                arrival_time = None
                if arrival_str:
                    try:
                        h, m = arrival_str.split(":")
                        arrival_time = datetime.time(int(h), int(m))
                    except (ValueError, AttributeError):
                        pass

                AttendanceRecord.objects.update_or_create(
                    session=session,
                    learner=learner,
                    defaults={
                        "status": status,
                        "reason": reason if status in ("A", "L", "E") else None,
                        "arrival_time": arrival_time if status == "L" else None,
                        "note": note,
                    },
                )

            # Recalculate summaries for all learners in this stream
            calculator.calculate_for_session(session)

        messages.success(
            request,
            f"Register saved for {stream.get_display_name()} on "
            f"{register_date.strftime('%d %B %Y')}.",
        )
        return redirect(
            reverse("academics:take_register", kwargs={"stream_pk": stream_pk})
            + f"?date={register_date}"
        )


class AttendanceSummaryView(StaffMixin, View):
    """
    Attendance summary for all learners in a stream for a term.
    Sorted by percentage ascending (worst first) so at-risk learners
    appear at the top.
    """

    template_name = "academics/attendance/summary.html"

    def _get_stream(self, pk):
        return get_object_or_404(
            GradeStream.objects.select_related("grade", "academic_year"),
            pk=pk,
            is_active=True,
        )

    def _get_term_number(self, request):
        t = request.GET.get("term", "")
        if t.isdigit() and int(t) in (1, 2, 3, 4):
            return int(t)
        ct = Term.get_current()
        return ct.term_number if ct else 1

    def get(self, request, stream_pk):
        stream = self._get_stream(stream_pk)
        term_number = self._get_term_number(request)

        summaries = AttendanceSummary.objects.filter(
            stream=stream,
            academic_year=stream.academic_year,
            term_number=term_number,
        ).select_related("learner").order_by("attendance_percentage")

        # Learners in the stream who have no summary yet
        enrolled_ids = LearnerEnrollment.objects.filter(
            stream=stream,
            academic_year=stream.academic_year,
            status=LearnerEnrollment.STATUS_ACTIVE,
            is_active=True,
        ).values_list("learner_id", flat=True)

        summarised_ids = summaries.values_list("learner_id", flat=True)
        missing_count = len(set(enrolled_ids) - set(summarised_ids))

        at_risk = summaries.filter(is_at_risk=True).count()
        registers_taken = AttendanceSession.objects.filter(
            stream=stream,
            academic_year=stream.academic_year,
        ).count()

        return render(request, self.template_name, {
            "stream": stream,
            "term_number": term_number,
            "summaries": summaries,
            "at_risk_count": at_risk,
            "missing_count": missing_count,
            "registers_taken": registers_taken,
            "total_enrolled": len(enrolled_ids),
            "all_terms": [
                {"number": n, "label": f"Term {n}", "selected": n == term_number}
                for n in (1, 2, 3, 4)
            ],
        })


class LearnerAttendanceHistoryView(StaffMixin, View):
    """Individual learner's attendance record — all sessions, all statuses."""

    template_name = "academics/attendance/history.html"

    def get(self, request, learner_pk):
        learner = get_object_or_404(Learner, pk=learner_pk, is_active=True)
        current_year = AcademicYear.get_current()

        # Scoped access — same rules as LearnerDetailView
        self._assert_access(request.user, learner)

        term_number = request.GET.get("term", "")
        records_qs = AttendanceRecord.objects.filter(
            learner=learner,
            session__academic_year=current_year,
        ).select_related("session__stream", "reason").order_by("-session__date")

        if term_number.isdigit():
            term = Term.objects.filter(
                academic_year=current_year,
                term_number=int(term_number),
            ).first()
            if term:
                records_qs = records_qs.filter(
                    session__date__gte=term.start_date,
                    session__date__lte=term.end_date,
                )

        summaries = AttendanceSummary.objects.filter(
            learner=learner,
            academic_year=current_year,
        ).order_by("term_number")

        return render(request, self.template_name, {
            "learner": learner,
            "records": records_qs,
            "summaries": summaries,
            "current_year": current_year,
            "selected_term": term_number,
            "all_terms": [
                {"number": n, "label": f"Term {n}"}
                for n in (1, 2, 3, 4)
            ],
        })

    def _assert_access(self, user, learner):
        if user.role in {"admin", "principal", "admin_clerk", "hod", "teacher"}:
            return
        from ..models.learner import LearnerGuardian
        if user.role == "parent":
            if not LearnerGuardian.objects.filter(
                guardian__user=user, learner=learner
            ).exists():
                raise PermissionDenied
            return
        raise PermissionDenied


class AttendanceAlertView(StaffMixin, View):
    """
    List of learners across all streams whose attendance is below 80%.
    Sorted by percentage ascending. Admin and principal see all.
    Teachers see only their homeroom stream.
    """

    template_name = "academics/attendance/dashboard.html"

    def get(self, request):
        current_year = AcademicYear.get_current()
        current_term = Term.get_current()
        if not current_year or not current_term:
            return redirect("academics:attendance_dashboard")

        qs = AttendanceSummary.objects.filter(
            academic_year=current_year,
            term_number=current_term.term_number,
            is_at_risk=True,
        ).select_related("learner", "stream__grade").order_by("attendance_percentage")

        user = request.user
        if user.role in {"teacher", "hod"}:
            streams = GradeStream.objects.filter(
                homeroom_teacher=user,
                academic_year=current_year,
                is_active=True,
            )
            qs = qs.filter(stream__in=streams)

        return render(request, "academics/attendance/alerts.html", {
            "alerts": qs,
            "current_term": current_term,
            "threshold": AttendanceSummary.THRESHOLD_AT_RISK,
        })