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
        from .forms.emails import send_application_received

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
        from .forms.emails import send_admin_notification

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