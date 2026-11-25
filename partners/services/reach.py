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


@transaction.atomic
def add_organisation(user, organisation, actor):
    """Give one ambassador or hub administrator one more partner. Returns False if they had it."""
    _require_admin(actor)
    if user.role not in User.NETWORK_SCOPED_ROLES:
        raise PartnersError('Only hub administrators and ambassadors are assigned partners.')
    _row, created = StaffAssignment.objects.get_or_create(
        user=user, organisation=organisation, defaults={'added_by': actor},
    )
    return created


@transaction.atomic
def remove_organisation(user, organisation, actor):
    _require_admin(actor)
    removed, _detail = StaffAssignment.objects.filter(user=user, organisation=organisation).delete()
    return bool(removed)


def update_shown_organisations(user, shown, selected, actor):
    """
    Save a filtered picker. Only the partners that were on screen can change: anything
    assigned but filtered out of view is kept, so narrowing the list never removes it.
    """
    shown_ids = {organisation.pk for organisation in shown}
    selected_ids = {organisation.pk for organisation in selected} & shown_ids
    current = set(
        user.network_assignments.filter(organisation__isnull=False).values_list('organisation_id', flat=True)
    )
    keep = (current - shown_ids) | selected_ids
    return set_organisations(user, PartnerOrganisation.objects.filter(pk__in=keep), actor)


def coverage_for(organisation):
    """Who covers a partner: hub administrators through its country, and anyone assigned it directly."""
    by_country = User.objects.filter(
        role=User.HUB_ADMIN, is_active=True, network_assignments__country=organisation.country,
    )
    assigned = User.objects.filter(
        role__in=User.NETWORK_SCOPED_ROLES, is_active=True, network_assignments__organisation=organisation,
    )
    return {
        'by_country': list(by_country.distinct().order_by('first_name', 'last_name')),
        'assigned': list(assigned.distinct().order_by('first_name', 'last_name')),
    }


def summary(user):
    """A short description of someone's reach for lists: countries, a partner count, or everything."""
    from partners.models.base import country_name

    if user.role in (User.ADMIN, User.PROGRAMME_STAFF, User.LEADER):
        return 'All partners'
    if user.role not in User.NETWORK_SCOPED_ROLES:
        return ''
    rows = list(user.network_assignments.all())
    countries = sorted(country_name(row.country) for row in rows if row.country)
    partners = sum(1 for row in rows if row.organisation_id)
    if user.role == User.AMBASSADOR:
        countries = []
    parts = []
    if countries:
        parts.append(', '.join(countries))
    if partners:
        parts.append(f'{partners} partner{"s" if partners != 1 else ""}')
    return ' and '.join(parts) or 'Nothing assigned yet'
