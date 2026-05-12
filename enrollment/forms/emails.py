"""
Email notification service for the enrollment module.

Follows the same _send() pattern as academics/emails.py.
Console backend in development (DEBUG=True), SMTP in production.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def _send(subject, to_emails, template_name, context, from_email=None):
    """
    Render both a .txt and .html template, build a multipart email, and send.
    Returns True on success, False on failure. Never raises.
    """
    from_email = from_email or settings.DEFAULT_FROM_EMAIL

    try:
        text_body = render_to_string(
            f'enrollment/emails/{template_name}.txt', context
        ).strip()
        html_body = render_to_string(
            f'enrollment/emails/{template_name}.html', context
        )
    except Exception as exc:
        logger.error(f'Enrollment email template render failed ({template_name}): {exc}')
        return False

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=from_email,
        to=to_emails,
    )
    msg.attach_alternative(html_body, 'text/html')

    try:
        msg.send(fail_silently=False)
        backend = settings.EMAIL_BACKEND.split('.')[-1]
        logger.info(
            f'Enrollment email sent | subject="{subject}" '
            f'| to={to_emails} | backend={backend}'
        )
        return True
    except Exception as exc:
        logger.error(
            f'Enrollment email failed | subject="{subject}" '
            f'| to={to_emails} | error={exc}'
        )
        return False


def send_application_received(application):
    """
    Confirmation email to the guardian on successful submission.
    Includes reference number, required documents list, and status check link.
    """
    from .models.application import ApplicationDocumentRequirement

    required_docs = ApplicationDocumentRequirement.objects.filter(
        template=application.template,
        is_required=True,
    ).order_by('document_type')

    context = {
        'application':    application,
        'required_docs':  required_docs,
        'school_name':    _get_school_name(),
        'site_url':       settings.SITE_URL,
        'docs_url': (
            f"{settings.SITE_URL}/enroll/documents/"
            f"{application.reference_number}/"
        ),
        'status_url': f"{settings.SITE_URL}/enroll/status/",
    }

    return _send(
        subject=(
            f'[{_get_school_name()}] Application received — '
            f'{application.reference_number}'
        ),
        to_emails=[application.guardian_email],
        template_name='application_received',
        context=context,
    )


def send_admin_notification(application):
    """
    Notification email to the enrollment admin when a new application arrives.
    Flags possible duplicates and includes a direct link to the admin review.
    """
    config = _get_enrollment_config()
    if not config.enrollment_admin_email:
        logger.info('No enrollment_admin_email configured — skipping admin notification.')
        return False

    context = {
        'application':          application,
        'school_name':          _get_school_name(),
        'site_url':             settings.SITE_URL,
        'is_possible_duplicate': application.suspected_duplicate_of is not None,
        'duplicate':            application.suspected_duplicate_of,
        'admin_url': (
            f"{settings.SITE_URL}/enroll/admin/applications/"
            f"{application.pk}/"
        ),
    }

    return _send(
        subject=(
            f'[Enrollment] New application — {application.reference_number} '
            f'({application.get_full_name()})'
            + (' ⚠ Possible duplicate' if application.suspected_duplicate_of else '')
        ),
        to_emails=[config.enrollment_admin_email],
        template_name='admin_notification',
        context=context,
    )


def send_document_request(application, document_type_display, reason):
    """
    Notify the guardian that a specific document needs to be re-uploaded.
    Sent when admin rejects a document in the review queue.
    """
    context = {
        'application':         application,
        'document_type':       document_type_display,
        'rejection_reason':    reason,
        'school_name':         _get_school_name(),
        'docs_url': (
            f"{settings.SITE_URL}/enroll/documents/"
            f"{application.reference_number}/"
        ),
    }

    return _send(
        subject=(
            f'[{_get_school_name()}] Document required — '
            f'{application.reference_number}'
        ),
        to_emails=[application.guardian_email],
        template_name='document_request',
        context=context,
    )


# ---- Helpers ----

def _get_school_name():
    try:
        from academics.models.school import School
        school = School.get_current()
        return school.name if school else 'Scholr'
    except Exception:
        return 'Scholr'


def _get_enrollment_config():
    from .models.application import EnrollmentConfig
    return EnrollmentConfig.get()