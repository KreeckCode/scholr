"""
Attendance summary calculation service.

Recalculates AttendanceSummary for one or more learners in a stream
after each register save. Designed to be fast enough to run synchronously
for a class of 40 learners.

The 80% threshold (CAPS promotion requirement) is applied here.
Excused absences (school sports, medical with certificate) count as present
for the percentage calculation but are recorded separately for LURITS.
"""

from decimal import Decimal, ROUND_HALF_UP

from ..models.attendance import AttendanceRecord, AttendanceSession, AttendanceSummary
from ..models.school import AcademicYear, Term


class AttendanceCalculatorService:

    PRECISION = Decimal("0.01")

    def calculate_for_session(self, session: AttendanceSession):
        """
        Recalculate summaries for all learners affected by one session.
        Called after every register save.
        """
        term_number = self._get_term_number(session)
        if not term_number:
            return

        learner_ids = session.records.values_list("learner_id", flat=True)
        from ..models.learner import Learner

        for learner in Learner.objects.filter(pk__in=learner_ids):
            self.calculate_for_learner(
                learner,
                session.stream,
                session.academic_year,
                term_number,
            )

    def calculate_for_learner(
        self, learner, stream, academic_year, term_number: int
    ) -> AttendanceSummary:
        """
        Recalculate the attendance summary for one learner for one term.
        """
        term = Term.objects.filter(
            academic_year=academic_year, term_number=term_number
        ).first()

        if not term:
            return None

        # All sessions for this stream within this term's date range
        sessions = AttendanceSession.objects.filter(
            stream=stream,
            academic_year=academic_year,
            date__gte=term.start_date,
            date__lte=term.end_date,
            is_school_day=True,
        )
        total = sessions.count()

        # Records for this learner in those sessions
        records = AttendanceRecord.objects.filter(
            session__in=sessions,
            learner=learner,
        )

        present = records.filter(status=AttendanceRecord.STATUS_PRESENT).count()
        absent = records.filter(status=AttendanceRecord.STATUS_ABSENT).count()
        late = records.filter(status=AttendanceRecord.STATUS_LATE).count()
        excused = records.filter(status=AttendanceRecord.STATUS_EXCUSED).count()

        # I'm treating late and excused as "present" for the percentage
        # since CAPS only counts unexcused absences against promotion
        if total > 0:
            attended = present + late + excused
            pct = Decimal(str(attended / total * 100)).quantize(
                self.PRECISION, rounding=ROUND_HALF_UP
            )
        else:
            pct = Decimal("100.00")

        summary, _ = AttendanceSummary.objects.update_or_create(
            learner=learner,
            stream=stream,
            academic_year=academic_year,
            term_number=term_number,
            defaults={
                "total_days": total,
                "days_present": present,
                "days_absent": absent,
                "days_late": late,
                "days_excused": excused,
                "attendance_percentage": pct,
                "is_at_risk": pct < AttendanceSummary.THRESHOLD_AT_RISK,
            },
        )
        return summary

    def _get_term_number(self, session: AttendanceSession) -> int | None:
        """Find which term the session date falls in."""
        term = Term.objects.filter(
            academic_year=session.academic_year,
            start_date__lte=session.date,
            end_date__gte=session.date,
        ).first()
        return term.term_number if term else None