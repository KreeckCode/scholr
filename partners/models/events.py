from django.conf import settings
from django.db import models
from django.db.models import F, Q

from .base import PublicIdModel, TimeStampedModel
from .cohort import Cohort
from .location import TrainingLocation


class LocationEvent(PublicIdModel, TimeStampedModel):
    """A dated occasion at a training location that METP staff may travel to."""

    class Kind(models.TextChoices):
        GRADUATION = 'graduation', 'Graduation'
        VISIT = 'visit', 'Staff visit'
        TRAINING = 'training', 'Training (METP 100a or 100b)'
        OTHER = 'other', 'Other'

    class Status(models.TextChoices):
        PLANNED = 'planned', 'Planned'
        CONFIRMED = 'confirmed', 'Confirmed'
        HELD = 'held', 'Held'
        CANCELLED = 'cancelled', 'Cancelled'

    location = models.ForeignKey(TrainingLocation, on_delete=models.CASCADE, related_name='events')
    cohort = models.ForeignKey(
        Cohort, on_delete=models.SET_NULL, null=True, blank=True, related_name='events',
        help_text='The cohort graduating or being visited, if there is one.',
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.GRADUATION, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED, db_index=True)
    title = models.CharField(max_length=150)
    starts_on = models.DateField(db_index=True)
    ends_on = models.DateField(null=True, blank=True)
    starts_at = models.TimeField(null=True, blank=True, help_text='Start time on the first day, local time.')
    venue = models.CharField(
        max_length=255, blank=True, help_text='Only if it is not at the training location itself.',
    )
    notes = models.TextField(blank=True, max_length=3000)
    attendees = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name='partner_events',
        help_text='METP staff travelling to this event.',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'partner_location_events'
        ordering = ['starts_on', 'starts_at']
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_on__isnull=True) | Q(ends_on__gte=F('starts_on')),
                name='event_ends_on_or_after_start',
            ),
        ]
        indexes = [
            models.Index(fields=['location', 'starts_on'], name='idx_event_location_start'),
        ]

    def __str__(self):
        return f'{self.title} ({self.starts_on:%d %b %Y})'

    @property
    def is_active(self):
        return self.status in (self.Status.PLANNED, self.Status.CONFIRMED)


class LocationAccessLog(models.Model):
    """Who opened the exact position or contacts of a location marked sensitive, and when."""

    class Kind(models.TextChoices):
        DETAILS = 'details', 'Location details'

    location = models.ForeignKey(TrainingLocation, on_delete=models.CASCADE, related_name='access_logs')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    kind = models.CharField(max_length=20, choices=Kind.choices)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    viewed_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'partner_location_access_logs'
        ordering = ['-viewed_at']

    def __str__(self):
        return f'{self.user} viewed {self.location} ({self.get_kind_display()})'
