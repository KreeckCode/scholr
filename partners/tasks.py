"""
Background work for the partner network: notifications, licence expiry and reminders.
Emails are plain text so they read well on basic phones and low-bandwidth connections.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Max, Q
from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger(__name__)


def staff_recipients():
    """PARTNERS_STAFF_EMAILS plus every active programme staff user with an email."""
    from accounts.models import User

    addresses = {address.lower() for address in settings.PARTNERS_STAFF_EMAILS}
    addresses.update(
        email.lower() for email in User.objects.filter(
            role=User.PROGRAMME_STAFF, is_active=True,
        ).exclude(email__isnull=True).exclude(email='').values_list('email', flat=True)
    )
    return sorted(addresses)


def absolute(path):
    return f"{settings.SITE_URL.rstrip('/')}{path}"


def _send(subject, template, context, recipients):
    recipients = [address for address in recipients if address]
    if not recipients:
        logger.warning('No recipients for "%s"; email not sent', subject)
        return 0
    body = render_to_string(template, {**context, 'brand': settings.SITE_BRAND})
    return send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=False)


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def notify_staff_of_enquiry(self, partnership_id, created):
    from django.urls import reverse

    from partners.models import Partnership

    partnership = (
        Partnership.objects.select_related('organisation').prefetch_related('events').filter(pk=partnership_id).first()
    )
    if partnership is None:
        return 0
    event = partnership.events.order_by('-created_at').first()
    context = {
        'partnership': partnership,
        'organisation': partnership.organisation,
        'created': created,
        'summary': event.body if event else '',
        'url': absolute(reverse('partners:organisation_detail', kwargs={'org_id': partnership.organisation.public_id})),
    }
    subject = f"{'New' if created else 'Repeat'} METP partner enquiry: {partnership.organisation.name}"
    try:
        return _send(subject, 'partners/emails/enquiry_staff.txt', context, staff_recipients())
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def send_activation_email(self, user_id, location_id):
    from accounts.models import User
    from partners.models import TrainingLocation
    from partners.services.accounts import activation_path, needs_activation

    user = User.objects.filter(pk=user_id, is_active=True).first()
    location = TrainingLocation.objects.filter(pk=location_id).first()
    if user is None or location is None or not user.email or not needs_activation(user):
        return 0
    context = {'user': user, 'location': location, 'url': absolute(activation_path(user))}
    try:
        return _send('Set your password for the METP location portal',
                     'partners/emails/activation.txt', context, [user.email])
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task
def update_licence_statuses():
    """Daily: expire overdue licences, then email reminders 90, 30 and 7 days before an end date."""
    from partners.services import licences

    today = timezone.localdate()
    expired = licences.expire_overdue(today)
    reminded = 0
    for licence in licences.reminders_due(today):
        organisation = licence.organisation
        recipients = [contact.email for contact in organisation.contacts.all() if contact.is_primary]
        partnership = getattr(organisation, 'partnership', None)
        if partnership is not None and partnership.owner and partnership.owner.email:
            recipients.append(partnership.owner.email)
        if not recipients:
            recipients = staff_recipients()
        context = {'licence': licence, 'organisation': organisation, 'days': licence.days_remaining(today)}
        try:
            reminded += bool(_send(
                f'METP licence {licence.number} ends on {licence.ends_on:%d %B %Y}',
                'partners/emails/licence_reminder.txt', context, sorted(set(recipients)),
            ))
        except Exception:
            logger.exception('Licence reminder for %s failed', licence.number)
    return {'expired': expired, 'reminded': reminded}


@shared_task
def stalled_partnerships_digest():
    """Weekly: partnerships with an overdue next step, or with no activity for PARTNERS_STALL_DAYS."""
    from django.urls import reverse

    from partners.models import Partnership, Stage

    today = timezone.localdate()
    quiet_since = timezone.now() - timedelta(days=settings.PARTNERS_STALL_DAYS)
    open_partnerships = (
        Partnership.objects.exclude(stage__in=[Stage.LAUNCHED, Stage.LOST])
        .filter(organisation__is_archived=False)
        .annotate(last_event=Max('events__created_at'))
        .filter(Q(next_action_due__lt=today) | Q(last_event__lt=quiet_since) | Q(last_event__isnull=True))
        .select_related('organisation', 'owner')
        .order_by('next_action_due')
    )
    rows = [
        {
            'partnership': partnership,
            'url': absolute(reverse('partners:organisation_detail',
                                    kwargs={'org_id': partnership.organisation.public_id})),
        }
        for partnership in open_partnerships
    ]
    if not rows:
        return 0
    return _send(
        f'METP pipeline: {len(rows)} partnership(s) need attention',
        'partners/emails/stalled_digest.txt',
        {'rows': rows, 'today': today, 'stall_days': settings.PARTNERS_STALL_DAYS},
        staff_recipients(),
    )
