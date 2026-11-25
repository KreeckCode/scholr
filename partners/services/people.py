"""
System administrators adding and finding network staff.

New staff get an account with no password and a one-time set-password link, the same way
coordinators do. Someone who already has an account is never given a second one: their
role is changed instead, so their history stays in one place.
"""
from django.db import transaction
from django.utils import timezone

from accounts.models import RoleChange, User
from partners.exceptions import PartnersError

from . import accounts
from .reach import _require_admin

CREATABLE_ROLES = (User.HUB_ADMIN, User.AMBASSADOR, User.LEADER, User.PROGRAMME_STAFF, User.ADMIN)


def manageable_staff():
    """Everyone whose role a system administrator can manage from these screens."""
    return User.objects.filter(role__in=User.ASSIGNABLE_STAFF_ROLES)


@transaction.atomic
def add_staff_member(actor, first_name, last_name, role, email='', phone=''):
    _require_admin(actor)
    if role not in CREATABLE_ROLES:
        raise PartnersError('Staff can be added as hub administrators, ambassadors, leaders, programme staff '
                            'or system administrators.')
    email = (email or '').strip().lower()
    phone = (phone or '').strip()
    if not email and not phone:
        raise PartnersError('Give an email address or a phone number, so they can be sent their link.')
    if email and User.objects.filter(email__iexact=email).exists():
        raise PartnersError('Someone already has an account with that email. Find them in the staff list '
                            'and change their role instead.')
    if phone and User.objects.filter(phone_number=phone).exists():
        raise PartnersError('Someone already has an account with that phone number. Find them in the staff '
                            'list and change their role instead.')

    user = accounts.create_account(first_name, last_name, role, email=email, phone=phone)
    # Verification gates students' enrolment; staff added by an administrator are vouched for already
    user.is_verified = True
    user.verified_by = actor
    user.verified_at = timezone.now()
    user.save(update_fields=['is_verified', 'verified_by', 'verified_at'])
    RoleChange.objects.create(user=user, from_role='', to_role=role, changed_by=actor, note='Account created')
    return user
