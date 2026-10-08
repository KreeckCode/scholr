from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from .base import PublicIdModel, TimeStampedModel, private_storage
from .organisation import PartnerOrganisation

AGREEMENT_MAX_BYTES = 10 * 1024 * 1024


def default_currency():
    return settings.PARTNERS_DEFAULT_CURRENCY


def agreement_upload_to(instance, filename):
    # I'm discarding the uploaded name entirely; it is user input and may carry path tricks
    return f'licences/{instance.public_id}/agreement.pdf'


def validate_agreement_size(file):
    if file.size > AGREEMENT_MAX_BYTES:
        raise ValidationError('The agreement must be 10 MB or smaller.')


class Licence(PublicIdModel, TimeStampedModel):
    """A partner's agreement to run METP, with its dates and fee status."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        ACTIVE = 'active', 'Active'
        SUSPENDED = 'suspended', 'Suspended'
        EXPIRED = 'expired', 'Expired'
        TERMINATED = 'terminated', 'Terminated'

    class FeeStatus(models.TextChoices):
        NOT_INVOICED = 'not_invoiced', 'Not invoiced'
        INVOICED = 'invoiced', 'Invoiced'
        PAID = 'paid', 'Paid'
        WAIVED = 'waived', 'Waived'

    organisation = models.ForeignKey(PartnerOrganisation, on_delete=models.PROTECT, related_name='licences')
    number = models.CharField(max_length=40, unique=True, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    starts_on = models.DateField()
    ends_on = models.DateField(db_index=True)
    signed_on = models.DateField(null=True, blank=True)
    agreement_file = models.FileField(
        upload_to=agreement_upload_to,
        storage=private_storage,
        blank=True,
        validators=[FileExtensionValidator(['pdf']), validate_agreement_size],
    )
    fee_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'), validators=[MinValueValidator(0)],
    )
    fee_currency = models.CharField(max_length=3, default=default_currency)
    fee_status = models.CharField(max_length=20, choices=FeeStatus.choices, default=FeeStatus.NOT_INVOICED)
    max_locations = models.PositiveIntegerField(
        null=True, blank=True, help_text='Leave blank for no limit.',
    )
    status_reason = models.TextField(blank=True)
    status_changed_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'partner_licences'
        ordering = ['-starts_on', '-id']
        constraints = [
            models.CheckConstraint(condition=Q(ends_on__gt=F('starts_on')), name='licence_ends_after_start'),
            models.UniqueConstraint(
                fields=['organisation'], condition=Q(status='active'),
                name='one_active_licence_per_organisation',
            ),
        ]
        indexes = [
            models.Index(fields=['status', 'ends_on'], name='idx_licence_status_ends'),
        ]

    def __str__(self):
        return f'{self.number} ({self.get_status_display()})'

    def clean(self):
        super().clean()
        if self.starts_on and self.ends_on and self.ends_on <= self.starts_on:
            raise ValidationError({'ends_on': 'The end date must be after the start date.'})

    def is_current(self, today=None):
        today = today or timezone.localdate()
        return self.starts_on <= today <= self.ends_on

    def days_remaining(self, today=None):
        today = today or timezone.localdate()
        return (self.ends_on - today).days
