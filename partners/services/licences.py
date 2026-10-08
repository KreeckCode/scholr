"""
Licence lifecycle and what each status allows.

draft -> active -> suspended <-> active
active -> expired (daily task, when ends_on has passed)
any except terminated -> terminated
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from partners.exceptions import LicenceStateError
from partners.models import Cohort, Licence, PartnershipEvent

logger = logging.getLogger(__name__)

Status = Licence.Status


def next_licence_number(organisation, starts_on):
    """METP-<country>-<year>-<sequence>, unique across all licences."""
    prefix = f"METP-{organisation.country or 'XX'}-{starts_on.year}-"
    taken = Licence.objects.filter(number__startswith=prefix).values_list('number', flat=True)
    highest = 0
    for number in taken:
        tail = number.rsplit('-', 1)[-1]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f'{prefix}{highest + 1:04d}'


def create_licence(licence, actor):
    """Save a new draft licence built from a form; the number is assigned here."""
    licence.status = Status.DRAFT
    licence.created_by = actor
    for _attempt in range(5):
        licence.number = next_licence_number(licence.organisation, licence.starts_on)
        try:
            with transaction.atomic():
                licence.save()
        except IntegrityError:
            if not Licence.objects.filter(number=licence.number).exists():
                raise
            # I'm retrying with the next number when two drafts were saved at the same moment
            licence.pk = None
            continue
        _log(licence, actor, f'Licence {licence.number} drafted.')
        return licence
    raise LicenceStateError('Could not assign a licence number. Please try again.')


def _log(licence, actor, body):
    partnership = getattr(licence.organisation, 'partnership', None)
    if partnership is not None:
        PartnershipEvent.objects.create(
            partnership=partnership, kind=PartnershipEvent.Kind.LICENCE, body=body, actor=actor,
        )


def _set_status(licence, status, actor, reason):
    licence.status = status
    licence.status_reason = reason
    licence.status_changed_at = timezone.now()
    licence.save(update_fields=['status', 'status_reason', 'status_changed_at', 'updated_at'])


@transaction.atomic
def activate(licence, actor, today=None):
    """Start a draft licence, or reinstate a suspended one."""
    today = today or timezone.localdate()
    licence = Licence.objects.select_for_update().get(pk=licence.pk)
    if licence.status not in (Status.DRAFT, Status.SUSPENDED):
        raise LicenceStateError(f'A {licence.get_status_display().lower()} licence cannot be activated.')
    if licence.status == Status.DRAFT:
        if not licence.signed_on:
            raise LicenceStateError('Record the date the agreement was signed before activating.')
        if not licence.agreement_file:
            raise LicenceStateError('Upload the signed agreement before activating.')
    if licence.ends_on < today:
        raise LicenceStateError(
            f'This licence ended on {licence.ends_on:%d %b %Y}. Draft a new licence instead.'
        )
    other_active = Licence.objects.filter(
        organisation=licence.organisation, status=Status.ACTIVE,
    ).exclude(pk=licence.pk)
    if other_active.exists():
        raise LicenceStateError(
            f'{licence.organisation} already has an active licence ({other_active.first().number}). '
            f'Suspend or terminate it first.'
        )
    was_suspended = licence.status == Status.SUSPENDED
    _set_status(licence, Status.ACTIVE, actor, '')
    _log(licence, actor, f"Licence {licence.number} {'reinstated' if was_suspended else 'activated'}.")
    return licence


@transaction.atomic
def suspend(licence, actor, reason):
    reason = (reason or '').strip()
    if not reason:
        raise LicenceStateError('Give a reason for suspending the licence.')
    licence = Licence.objects.select_for_update().get(pk=licence.pk)
    if licence.status != Status.ACTIVE:
        raise LicenceStateError('Only an active licence can be suspended.')
    _set_status(licence, Status.SUSPENDED, actor, reason)
    _log(licence, actor, f'Licence {licence.number} suspended: {reason}')
    return licence


@transaction.atomic
def terminate(licence, actor, reason):
    reason = (reason or '').strip()
    if not reason:
        raise LicenceStateError('Give a reason for terminating the licence.')
    licence = Licence.objects.select_for_update().get(pk=licence.pk)
    if licence.status == Status.TERMINATED:
        raise LicenceStateError('This licence is already terminated.')
    _set_status(licence, Status.TERMINATED, actor, reason)
    _log(licence, actor, f'Licence {licence.number} terminated: {reason}')
    return licence


def expire_overdue(today=None):
    """Move active licences past their end date to expired. Returns how many changed."""
    today = today or timezone.localdate()
    expired = 0
    overdue = Licence.objects.filter(status=Status.ACTIVE, ends_on__lt=today).select_related('organisation')
    for licence in overdue:
        with transaction.atomic():
            _set_status(licence, Status.EXPIRED, None, f'Ended on {licence.ends_on:%d %b %Y}.')
            _log(licence, None, f'Licence {licence.number} expired.')
        expired += 1
    if expired:
        logger.info('Expired %s partner licence(s)', expired)
    return expired


def reminders_due(today=None):
    """Active licences whose end date is exactly one of the reminder offsets away."""
    today = today or timezone.localdate()
    dates = [today + timedelta(days=days) for days in settings.PARTNERS_LICENCE_REMINDER_DAYS]
    return (
        Licence.objects.filter(status=Status.ACTIVE, ends_on__in=dates)
        .select_related('organisation', 'organisation__partnership__owner')
        .prefetch_related('organisation__contacts')
    )


# ==================== WHAT A STATUS ALLOWS ====================

def _is_live(licence, today):
    return licence.status == Status.ACTIVE and licence.is_current(today)


def allows_new_cohorts(licence, today=None):
    return _is_live(licence, today or timezone.localdate())


def allows_new_participants(licence, today=None):
    return _is_live(licence, today or timezone.localdate())


def allows_online_access(licence, today=None):
    return _is_live(licence, today or timezone.localdate())


def allows_recording(licence, cohort, today=None):
    """
    Recording what already happened is allowed more widely than starting new work:
    a suspended licence still lets running cohorts be recorded, and an expired one still
    lets cohorts that started before the end date finish.
    """
    if licence.status == Status.ACTIVE:
        return True
    if licence.status == Status.SUSPENDED:
        return cohort.status == Cohort.Status.RUNNING
    if licence.status == Status.EXPIRED:
        return cohort.starts_on <= licence.ends_on
    return False


def ensure(allowed, message):
    if not allowed:
        raise LicenceStateError(message)
