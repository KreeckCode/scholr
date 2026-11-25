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
    if is_portal_hub_admin(user):
        return locations_in_reach(user)
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
    if is_portal_hub_admin(user):
        return cohorts_in_reach(user)
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
    # Hub administrators act as programme staff at the locations of partners in their reach
    if is_portal_hub_admin(user):
        return STAFF if locations_in_reach(user).filter(pk=location.pk).exists() else None
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


# ==================== NETWORK REACH ====================
# The staff side of the partner network. Admins and programme staff see every partner;
# leaders see every partner but change nothing; hub administrators see the partners in
# their countries; ambassadors see the partners assigned to them. Everyone else sees none.
# Staff views must start every query from one of the *_in_reach functions below.

GLOBAL_REACH_ROLES = frozenset({User.ADMIN, User.PROGRAMME_STAFF, User.LEADER})
NETWORK_STAFF_ROLES = GLOBAL_REACH_ROLES | frozenset(User.NETWORK_SCOPED_ROLES)


def network_role(user):
    """The person's network staff role, or None for everyone else."""
    if user and user.is_authenticated and user.is_active and user.role in NETWORK_STAFF_ROLES:
        return user.role
    return None


def has_global_reach(user):
    return network_role(user) in GLOBAL_REACH_ROLES


def is_network_read_only(user):
    return network_role(user) in User.NETWORK_READ_ONLY_ROLES


def assigned_countries(user):
    """Countries a hub administrator covers. Empty for every other role."""
    if network_role(user) != User.HUB_ADMIN:
        return []
    return list(user.network_assignments.exclude(country='').values_list('country', flat=True))


def organisations_in_reach(user):
    from partners.models import PartnerOrganisation

    role = network_role(user)
    if role is None:
        return PartnerOrganisation.objects.none()
    if role in GLOBAL_REACH_ROLES:
        return PartnerOrganisation.objects.all()

    assigned = user.network_assignments.filter(organisation__isnull=False).values('organisation_id')
    reach = Q(pk__in=assigned)
    if role == User.HUB_ADMIN:
        reach |= Q(country__in=assigned_countries(user))
    return PartnerOrganisation.objects.filter(reach)


def partnerships_in_reach(user):
    from partners.models import Partnership
    return Partnership.objects.filter(organisation__in=organisations_in_reach(user))


def licences_in_reach(user):
    from partners.models import Licence
    return Licence.objects.filter(organisation__in=organisations_in_reach(user))


def locations_in_reach(user):
    """A location belongs to whoever holds its partner, wherever the location itself is."""
    return TrainingLocation.objects.filter(organisation__in=organisations_in_reach(user))


def cohorts_in_reach(user):
    return Cohort.objects.filter(location__organisation__in=organisations_in_reach(user))


def can_change_organisation(user, organisation):
    """Admins and programme staff anywhere; hub administrators inside their reach."""
    role = network_role(user)
    if role in PROGRAMME_STAFF_ROLES:
        return True
    if role == User.HUB_ADMIN:
        return organisations_in_reach(user).filter(pk=organisation.pk).exists()
    return False


def can_place_partner_in(user, country):
    """Whether this person may create a partner in, or move one to, a country."""
    role = network_role(user)
    if role in PROGRAMME_STAFF_ROLES:
        return True
    return role == User.HUB_ADMIN and country in assigned_countries(user)


def is_portal_hub_admin(user):
    """Hub administrators enter the location portal as staff, inside their reach only."""
    return network_role(user) == User.HUB_ADMIN


def result_cohorts(user):
    """Cohorts whose results someone may see: network reach for network staff, the portal rules otherwise."""
    if network_role(user) in (User.LEADER, User.HUB_ADMIN):
        return cohorts_in_reach(user)
    return cohorts_for(user)


# ==================== WHAT EACH NETWORK ROLE MAY DO ====================

def can_edit_network(user):
    """Change partners, contacts, licences (short of activating them) and locations."""
    return is_programme_staff(user) or network_role(user) == User.HUB_ADMIN


def can_log_network(user):
    """Add history notes and set the next step on a partnership."""
    return can_edit_network(user) or network_role(user) == User.AMBASSADOR


def can_add_partner(user):
    return can_edit_network(user) or network_role(user) == User.AMBASSADOR


def can_manage_licences(user):
    """Activate, suspend, terminate and delete licences: contracts stay with programme staff."""
    return is_programme_staff(user)


def can_enter_portal(user):
    return is_programme_staff(user) or is_portal_hub_admin(user)
