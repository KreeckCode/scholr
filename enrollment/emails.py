"""
Email notification service for the enrollment module.

Follows the same _send() pattern as academics/emails.py.
Console backend in development (DEBUG=True), SMTP in production.
"""
from datetime import timezone
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


def send_offer_issued(application):
    """
    Notification to guardian that a conditional offer has been issued.
    Includes payment link if registration fee is required.
    """
    template = application.template
    config = _get_enrollment_config()

    context = {
        'application':   application,
        'template':      template,
        'school_name':   _get_school_name(),
        'site_url':      settings.SITE_URL,
        'fee_required':  template.registration_fee_required,
        'fee_amount':    template.registration_fee_amount,
        'payment_url':   f"{settings.SITE_URL}/enroll/pay/{application.reference_number}/",
        'docs_url':      f"{settings.SITE_URL}/enroll/documents/{application.reference_number}/",
    }

    return _send(
        subject=(
            f'[{_get_school_name()}] Conditional offer issued — '
            f'{application.reference_number}'
        ),
        to_emails=[application.guardian_email],
        template_name='offer_issued',
        context=context,
    )


def send_application_approved(application):
    """
    Welcome email sent when application reaches STATUS_ENROLLED.
    Includes enrollment confirmation, school start date, and what to bring.
    """
    context = {
        'application':  application,
        'school_name':  _get_school_name(),
        'site_url':     settings.SITE_URL,
        'status_url':   f"{settings.SITE_URL}/enroll/status/",
    }

    return _send(
        subject=(
            f'[{_get_school_name()}] Enrollment confirmed — Welcome!'
        ),
        to_emails=[application.guardian_email],
        template_name='application_approved',
        context=context,
    )


def send_application_rejected(application):
    """
    Rejection email with reason and appeal instructions.
    """
    config = _get_enrollment_config()
    
    context = {
        'application':       application,
        'rejection_reason':  application.rejection_reason,
        'school_name':       _get_school_name(),
        'contact_email':     config.enrollment_admin_email or settings.DEFAULT_FROM_EMAIL,
    }

    return _send(
        subject=(
            f'[{_get_school_name()}] Application update — '
            f'{application.reference_number}'
        ),
        to_emails=[application.guardian_email],
        template_name='application_rejected',
        context=context,
    )


def send_waitlist_offer(application):
    """
    Notification that a waitlist place has become available.
    Includes 48-hour acceptance deadline.
    """
    from datetime import timedelta
    
    deadline = timezone.now() + timedelta(hours=48)
    
    context = {
        'application':   application,
        'school_name':   _get_school_name(),
        'site_url':      settings.SITE_URL,
        'deadline':      deadline,
        'acceptance_url': f"{settings.SITE_URL}/enroll/waitlist-accept/{application.reference_number}/",
    }

    return _send(
        subject=(
            f'[{_get_school_name()}] Place available — '
            f'{application.reference_number}'
        ),
        to_emails=[application.guardian_email],
        template_name='waitlist_offer',
        context=context,
    )




def send_promotion_notification(promotion):
    """
    Notify parent that their child has been promoted to the next grade.
    Includes details about the new grade and school start date.
    """
    from academics.models.learner import Guardian, LearnerGuardian

    # Get primary guardian
    guardian_relationship = LearnerGuardian.objects.filter(
        learner=promotion.learner,
        is_primary=True,
    ).select_related('guardian').first()

    if not guardian_relationship or not guardian_relationship.guardian.email:
        logger.warning(
            f'No primary guardian email for {promotion.learner.admission_number} '
            f'— promotion notification not sent'
        )
        return False

    guardian = guardian_relationship.guardian

    context = {
        'promotion':    promotion,
        'learner':      promotion.learner,
        'guardian':     guardian,
        'school_name':  _get_school_name(),
        'site_url':     settings.SITE_URL,
        'start_date':   promotion.to_academic_year.start_date if promotion.to_academic_year else None,
    }

    return _send(
        subject=(
            f'[{_get_school_name()}] '
            f'{promotion.learner.get_full_name()} — '
            f'{promotion.get_status_display()}'
        ),
        to_emails=[guardian.email],
        template_name='promotion_notification',
        context=context,
    )


def send_reenrollment_required(reenrollment):
    """
    Notify parent that re-enrollment confirmation is required.
    Includes link to the parent portal and expiry deadline.
    """
    from academics.models.learner import Guardian, LearnerGuardian

    guardian_relationship = LearnerGuardian.objects.filter(
        learner=reenrollment.learner,
        is_primary=True,
    ).select_related('guardian').first()

    if not guardian_relationship or not guardian_relationship.guardian.email:
        logger.warning(
            f'No primary guardian email for {reenrollment.learner.admission_number} '
            f'— reenrollment notification not sent'
        )
        return False

    guardian = guardian_relationship.guardian

    context = {
        'reenrollment':  reenrollment,
        'learner':       reenrollment.learner,
        'guardian':      guardian,
        'school_name':   _get_school_name(),
        'site_url':      settings.SITE_URL,
        'portal_url':    f"{settings.SITE_URL}/academics/portal/reenroll/{reenrollment.learner.pk}/",
        'expiry_date':   reenrollment.expiry_date,
    }

    return _send(
        subject=(
            f'[{_get_school_name()}] '
            f'Re-enrollment required — {reenrollment.learner.get_full_name()}'
        ),
        to_emails=[guardian.email],
        template_name='reenrollment_required',
        context=context,
    )