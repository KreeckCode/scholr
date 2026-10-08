"""
Who can see and change what.

Portal access comes from LocationMembership rows, never from free-text fields.
Every portal queryset starts from one of these functions.
"""

from django.db.models import Q

from accounts.models import User
from partners.models import Cohort, LocationMembership, TrainingLocation

PROGRAMME_STAFF_ROLES = frozenset({User.ADMIN, User.PROGRAMME_STAFF})

STAFF = 'staff'
COORDINATOR = LocationMembership.Role.COORDINATOR.value
FACILITATOR = LocationMembership.Role.FACILITATOR.value


def is_programme_staff(user):
    return bool(user and user.is_authenticated and user.is_active and user.role in PROGRAMME_STAFF_ROLES)


def _active_memberships(user, role=None):
    queryset = LocationMembership.objects.filter(user=user, is_active=True)
    if role:
        queryset = queryset.filter(role=role)
    return queryset


def locations_for(user):
    """Locations the user may open in the portal."""
    if is_programme_staff(user):
        return TrainingLocation.objects.all()
    if not (user and user.is_authenticated and user.is_active):
        return TrainingLocation.objects.none()
    return TrainingLocation.objects.filter(
        id__in=_active_memberships(user).values('location_id'),
    )


def coordinated_locations(user):
    """Locations where the user may manage cohorts, participants and the team."""
    if not (user and user.is_authenticated and user.is_active):
        return TrainingLocation.objects.none()
    return TrainingLocation.objects.filter(
        id__in=_active_memberships(user, COORDINATOR).values('location_id'),
    )


def cohorts_for(user):
    """
    Cohorts the user may open.
    Coordinators see every cohort at their locations; facilitators see the cohorts
    they are assigned to, and only while their membership at that location is active.
    """
    if is_programme_staff(user):
        return Cohort.objects.all()
    if not (user and user.is_authenticated and user.is_active):
        return Cohort.objects.none()
    coordinator_locations = _active_memberships(user, COORDINATOR).values('location_id')
    facilitator_locations = _active_memberships(user, FACILITATOR).values('location_id')
    return Cohort.objects.filter(
        Q(location_id__in=coordinator_locations)
        | Q(location_id__in=facilitator_locations, facilitators=user)
    ).distinct()


def role_at(user, location):
    """'staff', 'coordinator', 'facilitator' or None. Coordinator wins over facilitator."""
    if is_programme_staff(user):
        return STAFF
    if not (user and user.is_authenticated and user.is_active):
        return None
    roles = set(
        _active_memberships(user).filter(location=location).values_list('role', flat=True)
    )
    if COORDINATOR in roles:
        return COORDINATOR
    if FACILITATOR in roles:
        return FACILITATOR
    return None


def can_manage_location(user, location):
    """Coordinators run their location. Programme staff are read-only in the portal."""
    return role_at(user, location) == COORDINATOR


def can_record_for_cohort(user, cohort):
    """Take registers and enter results: the location's coordinator or an assigned facilitator."""
    role = role_at(user, cohort.location)
    if role == COORDINATOR:
        return True
    if role == FACILITATOR:
        return cohort.facilitators.filter(pk=user.pk).exists()
    return False


def can_view_contacts(user, location):
    """Phone numbers and emails: coordinators and programme staff, never facilitators."""
    return role_at(user, location) in (STAFF, COORDINATOR)


def can_grant_access(user, location):
    return role_at(user, location) in (STAFF, COORDINATOR)


def can_invite(user, location, role):
    """Programme staff appoint coordinators; coordinators add facilitators."""
    actor_role = role_at(user, location)
    if actor_role == STAFF:
        return True
    return actor_role == COORDINATOR and role == FACILITATOR
