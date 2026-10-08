from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from .base import TimeStampedModel
from .cohort import CohortMembership, CohortSession
from .programme import ProgrammeCourse


class SessionAttendance(TimeStampedModel):
    """One register mark: a participant at a session."""

    class Status(models.TextChoices):
        PRESENT = 'present', 'Present'
        LATE = 'late', 'Late'
        ABSENT = 'absent', 'Absent'
        EXCUSED = 'excused', 'Excused'

    ATTENDED = (Status.PRESENT, Status.LATE)

    session = models.ForeignKey(CohortSession, on_delete=models.CASCADE, related_name='attendance')
    membership = models.ForeignKey(CohortMembership, on_delete=models.CASCADE, related_name='attendance')
    status = models.CharField(max_length=10, choices=Status.choices)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    recorded_at = models.DateTimeField(help_text='When the mark was made, on the device that made it.')
    received_at = models.DateTimeField(default=timezone.now, help_text='When the server stored it.')

    class Meta:
        db_table = 'partner_session_attendance'
        constraints = [
            models.UniqueConstraint(fields=['session', 'membership'], name='unique_attendance_mark'),
        ]

    def __str__(self):
        return f'{self.membership.participant} {self.get_status_display()} at {self.session}'

    @property
    def attended(self):
        return self.status in self.ATTENDED


class CourseResult(TimeStampedModel):
    """A participant's outcome for one course. Certificates will hang off this later."""

    class Status(models.TextChoices):
        IN_PROGRESS = 'in_progress', 'In progress'
        PASSED = 'passed', 'Passed'
        FAILED = 'failed', 'Failed'
        INCOMPLETE = 'incomplete', 'Incomplete (attendance)'

    membership = models.ForeignKey(CohortMembership, on_delete=models.CASCADE, related_name='results')
    course = models.ForeignKey(ProgrammeCourse, on_delete=models.PROTECT, related_name='results')
    exam_score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS, db_index=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    recorded_at = models.DateTimeField(default=timezone.now)
    received_at = models.DateTimeField(default=timezone.now)
    signed_off_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    signed_off_at = models.DateTimeField(null=True, blank=True)
    # I'm bumping this on every change so offline edits made against an old copy are caught as conflicts
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = 'partner_course_results'
        ordering = ['course__sequence']
        constraints = [
            models.UniqueConstraint(fields=['membership', 'course'], name='unique_result_per_course'),
        ]

    def __str__(self):
        return f'{self.membership.participant}: {self.course.code} {self.get_status_display()}'

    @property
    def is_signed_off(self):
        return self.signed_off_at is not None
