"""Organisations, their contacts and their training locations (programme staff only)."""

from django.core.exceptions import PermissionDenied
from django.db import transaction

from partners.exceptions import LicenceStateError, RosterError
from partners.models import Licence, PartnerContact, PartnerOrganisation, TrainingLocation

from . import pipeline, scope


def _require_editor(actor, organisation):
    """Programme staff anywhere; hub administrators only for partners in their reach."""
    if not scope.can_change_organisation(actor, organisation):
        raise PermissionDenied


def _require_creator(actor, organisation):
    """Hub administrators add partners in their countries; ambassadors add partners they then look after."""
    if not (scope.can_place_partner_in(actor, organisation.country) or scope.network_role(actor) == 'ambassador'):
        raise PermissionDenied


@transaction.atomic
def create_organisation(organisation, actor):
    _require_creator(actor, organisation)
    organisation.created_by = actor
    organisation.save()
    pipeline.ensure_partnership(organisation, actor)
    return organisation


@transaction.atomic
def save_contact(contact, actor):
    """Save a contact; making one primary demotes the previous primary in the same transaction."""
    _require_editor(actor, contact.organisation)
    if contact.is_primary:
        PartnerContact.objects.filter(
            organisation=contact.organisation, is_primary=True,
        ).exclude(pk=contact.pk).update(is_primary=False)
    contact.save()
    return contact


@transaction.atomic
def set_archived(organisation, actor, archived):
    _require_editor(actor, organisation)
    if archived and organisation.licences.filter(status=Licence.Status.ACTIVE).exists():
        raise LicenceStateError('Terminate or let the active licence expire before archiving this partner.')
    organisation.is_archived = archived
    organisation.save(update_fields=['is_archived', 'updated_at'])
    return organisation


@transaction.atomic
def save_location(location, actor):
    """Create or update a training location under its organisation's licence."""
    _require_editor(actor, location.organisation)
    if location.licence.organisation_id != location.organisation_id:
        raise RosterError('The licence must belong to the same partner as the location.')
    limit = location.licence.max_locations
    if limit is not None:
        others = TrainingLocation.objects.filter(licence=location.licence).exclude(
            status=TrainingLocation.Status.CLOSED,
        ).exclude(pk=location.pk).count()
        if location.status != TrainingLocation.Status.CLOSED and others >= limit:
            raise LicenceStateError(f'Licence {location.licence.number} allows {limit} location(s).')
    if not location.pk:
        location.created_by = actor
    location.save()
    return location


def active_organisations():
    return PartnerOrganisation.objects.filter(is_archived=False)
