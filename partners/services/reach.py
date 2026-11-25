"""
Setting a network staff member's reach: the countries a hub administrator covers and the
partners an ambassador or hub administrator is assigned. Only system administrators set
reach, and only for the roles that use it.
"""
from django.db import transaction

from accounts.models import User
from partners.exceptions import PartnersError
from partners.models import PartnerOrganisation, StaffAssignment
from partners.countries import COUNTRY_NAMES


def _require_admin(actor):
    if not (actor and actor.is_authenticated and actor.is_active and actor.role == User.ADMIN):
        raise PartnersError('Only system administrators can change who covers which partners.')


@transaction.atomic
def set_countries(user, countries, actor):
    """Replace a hub administrator's countries. Returns (added, removed) country codes."""
    _require_admin(actor)
    if user.role != User.HUB_ADMIN:
        raise PartnersError('Only hub administrators cover countries.')
    wanted = {code.strip().upper() for code in countries if code and code.strip()}
    unknown = wanted - set(COUNTRY_NAMES)
    if unknown:
        raise PartnersError(f'Unknown country code: {", ".join(sorted(unknown))}.')

    current = set(user.network_assignments.exclude(country='').values_list('country', flat=True))
    user.network_assignments.filter(country__in=current - wanted).delete()
    StaffAssignment.objects.bulk_create(
        [StaffAssignment(user=user, country=code, added_by=actor) for code in sorted(wanted - current)]
    )
    return sorted(wanted - current), sorted(current - wanted)


@transaction.atomic
def set_organisations(user, organisations, actor):
    """Replace the partners assigned to an ambassador or hub administrator."""
    _require_admin(actor)
    if user.role not in User.NETWORK_SCOPED_ROLES:
        raise PartnersError('Only hub administrators and ambassadors are assigned partners.')
    wanted = {organisation.pk for organisation in organisations}
    if PartnerOrganisation.objects.filter(pk__in=wanted).count() != len(wanted):
        raise PartnersError('One of those partners no longer exists.')

    current = set(user.network_assignments.filter(organisation__isnull=False).values_list('organisation_id', flat=True))
    user.network_assignments.filter(organisation_id__in=current - wanted).delete()
    StaffAssignment.objects.bulk_create(
        [StaffAssignment(user=user, organisation_id=pk, added_by=actor) for pk in sorted(wanted - current)]
    )
    return sorted(wanted - current), sorted(current - wanted)


def clear_unused_reach(user, new_role):
    """
    Drop assignments the new role does not use: everything for roles without reach,
    and country rows for ambassadors, who only cover named partners.
    Returns how many rows were removed.
    """
    rows = user.network_assignments.all()
    if new_role == User.AMBASSADOR:
        rows = rows.exclude(country='')
    elif new_role == User.HUB_ADMIN:
        return 0
    removed, _detail = rows.delete()
    return removed
