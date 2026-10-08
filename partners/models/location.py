from django.conf import settings
from django.db import models

from .base import CountryField, PublicIdModel, TimeStampedModel, country_name
from .licence import Licence
from .organisation import PartnerOrganisation


class TrainingLocation(PublicIdModel, TimeStampedModel):
    """A licensed site where METP is taught in person."""

    class Status(models.TextChoices):
        PREPARING = 'preparing', 'Preparing'
        ACTIVE = 'active', 'Active'
        PAUSED = 'paused', 'Paused'
        CLOSED = 'closed', 'Closed'

    organisation = models.ForeignKey(PartnerOrganisation, on_delete=models.PROTECT, related_name='locations')
    licence = models.ForeignKey(Licence, on_delete=models.PROTECT, related_name='locations')
    name = models.CharField(max_length=150)
    country = CountryField(db_index=True)
    region = models.CharField(max_length=100, blank=True, db_index=True)
    city = models.CharField(max_length=100, blank=True)
    language = models.CharField(max_length=50, default='English')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PREPARING, db_index=True)
    is_sensitive = models.BooleanField(
        default=False,
        help_text='Hide participant names from exports and use initials in offline packs.',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'training_locations'
        ordering = ['country', 'name']
        constraints = [
            models.UniqueConstraint(fields=['organisation', 'name'], name='unique_location_name_per_partner'),
        ]

    def __str__(self):
        return self.name

    @property
    def country_name(self):
        return country_name(self.country)

    @property
    def hides_identities(self):
        return self.is_sensitive or self.organisation.is_sensitive


class LocationMembership(TimeStampedModel):
    """Who works at a location, and in which capacity."""

    class Role(models.TextChoices):
        COORDINATOR = 'coordinator', 'Coordinator (METP 100a)'
        FACILITATOR = 'facilitator', 'Facilitator (METP 100b)'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='location_memberships')
    location = models.ForeignKey(TrainingLocation, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=20, choices=Role.choices)
    trained_on = models.DateField(null=True, blank=True, help_text='Date METP 100a or 100b was completed.')
    is_active = models.BooleanField(default=True, db_index=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'location_memberships'
        ordering = ['role', 'user__first_name']
        constraints = [
            models.UniqueConstraint(fields=['user', 'location', 'role'], name='unique_location_membership'),
        ]
        indexes = [
            models.Index(fields=['user', 'is_active'], name='idx_loc_member_user_active'),
        ]

    def __str__(self):
        return f'{self.user} at {self.location} ({self.get_role_display()})'
