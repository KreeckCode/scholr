from django.conf import settings
from django.db import models

from .location import TrainingLocation


class SyncOperation(models.Model):
    """
    One operation sent by an offline device, stored once by its op_id.
    A resend of the same op_id returns the stored response, so retries never double-apply.
    """

    class Result(models.TextChoices):
        APPLIED = 'applied', 'Applied'
        SUPERSEDED = 'superseded', 'Superseded by a newer change'
        CONFLICT = 'conflict', 'Conflict'
        REJECTED = 'rejected', 'Rejected'

    class Kind(models.TextChoices):
        ATTENDANCE_SET = 'attendance.set', 'Attendance'
        RESULT_SET = 'result.set', 'Course result'
        PARTICIPANT_CREATE = 'participant.create', 'New participant'

    op_id = models.UUIDField(unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    location = models.ForeignKey(
        TrainingLocation, on_delete=models.SET_NULL, null=True, blank=True, related_name='sync_operations',
    )
    device_id = models.CharField(max_length=64, blank=True)
    kind = models.CharField(max_length=40, choices=Kind.choices)
    payload = models.JSONField(default=dict)
    result = models.CharField(max_length=20, choices=Result.choices, db_index=True)
    detail = models.JSONField(default=dict, blank=True)
    response = models.JSONField(default=dict, blank=True)
    recorded_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'partner_sync_operations'
        ordering = ['-received_at']
        indexes = [
            models.Index(fields=['location', 'result', 'resolved_at'], name='idx_syncop_conflicts'),
        ]

    def __str__(self):
        return f'{self.kind} {self.op_id} ({self.result})'

    @property
    def is_open_conflict(self):
        return self.result == self.Result.CONFLICT and self.resolved_at is None
