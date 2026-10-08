from django.conf import settings
from django.db import models
from django.db.models import Q

from .base import CountryField, PublicIdModel, TimeStampedModel, country_name, phone_validator


class PartnerOrganisation(PublicIdModel, TimeStampedModel):
    """A church, denomination, ministry organisation or college that partners with METP."""

    class Kind(models.TextChoices):
        CHURCH = 'church', 'Church'
        DENOMINATION = 'denomination', 'Denomination'
        ORGANISATION = 'organisation', 'Ministry organisation'
        COLLEGE = 'college', 'Bible college or seminary'

    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.CHURCH)
    country = CountryField(db_index=True)
    region = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    website = models.URLField(blank=True)
    is_sensitive = models.BooleanField(
        default=False,
        help_text='Hide names from exports and offline packs, for partners in places where '
                  'Christian leaders face danger.',
    )
    is_archived = models.BooleanField(default=False, db_index=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'partner_organisations'
        ordering = ['name']
        verbose_name = 'Partner organisation'
        indexes = [
            models.Index(fields=['is_archived', 'name'], name='idx_partner_org_active_name'),
        ]

    def __str__(self):
        return self.name

    @property
    def country_name(self):
        return country_name(self.country)

    @property
    def primary_contact(self):
        # I'm reading from the prefetched list when it exists, so detail pages stay at one query
        for contact in self.contacts.all():
            if contact.is_primary:
                return contact
        return None


class PartnerContact(TimeStampedModel):
    """A named person at a partner organisation."""

    organisation = models.ForeignKey(PartnerOrganisation, on_delete=models.CASCADE, related_name='contacts')
    name = models.CharField(max_length=150)
    role_title = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    is_primary = models.BooleanField(default=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='partner_contacts',
    )

    class Meta:
        db_table = 'partner_contacts'
        ordering = ['-is_primary', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['organisation'], condition=Q(is_primary=True),
                name='one_primary_contact_per_organisation',
            ),
        ]
        indexes = [models.Index(fields=['email'], name='idx_partner_contact_email')]

    def __str__(self):
        return f'{self.name} ({self.organisation})'
