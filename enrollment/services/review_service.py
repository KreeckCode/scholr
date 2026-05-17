"""
ReviewService — business logic for enrollment admin decisions.

All decision actions (approve, reject, issue offer) run through this service
to maintain consistency, record audit trails, and fire email notifications.
"""
import logging

from django.db import transaction
from django.utils import timezone

from ..models.application import Application, ApplicationDocument

logger = logging.getLogger(__name__)


class ReviewService:
    """
    Handles all admin review actions on applications.
    Every public method wraps database writes in a transaction
    and records the decision in ApplicationStatusHistory.
    """

    def approve_document(self, document: ApplicationDocument, reviewed_by):
        """
        Mark a document as approved.
        If all required documents for this application are now approved,
        auto-advance the application to STATUS_UNDER_REVIEW.
        """
        document.approve(reviewed_by)
        logger.info(
            f'Document approved: {document.application.reference_number} — '
            f'{document.get_document_type_display()} by {reviewed_by}'
        )

        self._check_all_documents_approved(document.application)

    def reject_document(self, document: ApplicationDocument, reviewed_by, reason: str):
        """
        Reject a document and fire an email to the guardian requesting re-upload.
        """
        document.reject(reviewed_by, reason)
        logger.info(
            f'Document rejected: {document.application.reference_number} — '
            f'{document.get_document_type_display()} by {reviewed_by}'
        )

        # Fire email task
        from ..tasks import send_document_request_email_task
        send_document_request_email_task.delay(
            application_pk=document.application.pk,
            document_type=document.get_document_type_display(),
            reason=reason,
        )

    def issue_offer(self, application: Application, issued_by, notes: str = ''):
        """
        Issue a conditional offer.
        Transitions the application to STATUS_OFFER_ISSUED.
        Fires an email to the guardian with payment instructions (if fee required).
        """
        with transaction.atomic():
            application.transition_status(
                Application.STATUS_OFFER_ISSUED,
                changed_by=issued_by,
                notes=notes or 'Conditional offer issued by admissions team.',
            )
            application.save(update_fields=['status'])

        logger.info(
            f'Offer issued: {application.reference_number} by {issued_by}'
        )

        # Fire email
        from ..tasks import send_offer_issued_email_task
        send_offer_issued_email_task.delay(application.pk)

    def approve_application(self, application: Application, approved_by, notes: str = ''):
        """
        Final approval — transitions to STATUS_ENROLLED.
        Used when no registration fee is required, or when fee has been paid.
        Fires welcome email to guardian.
        """
        with transaction.atomic():
            application.transition_status(
                Application.STATUS_ENROLLED,
                changed_by=approved_by,
                notes=notes or 'Application approved — enrollment confirmed.',
            )
            application.save(update_fields=['status'])

        logger.info(
            f'Application approved: {application.reference_number} by {approved_by}'
        )

        # Fire email
        from ..tasks import send_application_approved_email_task
        send_application_approved_email_task.delay(application.pk)

    def reject_application(self, application: Application, rejected_by, reason: str, internal_notes: str = ''):
        """
        Reject an application with a reason sent to the guardian.
        """
        with transaction.atomic():
            application.rejection_reason = reason
            application.transition_status(
                Application.STATUS_REJECTED,
                changed_by=rejected_by,
                notes=internal_notes or f'Application rejected. Reason: {reason[:100]}',
            )
            if internal_notes and internal_notes != reason:
                application.internal_notes = (
                    f"{application.internal_notes}\n\n"
                    f"[{timezone.now().strftime('%Y-%m-%d %H:%M')}] {rejected_by}: {internal_notes}"
                ).strip()
            application.save(update_fields=['status', 'rejection_reason', 'internal_notes'])

        logger.info(
            f'Application rejected: {application.reference_number} by {rejected_by}'
        )

        # Fire email
        from ..tasks import send_application_rejected_email_task
        send_application_rejected_email_task.delay(application.pk)

    def clear_duplicate(self, application: Application, cleared_by):
        """
        Clear the duplicate flag and move to STATUS_UNDER_REVIEW.
        Admin has manually confirmed this is not a duplicate.
        """
        with transaction.atomic():
            application.suspected_duplicate_of = None
            application.duplicate_cleared_by = cleared_by
            application.duplicate_cleared_at = timezone.now()
            application.transition_status(
                Application.STATUS_UNDER_REVIEW,
                changed_by=cleared_by,
                notes='Duplicate flag cleared by admin — confirmed not a duplicate.',
            )
            application.save(update_fields=[
                'status', 'suspected_duplicate_of',
                'duplicate_cleared_by', 'duplicate_cleared_at',
            ])

        logger.info(
            f'Duplicate cleared: {application.reference_number} by {cleared_by}'
        )

    def promote_from_waitlist(self, application: Application, promoted_by):
        """
        Promote a waitlisted application to STATUS_OFFER_ISSUED.
        Fires a waitlist offer email with a 48-hour acceptance deadline.
        """
        with transaction.atomic():
            old_position = application.waitlist_position
            application.waitlist_position = None
            application.transition_status(
                Application.STATUS_OFFER_ISSUED,
                changed_by=promoted_by,
                notes=f'Promoted from waitlist position {old_position}.',
            )
            application.save(update_fields=['status', 'waitlist_position'])

        logger.info(
            f'Waitlist promotion: {application.reference_number} '
            f'(position {old_position}) by {promoted_by}'
        )

        # Fire email
        from ..tasks import send_waitlist_offer_email_task
        send_waitlist_offer_email_task.delay(application.pk)

    # ---- Private helpers ----

    def _check_all_documents_approved(self, application):
        """
        Check if all required documents are now approved.
        If yes, auto-advance to STATUS_UNDER_REVIEW.
        """
        from ..models.application import ApplicationDocumentRequirement

        required_types = set(
            ApplicationDocumentRequirement.objects.filter(
                template=application.template,
                is_required=True,
            ).values_list('document_type', flat=True)
        )

        if not required_types:
            return

        approved_types = set(
            ApplicationDocument.objects.filter(
                application=application,
                status=ApplicationDocument.STATUS_APPROVED,
            ).values_list('document_type', flat=True)
        )

        if required_types.issubset(approved_types):
            if application.status == Application.STATUS_DOCUMENTS_PENDING:
                application.transition_status(
                    Application.STATUS_UNDER_REVIEW,
                    notes='All required documents approved. Ready for final review.',
                )
                application.save(update_fields=['status'])
                logger.info(
                    f'Auto-advanced to under_review: {application.reference_number} '
                    f'(all required docs approved)'
                )