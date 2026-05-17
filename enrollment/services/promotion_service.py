"""
PromotionService — handles grade promotion and learner migration.

At the end of each academic year, staff use this service to:
- Promote learners to the next grade
- Retain learners in the same grade (repeating)
- Mark learners as graduated or withdrawn

The service creates LearnerEnrollment records for the new academic year
and optionally creates Reenrollment records if parent confirmation is required.
"""
import logging

from django.db import transaction
from django.utils import timezone

from ..models.promotion import BulkPromotionBatch, LearnerPromotion
from ..models.reenrollment import Reenrollment

logger = logging.getLogger(__name__)


class PromotionService:
    """
    Handles bulk and individual learner promotions.
    
    Every promotion creates a LearnerPromotion record first (decision),
    then executes it to create the actual LearnerEnrollment (outcome).
    """

    def create_bulk_batch(
        self,
        label: str,
        from_academic_year,
        to_academic_year,
        promotion_decisions: list,
        created_by,
        notes: str = '',
    ) -> BulkPromotionBatch:
        """
        Create a bulk promotion batch with individual promotion decisions.
        
        promotion_decisions is a list of dicts:
        [
            {
                'learner': Learner instance,
                'status': 'promoted',
                'to_grade': Grade instance,
                'to_stream': GradeStream instance (optional),
                'notes': 'string',
            },
            ...
        ]
        
        Returns the BulkPromotionBatch (not yet executed).
        """
        with transaction.atomic():
            batch = BulkPromotionBatch.objects.create(
                label=label,
                from_academic_year=from_academic_year,
                to_academic_year=to_academic_year,
                created_by=created_by,
                notes=notes,
                total_count=len(promotion_decisions),
            )

            for decision in promotion_decisions:
                learner = decision['learner']
                
                # Get learner's current enrollment
                current_enrollment = learner.enrollments.filter(
                    academic_year=from_academic_year,
                    is_active=True,
                ).select_related('grade', 'stream').first()

                if not current_enrollment:
                    logger.warning(
                        f'Learner {learner.admission_number} has no active enrollment '
                        f'in {from_academic_year} — skipping in batch {batch.pk}'
                    )
                    continue

                # Check if subject selection is needed
                requires_subject_selection = self._requires_subject_selection(
                    from_grade=current_enrollment.grade,
                    to_grade=decision.get('to_grade'),
                )

                LearnerPromotion.objects.create(
                    learner=learner,
                    batch=batch,
                    from_academic_year=from_academic_year,
                    from_grade=current_enrollment.grade,
                    from_stream=current_enrollment.stream,
                    to_academic_year=to_academic_year,
                    to_grade=decision.get('to_grade'),
                    to_stream=decision.get('to_stream'),
                    status=decision['status'],
                    decided_by=created_by,
                    notes=decision.get('notes', ''),
                    requires_subject_selection=requires_subject_selection,
                )

        logger.info(
            f'Bulk promotion batch created: {batch.label} '
            f'({batch.total_count} learners) by {created_by}'
        )
        return batch

    def execute_batch(self, batch: BulkPromotionBatch, executed_by) -> dict:
        """
        Execute all promotions in a batch.
        Creates LearnerEnrollments for the new academic year.
        
        Returns a dict with success_count and failure_count.
        """
        promotions = batch.promotions.filter(status=LearnerPromotion.STATUS_PENDING)
        
        success_count = 0
        failure_count = 0

        for promotion in promotions:
            try:
                self.execute_promotion(promotion)
                success_count += 1
            except Exception as exc:
                failure_count += 1
                promotion.status = LearnerPromotion.STATUS_FAILED
                promotion.execution_error = str(exc)
                promotion.save(update_fields=['status', 'execution_error'])
                logger.error(
                    f'Promotion execution failed for {promotion.learner.admission_number}: {exc}'
                )

        # Update batch stats
        batch.executed = True
        batch.executed_at = timezone.now()
        batch.success_count = success_count
        batch.failure_count = failure_count
        batch.save(update_fields=['executed', 'executed_at', 'success_count', 'failure_count'])

        logger.info(
            f'Batch {batch.pk} executed: {success_count} success, {failure_count} failed'
        )

        return {
            'success_count': success_count,
            'failure_count': failure_count,
        }

    def execute_promotion(self, promotion: LearnerPromotion):
        """
        Execute a single promotion — creates the LearnerEnrollment for the new year.
        
        If requires_subject_selection is True, creates a Reenrollment record
        for parent to complete subject selection.
        
        If EnrollmentConfig.require_parent_reenrollment_confirmation is True,
        creates a Reenrollment record even if subject selection is not needed.
        """
        from academics.models.learner import LearnerEnrollment
        from ..models.application import EnrollmentConfig

        config = EnrollmentConfig.get()

        with transaction.atomic():
            # Handle different statuses
            if promotion.status == LearnerPromotion.STATUS_PROMOTED:
                # Create the new enrollment
                new_enrollment = LearnerEnrollment.objects.create(
                    learner=promotion.learner,
                    academic_year=promotion.to_academic_year,
                    grade=promotion.to_grade,
                    stream=promotion.to_stream,
                    is_active=True,
                    enrollment_date=promotion.to_academic_year.start_date,
                )

                promotion.new_learner_enrollment = new_enrollment
                promotion.status = LearnerPromotion.STATUS_PROMOTED
                promotion.executed_at = timezone.now()
                promotion.save(update_fields=[
                    'new_learner_enrollment',
                    'status',
                    'executed_at',
                ])

                # Copy subject enrollments if applicable
                self._copy_subject_enrollments(promotion, new_enrollment)

                # Create reenrollment if needed
                if config.require_parent_reenrollment_confirmation or promotion.requires_subject_selection:
                    self._create_reenrollment(promotion, new_enrollment)
                else:
                    # Send promotion notification email
                    self._send_promotion_notification(promotion)

            elif promotion.status == LearnerPromotion.STATUS_RETAINED:
                # Learner repeats the same grade
                new_enrollment = LearnerEnrollment.objects.create(
                    learner=promotion.learner,
                    academic_year=promotion.to_academic_year,
                    grade=promotion.from_grade,  # Same grade
                    stream=promotion.to_stream or promotion.from_stream,
                    is_active=True,
                    enrollment_date=promotion.to_academic_year.start_date,
                )

                promotion.new_learner_enrollment = new_enrollment
                promotion.status = LearnerPromotion.STATUS_RETAINED
                promotion.executed_at = timezone.now()
                promotion.save(update_fields=[
                    'new_learner_enrollment',
                    'status',
                    'executed_at',
                ])

                # Copy subjects
                self._copy_subject_enrollments(promotion, new_enrollment)
                self._send_promotion_notification(promotion)

            elif promotion.status == LearnerPromotion.STATUS_GRADUATED:
                # No new enrollment needed — learner is leaving
                promotion.status = LearnerPromotion.STATUS_GRADUATED
                promotion.executed_at = timezone.now()
                promotion.save(update_fields=['status', 'executed_at'])
                self._send_promotion_notification(promotion)

            elif promotion.status == LearnerPromotion.STATUS_WITHDRAWN:
                # Mark as withdrawn — no new enrollment
                promotion.status = LearnerPromotion.STATUS_WITHDRAWN
                promotion.executed_at = timezone.now()
                promotion.save(update_fields=['status', 'executed_at'])

        logger.info(
            f'Promotion executed: {promotion.learner.admission_number} '
            f'({promotion.get_status_display()})'
        )

    # ---- Private helpers ----

    def _requires_subject_selection(self, from_grade, to_grade) -> bool:
        """
        Check if the grade transition requires subject selection.
        Typically Grade 9 → 10 in South African schools.
        """
        if not from_grade or not to_grade:
            return False

        # In South Africa, Grade 10 is when learners choose electives
        # This is configurable — check if to_grade has elective subjects
        if to_grade.grade_number == 10:
            return True

        return False

    def _copy_subject_enrollments(self, promotion: LearnerPromotion, new_enrollment):
        """
        Copy subject enrollments from the previous year.
        Used when learner is promoted or retained.
        
        For promotions requiring subject selection (Grade 9→10),
        this creates placeholder enrollments that will be replaced
        once the parent completes subject selection.
        """
        from academics.models.learner import SubjectEnrollment

        # Get subjects from previous year
        previous_subjects = SubjectEnrollment.objects.filter(
            learner_enrollment__learner=promotion.learner,
            learner_enrollment__academic_year=promotion.from_academic_year,
        ).select_related('subject_offering__subject')

        if not previous_subjects.exists():
            # No subjects to copy — might be early grades with no subject tracking
            return

        # If subject selection is required, don't copy — parent will select
        if promotion.requires_subject_selection:
            logger.info(
                f'Skipping subject copy for {promotion.learner.admission_number} '
                f'— subject selection required'
            )
            return

        # Copy subjects to new year
        for prev_subject in previous_subjects:
            # Find equivalent subject offering in new year
            try:
                new_offering = prev_subject.subject_offering.subject.offerings.get(
                    academic_year=promotion.to_academic_year,
                    grade=new_enrollment.grade,
                )

                SubjectEnrollment.objects.create(
                    learner_enrollment=new_enrollment,
                    subject_offering=new_offering,
                    enrollment_date=new_enrollment.enrollment_date,
                )
            except Exception as exc:
                logger.warning(
                    f'Could not copy subject {prev_subject.subject_offering.subject.name} '
                    f'for {promotion.learner.admission_number}: {exc}'
                )

    def _create_reenrollment(self, promotion: LearnerPromotion, new_enrollment):
        """
        Create a Reenrollment record for parent confirmation.
        Triggered when config.require_parent_reenrollment_confirmation is True
        or when subject selection is required.
        """
        from datetime import timedelta

        reenrollment = Reenrollment.objects.create(
            learner=promotion.learner,
            promotion=promotion,
            enrollment_year=promotion.to_academic_year.year,
            to_academic_year=promotion.to_academic_year,
            to_grade=promotion.to_grade,
            subject_selection_required=promotion.requires_subject_selection,
            learner_enrollment=new_enrollment,
            status=Reenrollment.STATUS_PENDING,
            expiry_date=timezone.now().date() + timedelta(days=30),
        )

        # Send reenrollment email
        from ..tasks import send_reenrollment_required_email_task
        send_reenrollment_required_email_task.delay(reenrollment.pk)

        logger.info(
            f'Reenrollment created for {promotion.learner.admission_number} '
            f'(expires {reenrollment.expiry_date})'
        )

    def _send_promotion_notification(self, promotion: LearnerPromotion):
        """Fire email task to notify parent of promotion."""
        from ..tasks import send_promotion_notification_email_task
        send_promotion_notification_email_task.delay(promotion.pk)