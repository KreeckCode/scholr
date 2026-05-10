from django.conf import settings
from django.db import models

from .learner import Learner
from .school import AcademicYear, Term
from .structure import GradeStream


class AbsenceReason(models.Model):
    """
    Lookup table for why a learner is absent.
    is_excused controls whether the day counts against the attendance percentage.
    Seeded via seed_attendance_reasons management command.
    """

    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=10, unique=True)
    is_excused = models.BooleanField(
        default=True,
        help_text="Excused absences count in LURITS but may affect promotion differently",
    )
    requires_note = models.BooleanField(
        default=False,
        help_text="Flag when a written note or medical certificate is required",
    )
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "absence_reasons"
        verbose_name = "Absence Reason"
        verbose_name_plural = "Absence Reasons"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class AttendanceSession(models.Model):
    """
    One register for one stream on one day.
    session_type 'am' = morning register (default for SA schools).
    The same stream on the same day can have multiple sessions (am/pm/period)
    but for MVP only 'am' is used.
    """

    SESSION_AM = "am"
    SESSION_PM = "pm"

    SESSION_CHOICES = [
        (SESSION_AM, "Morning Register"),
        (SESSION_PM, "Afternoon Register"),
    ]

    stream = models.ForeignKey(
        GradeStream,
        on_delete=models.CASCADE,
        related_name="attendance_sessions",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name="attendance_sessions",
    )
    date = models.DateField(db_index=True)
    session_type = models.CharField(
        max_length=10,
        choices=SESSION_CHOICES,
        default=SESSION_AM,
    )

    taken_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="registers_taken",
    )
    taken_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # School day flag - False if this session covers a non-teaching day
    # captured retrospectively for record keeping
    is_school_day = models.BooleanField(default=True)
    notes = models.TextField(
        blank=True,
        help_text="Optional note about this session (e.g. school event, half-day)",
    )

    class Meta:
        db_table = "attendance_sessions"
        verbose_name = "Attendance Session"
        verbose_name_plural = "Attendance Sessions"
        unique_together = [["stream", "date", "session_type"]]
        ordering = ["-date"]
        indexes = [
            models.Index(
                fields=["stream", "date"],
                name="idx_session_stream_date",
            ),
            models.Index(
                fields=["academic_year", "date"],
                name="idx_session_year_date",
            ),
        ]

    def __str__(self):
        return (
            f"{self.stream.get_display_name()} - "
            f"{self.date.strftime('%d %b %Y')} ({self.get_session_type_display()})"
        )


class AttendanceRecord(models.Model):
    """
    One attendance mark per learner per session.
    Present (P) is the default - only deviations need to be recorded.
    Late (L) learners are present but arrived after the bell.
    """

    STATUS_PRESENT = "P"
    STATUS_ABSENT = "A"
    STATUS_LATE = "L"
    STATUS_EXCUSED = "E"  # Absent with prior permission (sports trip, school event)

    STATUS_CHOICES = [
        (STATUS_PRESENT, "Present"),
        (STATUS_ABSENT, "Absent"),
        (STATUS_LATE, "Late"),
        (STATUS_EXCUSED, "Excused Absence"),
    ]

    session = models.ForeignKey(
        AttendanceSession,
        on_delete=models.CASCADE,
        related_name="records",
    )
    learner = models.ForeignKey(
        Learner,
        on_delete=models.CASCADE,
        related_name="attendance_records",
    )
    status = models.CharField(
        max_length=1,
        choices=STATUS_CHOICES,
        default=STATUS_PRESENT,
        db_index=True,
    )
    reason = models.ForeignKey(
        AbsenceReason,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_records",
    )
    arrival_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Actual arrival time for late learners",
    )
    note = models.CharField(
        max_length=200,
        blank=True,
        help_text="Brief note (e.g. 'Doctor's certificate received')",
    )

    class Meta:
        db_table = "attendance_records"
        verbose_name = "Attendance Record"
        verbose_name_plural = "Attendance Records"
        unique_together = [["session", "learner"]]
        indexes = [
            models.Index(
                fields=["learner", "session"],
                name="idx_record_learner_session",
            ),
            models.Index(
                fields=["status"],
                name="idx_record_status",
            ),
        ]

    def __str__(self):
        return (
            f"{self.learner.get_full_name()} - "
            f"{self.session.date} - {self.get_status_display()}"
        )

    def is_absent_or_late(self):
        return self.status in {self.STATUS_ABSENT, self.STATUS_LATE}

    def counts_as_absent(self):
        """True for unexcused absences - affects the 80% threshold."""
        return self.status == self.STATUS_ABSENT


class AttendanceSummary(models.Model):
    """
    Pre-computed attendance statistics per learner per term.
    Recalculated by AttendanceCalculatorService after every register save.
    The is_at_risk flag fires when attendance falls below 80%.
    """

    THRESHOLD_AT_RISK = 80

    learner = models.ForeignKey(
        Learner,
        on_delete=models.CASCADE,
        related_name="attendance_summaries",
    )
    stream = models.ForeignKey(
        GradeStream,
        on_delete=models.CASCADE,
        related_name="attendance_summaries",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name="attendance_summaries",
    )
    term_number = models.PositiveSmallIntegerField(
        choices=[(1, "Term 1"), (2, "Term 2"), (3, "Term 3"), (4, "Term 4")]
    )

    total_days = models.IntegerField(
        default=0,
        help_text="School days in this term where register was taken for this stream",
    )
    days_present = models.IntegerField(default=0)
    days_absent = models.IntegerField(default=0)
    days_late = models.IntegerField(default=0)
    days_excused = models.IntegerField(default=0)

    attendance_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=100,
        help_text="(days_present + days_late) / total_days × 100",
    )
    is_at_risk = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True when attendance percentage drops below 80%",
    )

    calculated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "attendance_summaries"
        verbose_name = "Attendance Summary"
        verbose_name_plural = "Attendance Summaries"
        unique_together = [["learner", "stream", "academic_year", "term_number"]]
        ordering = ["attendance_percentage"]
        indexes = [
            models.Index(
                fields=["academic_year", "term_number", "is_at_risk"],
                name="idx_summary_year_term_risk",
            ),
        ]

    def __str__(self):
        return (
            f"{self.learner.get_full_name()} - "
            f"T{self.term_number} {self.academic_year.year}: "
            f"{self.attendance_percentage}%"
        )

    def days_recorded(self):
        return self.days_present + self.days_absent + self.days_late + self.days_excused