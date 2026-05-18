"""
Celery tasks for the enrollment module.
All email sends are deferred to Celery so the HTTP response
returns immediately after application submission.
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_application_received_email_task(self, application_pk):
    """
    Send the guardian confirmation email after successful submission.
    Retries up to 3 times on failure (SMTP timeouts, etc.).
    """
    try:
        from .models.application import Application
        from .emails import send_application_received

        application = Application.objects.get(pk=application_pk)
        send_application_received(application)
    except Exception as exc:
        logger.error(
            f'send_application_received_email_task failed '
            f'for application {application_pk}: {exc}'
        )
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            pass


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_admin_notification_email_task(self, application_pk):
    """
    Notify the enrollment admin of a new application.
    Fired alongside the guardian confirmation.
    """
    try:
        from .models.application import Application
        from .emails import send_admin_notification

        application = Application.objects.get(pk=application_pk)
        send_admin_notification(application)
    except Exception as exc:
        logger.error(
            f'send_admin_notification_email_task failed '
            f'for application {application_pk}: {exc}'
        )
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            pass






@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_document_request_email_task(self, application_pk, document_type, reason):
    """Notify guardian that a specific document needs re-upload."""
    try:
        from .models.application import Application
        from .emails import send_document_request

        application = Application.objects.get(pk=application_pk)
        send_document_request(application, document_type, reason)
    except Exception as exc:
        logger.error(f'send_document_request_email_task failed: {exc}')
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            pass


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_offer_issued_email_task(self, application_pk):
    """Send conditional offer email to guardian."""
    try:
        from .models.application import Application
        from .emails import send_offer_issued

        application = Application.objects.get(pk=application_pk)
        send_offer_issued(application)
    except Exception as exc:
        logger.error(f'send_offer_issued_email_task failed: {exc}')
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            pass


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_application_approved_email_task(self, application_pk):
    """Send enrollment confirmation email."""
    try:
        from .models.application import Application
        from .emails import send_application_approved

        application = Application.objects.get(pk=application_pk)
        send_application_approved(application)
    except Exception as exc:
        logger.error(f'send_application_approved_email_task failed: {exc}')
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            pass


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_application_rejected_email_task(self, application_pk):
    """Send rejection email with reason."""
    try:
        from .models.application import Application
        from .emails import send_application_rejected

        application = Application.objects.get(pk=application_pk)
        send_application_rejected(application)
    except Exception as exc:
        logger.error(f'send_application_rejected_email_task failed: {exc}')
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            pass


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_waitlist_offer_email_task(self, application_pk):
    """Send waitlist promotion offer with 48h deadline."""
    try:
        from .models.application import Application
        from .emails import send_waitlist_offer

        application = Application.objects.get(pk=application_pk)
        send_waitlist_offer(application)
    except Exception as exc:
        logger.error(f'send_waitlist_offer_email_task failed: {exc}')
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            pass






@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_promotion_notification_email_task(self, promotion_pk):
    """Send promotion notification to parent."""
    try:
        from .models.promotion import LearnerPromotion
        from .emails import send_promotion_notification

        promotion = LearnerPromotion.objects.get(pk=promotion_pk)
        send_promotion_notification(promotion)
    except Exception as exc:
        logger.error(f'send_promotion_notification_email_task failed: {exc}')
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            pass


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_reenrollment_required_email_task(self, reenrollment_pk):
    """Send re-enrollment confirmation request to parent."""
    try:
        from .models.reenrollment import Reenrollment
        from .emails import send_reenrollment_required

        reenrollment = Reenrollment.objects.get(pk=reenrollment_pk)
        send_reenrollment_required(reenrollment)
    except Exception as exc:
        logger.error(f'send_reenrollment_required_email_task failed: {exc}')
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            pass



@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_reenrollment_confirmation_email_task(self, reenrollment_pk):
    """Send re-enrollment completion confirmation."""
    try:
        from .models.reenrollment import Reenrollment
        from .emails import send_reenrollment_confirmation

        reenrollment = Reenrollment.objects.get(pk=reenrollment_pk)
        send_reenrollment_confirmation(reenrollment)
    except Exception as exc:
        logger.error(f'send_reenrollment_confirmation_email_task failed: {exc}')
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            pass