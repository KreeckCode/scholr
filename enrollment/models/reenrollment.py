"""
Re-enrollment model for returning learners.

When EnrollmentConfig.require_parent_reenrollment_confirmation is True,
promoted learners have a Reenrollment record created automatically.
The parent completes this via the portal to confirm continuation,
update contact details, and sign the updated fee agreement.

When require_parent_reenrollment_confirmation is False, this model is
not used — promotion directly creates LearnerEnrollment.
"""
from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


class Reenrollment(models.Model):
    """
    Parent-facing re-enrollment confirmation for a returning learner.
    Created by the system after grade promotion when confirmation is required.
    Completed by the parent via the portal before the learner's new-year
    enrollment is finalised.
    """

    STATUS_PENDING   = 'pending'
    STATUS_COMPLETED = 'completed'
    STATUS_DECLINED  = 'declined'
    STATUS_EXPIRED   = 'expired'

    STATUS_CHOICES = [
        (STATUS_PENDING,   'Awaiting Parent Confirmation'),
        (STATUS_COMPLETED, 'Confirmed'),
        (STATUS_DECLINED,  'Declined — Learner Leaving'),
        (STATUS_EXPIRED,   'Expired — No Response'),
    ]

    # ---- Core ----
    learner = models.ForeignKey(
        'academics.Learner',
        on_delete=models.CASCADE,
        related_name='reenrollments',
    )
    promotion = models.OneToOneField(
        'enrollment.LearnerPromotion',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='reenrollment',
    )
    enrollment_year = models.PositiveIntegerField()
    to_academic_year = models.ForeignKey(
        'academics.AcademicYear',
        on_delete=models.PROTECT,
        related_name='reenrollments',
    )
    to_grade = models.ForeignKey(
        'academics.Grade',
        on_delete=models.PROTECT,
        related_name='reenrollments',
    )

    # ---- Confirmation steps ----
    # Each boolean flips True when the parent completes that step.
    personal_details_confirmed = models.BooleanField(default=False)
    address_confirmed          = models.BooleanField(default=False)
    guardian_details_confirmed = models.BooleanField(default=False)

    # Subject selection (for grade transitions requiring elective choices)
    subject_selection_required  = models.BooleanField(default=False)
    subject_selection_completed = models.BooleanField(default=False)
    # ManyToMany to SubjectOffering — populated when subject selection is completed
    selected_subject_offerings = models.ManyToManyField(
        'academics.SubjectOffering',
        blank=True,
        related_name='reenrollments',
    )

    # ---- Fee agreement ----
    fee_agreement_required   = models.BooleanField(default=True)
    fee_agreement_signed     = models.BooleanField(default=False)
    fee_agreement_signed_at  = models.DateTimeField(null=True, blank=True)
    fee_agreement_ip         = models.GenericIPAddressField(null=True, blank=True)
    fee_agreement_version    = models.CharField(max_length=20, blank=True)

    # ---- Optional deposit ----
    enrollment_deposit_required = models.BooleanField(default=False)
    enrollment_deposit_amount   = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
    )
    fee_order_id = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='PK of the associated accounts.Order for the enrollment deposit.',
    )

    # ---- Status ----
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    expiry_date = models.DateField(
        null=True, blank=True,
        help_text='Date by which the parent must confirm. After this, status → expired.',
    )

    # ---- Result ----
    learner_enrollment = models.OneToOneField(
        'academics.LearnerEnrollment',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='source_reenrollment',
    )

    # ---- Reason if declined ----
    decline_reason = models.TextField(blank=True)

    completed_at = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('learner', 'enrollment_year')]
        ordering = ['-created_at']
        verbose_name = 'Re-enrollment'
        verbose_name_plural = 'Re-enrollments'

    def __str__(self):
        return (
            f'{self.learner.get_full_name()} — '
            f'Re-enrollment {self.enrollment_year} ({self.get_status_display()})'
        )

    @property
    def all_steps_complete(self) -> bool:
        """Return True when all required steps have been completed."""
        base = (
            self.personal_details_confirmed
            and self.guardian_details_confirmed
            and (self.fee_agreement_signed if self.fee_agreement_required else True)
            and (self.subject_selection_completed if self.subject_selection_required else True)
        )
        return base
    
    def is_expired(self):
        """Check if the reenrollment deadline has passed."""
        from django.utils import timezone
        return timezone.now().date() > self.expiry_date