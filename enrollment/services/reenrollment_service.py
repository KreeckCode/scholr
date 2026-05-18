"""
ReenrollmentService — handles parent re-enrollment workflow.

Manages the multi-step process of confirming learner details,
selecting subjects, and signing fee agreements.
"""
import hashlib
import logging

from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from django.db import transaction
from django.utils import timezone

from ..models.application import ConsentRecord
from ..models.reenrollment import Reenrollment

logger = logging.getLogger(__name__)


class ReenrollmentService:
    """
    Handles completion of re-enrollment process.
    
    Tracks progress through steps:
    1. Details confirmation
    2. Subject selection (if required)
    3. Fee agreement consent
    4. Payment (if required)
    """

    TOKEN_MAX_AGE = 60 * 60 * 24 * 30  # 30 days in seconds

    def generate_access_token(self, reenrollment: Reenrollment) -> str:
        """
        Generate a signed token for magic link authentication.
        Token contains reenrollment PK and is valid for 30 days.
        """
        signer = TimestampSigner()
        value = f'{reenrollment.pk}'
        return signer.sign(value)

    def verify_access_token(self, token: str) -> Reenrollment:
        """
        Verify token and return the Reenrollment object.
        Raises BadSignature if invalid, SignatureExpired if expired.
        """
        signer = TimestampSigner()
        
        try:
            value = signer.unsign(token, max_age=self.TOKEN_MAX_AGE)
            reenrollment_pk = int(value)
            return Reenrollment.objects.select_related(
                'learner',
                'to_grade',
                'to_academic_year',
                'promotion__learner',
            ).get(pk=reenrollment_pk)
        except (BadSignature, SignatureExpired, ValueError, Reenrollment.DoesNotExist) as exc:
            logger.warning(f'Invalid reenrollment token: {exc}')
            raise

    def confirm_details(
        self,
        reenrollment: Reenrollment,
        learner_data: dict,
        guardian_data: dict = None,
    ):
        """
        Step 1: Confirm learner details.
        Updates learner record with any changes made by parent.
        """
        with transaction.atomic():
            learner = reenrollment.learner

            # Update allowed fields
            for field in [
                'home_language',
                'allergies',
                'chronic_conditions',
                'current_medication',
            ]:
                if field in learner_data:
                    setattr(learner, field, learner_data[field])

            learner.save()

            # Update guardian contact if provided
            if guardian_data:
                from academics.models.learner import Guardian, LearnerGuardian
                
                # Get primary guardian
                guardian_rel = LearnerGuardian.objects.filter(
                    learner=learner,
                    is_primary_contact=True,  # Note: field is is_primary_contact, not is_primary
                ).select_related('guardian').first()

                if guardian_rel:
                    guardian = guardian_rel.guardian
                    guardian.email = guardian_data.get('email', guardian.email)
                    guardian.phone_primary = guardian_data.get('cell_phone', guardian.phone_primary)
                    guardian.phone_secondary = guardian_data.get('work_phone', guardian.phone_secondary)
                    guardian.street_address = guardian_data.get('physical_address', guardian.street_address)
                    guardian.save()

            # Mark step complete
            reenrollment.details_confirmed = True
            reenrollment.details_confirmed_at = timezone.now()
            reenrollment.save(update_fields=[
                'details_confirmed',
                'details_confirmed_at',
            ])

        logger.info(
            f'Details confirmed for reenrollment {reenrollment.pk} '
            f'(learner {learner.admission_number})'
        )












    def save_subject_selection(
        self,
        reenrollment: Reenrollment,
        selected_offerings: list,
    ):
        """
        Step 2: Save subject selection.
        Creates SubjectEnrollment records for the new academic year.
        """
        from academics.models.learner import SubjectEnrollment

        with transaction.atomic():
            # Delete any existing subject enrollments for this learner/year
            # (in case they're re-doing subject selection)
            SubjectEnrollment.objects.filter(
                learner_enrollment=reenrollment.learner_enrollment,
            ).delete()

            # Create new subject enrollments
            for offering in selected_offerings:
                SubjectEnrollment.objects.create(
                    learner_enrollment=reenrollment.learner_enrollment,
                    subject_offering=offering,
                    enrollment_date=reenrollment.learner_enrollment.enrollment_date,
                )

            # Mark step complete
            reenrollment.subjects_selected = True
            reenrollment.subjects_selected_at = timezone.now()
            reenrollment.save(update_fields=[
                'subjects_selected',
                'subjects_selected_at',
            ])

        logger.info(
            f'Subjects selected for reenrollment {reenrollment.pk}: '
            f'{len(selected_offerings)} subjects'
        )

    def sign_fee_agreement(
        self,
        reenrollment: Reenrollment,
        signature_name: str,
        ip_address: str,
        user_agent: str,
    ):
        """
        Step 3: Record fee agreement consent.
        Creates ECTA-compliant ConsentRecord.
        """
        # Generate hash of fee agreement document
        # In production, this would hash the actual PDF or agreement text
        fee_agreement_text = self._get_fee_agreement_text(reenrollment)
        document_hash = hashlib.sha256(fee_agreement_text.encode()).hexdigest()

        with transaction.atomic():
            consent = ConsentRecord.objects.create(
                consent_type=ConsentRecord.TYPE_FEE_AGREEMENT,
                consented_by_name=signature_name,
                consented_by_email=self._get_guardian_email(reenrollment),
                ip_address=ip_address,
                user_agent=user_agent,
                document_hash=document_hash,
                document_url=f'/enrollment/fee-schedule/{reenrollment.enrollment_year}/',
                reenrollment=reenrollment,
            )

            reenrollment.fee_agreement_signed = True
            reenrollment.fee_agreement_signed_at = timezone.now()
            reenrollment.consent_record = consent
            reenrollment.save(update_fields=[
                'fee_agreement_signed',
                'fee_agreement_signed_at',
                'consent_record',
            ])

        logger.info(
            f'Fee agreement signed for reenrollment {reenrollment.pk} '
            f'by {signature_name}'
        )

    def complete_reenrollment(self, reenrollment: Reenrollment):
        """
        Final step: Mark reenrollment as confirmed.
        Triggered after all required steps are complete.
        """
        with transaction.atomic():
            reenrollment.status = Reenrollment.STATUS_CONFIRMED
            reenrollment.confirmed_at = timezone.now()
            reenrollment.save(update_fields=['status', 'confirmed_at'])

        logger.info(
            f'Reenrollment completed: {reenrollment.pk} '
            f'(learner {reenrollment.learner.admission_number})'
        )

        # Send confirmation email
        from ..tasks import send_reenrollment_confirmation_email_task
        send_reenrollment_confirmation_email_task.delay(reenrollment.pk)

    def check_completion_status(self, reenrollment: Reenrollment) -> dict:
        """
        Check which steps are complete and what's next.
        Returns a dict with completion status and next step.
        """
        steps = {
            'details_confirmed': reenrollment.details_confirmed,
            'subjects_selected': (
                reenrollment.subjects_selected
                if reenrollment.subject_selection_required
                else True  # Skip if not required
            ),
            'fee_agreement_signed': reenrollment.fee_agreement_signed,
            'payment_complete': (
                reenrollment.payment_complete
                if reenrollment.deposit_required
                else True  # Skip if not required
            ),
        }

        all_complete = all(steps.values())

        if all_complete and reenrollment.status != Reenrollment.STATUS_CONFIRMED:
            self.complete_reenrollment(reenrollment)

        # Determine next step
        if not steps['details_confirmed']:
            next_step = 'confirm_details'
        elif not steps['subjects_selected']:
            next_step = 'select_subjects'
        elif not steps['fee_agreement_signed']:
            next_step = 'sign_agreement'
        elif not steps['payment_complete']:
            next_step = 'payment'
        else:
            next_step = 'complete'

        return {
            'steps': steps,
            'all_complete': all_complete,
            'next_step': next_step,
            'completion_percentage': sum(steps.values()) / len(steps) * 100,
        }

    # ---- Private helpers ----

    def _get_fee_agreement_text(self, reenrollment: Reenrollment) -> str:
        """
        Get the fee agreement text for hashing.
        In production, this would fetch the actual PDF or agreement document.
        """
        from ..models.application import EnrollmentConfig
        
        config = EnrollmentConfig.get()
        
        return (
            f'Fee Agreement for {reenrollment.enrollment_year}\n'
            f'Learner: {reenrollment.learner.get_full_name()}\n'
            f'Grade: {reenrollment.to_grade.name}\n'
            f'Date: {timezone.now().date()}\n'
            f'School: {config.school_name if hasattr(config, "school_name") else "Scholr School"}\n'
        )

    def _get_guardian_email(self, reenrollment: Reenrollment) -> str:
        """Get primary guardian email for consent record."""
        from academics.models.learner import LearnerGuardian

        guardian_rel = LearnerGuardian.objects.filter(
            learner=reenrollment.learner,
            is_primary=True,
        ).select_related('guardian').first()

        if guardian_rel and guardian_rel.guardian.email:
            return guardian_rel.guardian.email

        return ''