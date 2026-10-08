"""
Login accounts for people in the partner network.

Accounts are created without a usable password. The person sets one through a one-time
link built on Django's password reset tokens, so the link stops working as soon as it is
used, when the password changes, or after PASSWORD_RESET_TIMEOUT.
"""

import re
import secrets
import unicodedata

from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import User
from partners.exceptions import AccessError, RosterError
from partners.models import LocationMembership

from . import scope


def _slug(value):
    ascii_value = unicodedata.normalize('NFKD', value or '').encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]+', '', ascii_value.lower())


def generate_username(first_name, last_name):
    """firstname.lastname plus a short random suffix; never derived from a phone number."""
    base = '.'.join(part for part in (_slug(first_name), _slug(last_name)) if part) or 'member'
    base = base[:120]
    for _attempt in range(10):
        candidate = f'{base}.{secrets.token_hex(2)}'
        if not User.objects.filter(username__iexact=candidate).exists():
            return candidate
    return f'{base}.{secrets.token_hex(6)}'


def activation_path(user):
    """Relative URL of the one-time set-password page for this user."""
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return reverse('partners:activate', kwargs={'uidb64': uidb64, 'token': token})


def needs_activation(user):
    return not user.has_usable_password() and user.last_login is None


def _phone_free(phone):
    return bool(phone) and not User.objects.filter(phone_number=phone).exists()


def create_account(first_name, last_name, role, email='', phone=''):
    """
    A new active user with no usable password.
    A phone number already used by another account is left off rather than linked,
    because linking would hand an existing account to whoever typed the number.
    """
    user = User(
        username=generate_username(first_name, last_name),
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        email=(email or '').strip().lower() or None,
        phone_number=phone.strip() if _phone_free(phone) else None,
        role=role,
        is_active=True,
    )
    user.set_unusable_password()
    user.save()
    return user


@transaction.atomic
def invite_member(location, actor, first_name, last_name, role, email='', phone='', trained_on=None):
    """
    Add a coordinator or facilitator to a location.
    Reuses an existing partner-staff account with the same email; refuses to attach any
    other kind of account, so a student or admin can never be pulled into a location by typo.
    Returns (membership, created_account).
    """
    if role not in LocationMembership.Role.values:
        raise RosterError('Unknown team role.')
    if not scope.can_invite(actor, location, role):
        raise RosterError('You cannot add someone in that role to this location.')

    email = (email or '').strip().lower()
    user = None
    if email:
        user = User.objects.filter(email__iexact=email).first()
    if user is None and phone:
        user = User.objects.filter(phone_number=phone.strip()).first()

    created_account = False
    if user is None:
        user = create_account(first_name, last_name, User.PARTNER, email=email, phone=phone)
        created_account = True
    elif user.role != User.PARTNER:
        raise AccessError(
            'That email or phone already belongs to an account that is not partner staff. '
            'Use different contact details, or ask programme staff to help.'
        )

    membership, created = LocationMembership.objects.get_or_create(
        user=user, location=location, role=role,
        defaults={'trained_on': trained_on, 'added_by': actor},
    )
    if not created:
        membership.is_active = True
        if trained_on:
            membership.trained_on = trained_on
        membership.save(update_fields=['is_active', 'trained_on', 'updated_at'])
    return membership, created_account


@transaction.atomic
def deactivate_member(membership, actor):
    if not scope.can_invite(actor, membership.location, membership.role):
        raise RosterError('You cannot remove someone in that role from this location.')
    if membership.user_id == actor.pk:
        raise RosterError('You cannot remove yourself. Ask programme staff to do it.')
    membership.is_active = False
    membership.save(update_fields=['is_active', 'updated_at'])
    # I'm also taking them off this location's cohorts so stale assignments never grant access
    for cohort in membership.location.cohorts.filter(facilitators=membership.user):
        if not LocationMembership.objects.filter(
            user=membership.user, location=membership.location, is_active=True,
        ).exists():
            cohort.facilitators.remove(membership.user)
    return membership
