"""
Bursary and financial aid models.

These models are only meaningful when SCHOLR_BURSARY_ENABLED=True.
The views and forms gate on this flag. The models always exist in the
database schema — the flag controls access, not schema presence.

This means:
  - Migrations run regardless of the flag (correct — schema is always current)
  - Views, forms, and templates check bursary_enabled() before rendering
  - Existing data is preserved when the flag is toggled

Most private schools will leave SCHOLR_BURSARY_ENABLED=False.
"""
import os
import uuid

from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


def _bursary_document_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    unique = uuid.uuid4().hex[:10]
    return (
        f"enrollment_private/"
        f"{instance.bursary_application.application.reference_number}/"
        f"bursary_{instance.document_type}_{unique}{ext}"
    )


class BursaryApplication(models.Model):
    """
    Financial aid application linked one-to-one to an Application.

    The parent starts a bursary application from within the enrollment form
    when they indicate they are applying for financial assistance.
    The bursary review workflow runs independently of the main enrollment review.

    A bursary percentage of 100 means full fee waiver.
    A bursary percentage of 50 means 50% reduction on school fees.
    """

    STATUS_PENDING      = 'pending'
    STATUS_UNDER_REVIEW = 'under_review'
    STATUS_APPROVED     = 'approved'
    STATUS_PARTIAL      = 'partial'
    STATUS_REJECTED     = 'rejected'

    STATUS_CHOICES = [
        (STATUS_PENDING,      'Pending Review'),
        (STATUS_UNDER_REVIEW, 'Under Review'),
        (STATUS_APPROVED,     'Approved — Full Bursary'),
        (STATUS_PARTIAL,      'Approved — Partial Bursary'),
        (STATUS_REJECTED,     'Rejected'),
    ]

    EMPLOYMENT_CHOICES = [
        ('employed_full',   'Employed Full-Time'),
        ('employed_part',   'Employed Part-Time'),
        ('self_employed',   'Self-Employed'),
        ('unemployed',      'Unemployed'),
        ('sassa',           'SASSA Grant Recipient'),
        ('retired',         'Retired'),
        ('other',           'Other'),
    ]

    INCOME_FREQUENCY_CHOICES = [
        ('monthly', 'Monthly'),
        ('annual',  'Annual'),
    ]

    # ---- Link to enrollment application ----
    application = models.OneToOneField(
        'enrollment.Application',
        on_delete=models.CASCADE,
        related_name='bursary_application',
    )

    # ---- Financial information ----
    household_gross_income = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text='Combined gross income of all guardians.',
    )
    income_frequency = models.CharField(
        max_length=10,
        choices=INCOME_FREQUENCY_CHOICES,
        default='monthly',
    )
    number_of_dependants = models.PositiveSmallIntegerField(
        default=1,
        help_text='Number of people financially dependent on the guardian(s).',
    )
    number_of_children_in_school = models.PositiveSmallIntegerField(
        default=1,
        help_text='Total children currently in any school (including this applicant).',
    )

    # ---- Employment ----
    guardian_1_employment = models.CharField(
        max_length=20, choices=EMPLOYMENT_CHOICES,
    )
    guardian_1_employer = models.CharField(max_length=150, blank=True)

    guardian_2_employment = models.CharField(
        max_length=20, choices=EMPLOYMENT_CHOICES, blank=True,
    )
    guardian_2_employer = models.CharField(max_length=150, blank=True)

    # ---- SASSA / grants ----
    receives_sassa   = models.BooleanField(default=False)
    sassa_grant_type = models.CharField(max_length=60, blank=True)
    sassa_amount     = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
    )

    # ---- Property ----
    owns_home   = models.BooleanField(default=False)
    owns_vehicle = models.BooleanField(default=False)

    # ---- Reason ----
    reason_for_application = models.TextField(
        help_text='Free text — why does this family need financial assistance?',
    )

    # ---- Status and decision ----
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING,
    )

    # Percentage of school fees covered by the bursary (0–100)
    bursary_percentage = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text='Percentage of annual school fees covered. 100 = full bursary.',
    )
    # Rand value of bursary if the school prefers fixed amounts
    bursary_amount_annual = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True,
        help_text='Fixed annual bursary amount. Used instead of percentage if set.',
    )

    decision_notes = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='bursary_decisions',
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = 'Bursary Application'
        verbose_name_plural = 'Bursary Applications'

    def __str__(self):
        return (
            f'Bursary — {self.application.get_full_name()} '
            f'({self.get_status_display()})'
        )

    @property
    def monthly_household_income(self):
        """Return monthly income regardless of how it was entered."""
        if self.income_frequency == 'annual':
            return self.household_gross_income / 12
        return self.household_gross_income


class BursaryDocument(models.Model):
    """
    Supporting financial documents for a bursary application.
    Stored in the same private media path as ApplicationDocuments.
    """

    DOCUMENT_TYPES = [
        ('payslip_1',       'Latest Payslip — Guardian 1'),
        ('payslip_2',       'Latest Payslip — Guardian 2'),
        ('bank_statement',  '3 Months Bank Statements'),
        ('sassa_letter',    'SASSA Confirmation Letter'),
        ('affidavit',       'Affidavit of Financial Status'),
        ('tax_certificate', 'IRP5 / Tax Certificate'),
        ('other',           'Other Supporting Document'),
    ]

    STATUS_PENDING   = 'pending'
    STATUS_APPROVED  = 'approved'
    STATUS_REJECTED  = 'rejected'

    STATUS_CHOICES = [
        (STATUS_PENDING,  'Pending Review'),
        (STATUS_APPROVED, 'Accepted'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    bursary_application = models.ForeignKey(
        BursaryApplication,
        on_delete=models.CASCADE,
        related_name='documents',
    )
    document_type = models.CharField(max_length=30, choices=DOCUMENT_TYPES)
    upload        = models.FileField(upload_to=_bursary_document_upload_path)
    original_filename = models.CharField(max_length=255)
    file_size_bytes   = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING,
    )

    uploaded_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['bursary_application', 'document_type']
        verbose_name = 'Bursary Document'
        verbose_name_plural = 'Bursary Documents'

    def __str__(self):
        return (
            f'{self.bursary_application} — '
            f'{self.get_document_type_display()}'
        )