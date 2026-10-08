"""
The only route from the partner network to Moodle.

Granting online access creates a zero-value, licence-funded Order and hands it to the
existing process_order_enrollment_task. That task creates the StudentEnrollment and
CourseEnrollment rows and syncs to Moodle exactly as it does for a buyer, so the
reconciliation job sees a product enrolment and keeps the access.

This module must never import integration.moodle_client or integration.services.
"""

import logging
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import Order, OrderItem, Product, StudentEnrollment, User
from integration.models import CourseEnrollment
from partners.exceptions import AccessError
from partners.models import Cohort, CohortMembership

from . import licences, scope
from .accounts import create_account

logger = logging.getLogger(__name__)

PAYMENT_METHOD = 'partner_licence'


def online_product():
    product = Product.objects.filter(slug=settings.PARTNERS_ONLINE_PRODUCT_SLUG).first()
    if product is None:
        raise AccessError(
            'The online programme product is not set up yet. Ask an administrator to create it '
            f'with the slug "{settings.PARTNERS_ONLINE_PRODUCT_SLUG}".'
        )
    return product


def _queue_enrolment(order_id):
    # I'm importing here so a broken task module can never stop the portal from loading
    from shop.tasks import process_order_enrollment_task

    from partners.utils import enqueue
    if not enqueue(process_order_enrollment_task, order_id):
        logger.error('Order %s is saved but its enrolment was not queued; reprocess it from the admin', order_id)


@transaction.atomic
def grant_online_access(membership, actor, today=None):
    """
    Give an enrolled participant in a blended cohort online access.
    Idempotent: a second call returns the existing order. Returns (order, new_user).
    """
    membership = (
        CohortMembership.objects.select_for_update(of=('self',))
        .select_related('cohort__location__licence', 'participant__user')
        .get(pk=membership.pk)
    )
    cohort = membership.cohort
    location = cohort.location
    participant = membership.participant

    if not scope.can_grant_access(actor, location):
        raise AccessError('Only the location coordinator or programme staff can give online access.')
    if membership.has_online_access:
        return membership.access_order, None
    if membership.access_order_id and membership.access_withdrawn_at:
        raise AccessError(
            'Online access was withdrawn for this participant. Programme staff can restore it '
            'from the order in the admin.'
        )
    if cohort.delivery != Cohort.Delivery.BLENDED:
        raise AccessError('Online access is only for blended cohorts.')
    if membership.status != CohortMembership.Status.ENROLLED:
        raise AccessError('Only an enrolled participant can be given online access.')
    licences.ensure(
        licences.allows_online_access(location.licence, today),
        'The licence for this location is not active, so online access cannot be given.',
    )
    if not (participant.phone or participant.email):
        raise AccessError('Add a phone number or email for this participant first.')

    product = online_product()
    user = participant.user
    new_user = None
    if user is None:
        user = create_account(
            participant.first_name, participant.last_name, User.STUDENT,
            email=participant.email, phone=participant.phone,
        )
        participant.user = user
        participant.save(update_fields=['user', 'updated_at'])
        new_user = user
    elif user.role != User.STUDENT:
        raise AccessError('This participant is linked to a staff account, which cannot study online.')
    elif StudentEnrollment.objects.filter(user=user, product=product, is_active=False).exists():
        # I'm refusing here because the shop extends a lapsed enrolment without reactivating it
        raise AccessError(
            'This participant has a lapsed enrolment in the online programme. Programme staff '
            'can reactivate it in the admin.'
        )

    if not user.is_verified:
        # I'm relying on the existing integration signal to create the Moodle mapping on this save
        user.is_verified = True
        user.verified_by = actor
        user.verified_at = timezone.now()
        user.save(update_fields=['is_verified', 'verified_by', 'verified_at', 'updated_at'])

    now = timezone.now()
    order = Order.objects.create(
        user=user,
        status=Order.FREE,
        subtotal=Decimal('0.00'),
        discount_amount=Decimal('0.00'),
        total_amount=Decimal('0.00'),
        currency=product.currency,
        payment_method=PAYMENT_METHOD,
        payment_metadata={
            'source': PAYMENT_METHOD,
            'licence': str(location.licence.public_id),
            'licence_number': location.licence.number,
            'location': str(location.public_id),
            'cohort': str(cohort.public_id),
            'membership': str(membership.public_id),
        },
        paid_at=now,
        admin_notes=f'Licence-funded online access for {cohort.name} at {location.name}.',
    )
    OrderItem.objects.create(order=order, product=product, quantity=1, unit_price=Decimal('0.00'))

    membership.access_order = order
    membership.access_granted_at = now
    membership.access_granted_by = actor
    membership.access_withdrawn_at = None
    membership.save(update_fields=[
        'access_order', 'access_granted_at', 'access_granted_by', 'access_withdrawn_at', 'updated_at',
    ])

    order_id = order.pk
    transaction.on_commit(lambda: _queue_enrolment(order_id))
    logger.info('Online access order %s queued for membership %s', order.order_number, membership.pk)
    return order, new_user


@transaction.atomic
def withdraw_online_access(membership, actor):
    """
    Deactivate the product enrolment created by the access order.
    Saving it inactive fires the existing shop signal, which handles Moodle unenrolment.
    """
    membership = (
        CohortMembership.objects.select_for_update(of=('self',))
        .select_related('cohort__location').get(pk=membership.pk)
    )
    if not scope.can_grant_access(actor, membership.cohort.location):
        raise AccessError('Only the location coordinator or programme staff can withdraw online access.')
    if not membership.has_online_access:
        raise AccessError('This participant does not have online access.')
    for enrolment in StudentEnrollment.objects.filter(order_id=membership.access_order_id, is_active=True):
        enrolment.is_active = False
        enrolment.save()
    membership.access_withdrawn_at = timezone.now()
    membership.save(update_fields=['access_withdrawn_at', 'updated_at'])
    return membership


ACCESS_NONE = 'none'
ACCESS_QUEUED = 'queued'
ACCESS_ACTIVE = 'active'
ACCESS_ERROR = 'error'
ACCESS_WITHDRAWN = 'withdrawn'

ACCESS_LABELS = {
    ACCESS_NONE: 'No online access',
    ACCESS_QUEUED: 'Being set up',
    ACCESS_ACTIVE: 'Active in Moodle',
    ACCESS_ERROR: 'Needs attention',
    ACCESS_WITHDRAWN: 'Withdrawn',
}


def _status_from(membership, sync_states):
    if not membership.access_order_id:
        return ACCESS_NONE
    if membership.access_withdrawn_at:
        return ACCESS_WITHDRAWN
    order = membership.access_order
    if order.enrollment_error:
        return ACCESS_ERROR
    if not order.enrollment_processed:
        return ACCESS_QUEUED
    if 'failed' in sync_states:
        return ACCESS_ERROR
    if 'pending' in sync_states or not sync_states:
        return ACCESS_QUEUED
    return ACCESS_ACTIVE


def access_statuses(memberships):
    """{membership.pk: status} for many memberships in one query. Reads integration models only."""
    memberships = list(memberships)
    user_ids = {
        membership.access_order.user_id for membership in memberships
        if membership.access_order_id and not membership.access_withdrawn_at
    }
    states = defaultdict(set)
    if user_ids:
        rows = CourseEnrollment.objects.filter(user_id__in=user_ids, is_active=True).values_list(
            'user_id', 'sync_status',
        )
        for user_id, sync_status in rows:
            states[user_id].add(sync_status)
    return {
        membership.pk: _status_from(
            membership, states[membership.access_order.user_id] if membership.access_order_id else set(),
        )
        for membership in memberships
    }


def access_status(membership):
    return access_statuses([membership])[membership.pk]
