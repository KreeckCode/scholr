"""
Partner pipeline: METP's six launch steps, with gates backed by real records.

Moves:
  forward one step              always allowed
  skip forward or step back     programme staff only, with a note
  pause                         from any forward stage; resume returns to the stage it left
  lost                          from any stage, with a reason; reopen returns to enquiry
Gates (checked for every gated stage a forward move passes through):
  admin_training                a draft or active licence
  facilitator_training          an active coordinator with a training date
  launched                      an active licence, an active location and a running cohort
"""

import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from partners.exceptions import GateNotMet, InvalidTransition
from partners.models import (
    FORWARD_STAGES,
    Cohort,
    Licence,
    LocationMembership,
    PartnerContact,
    PartnerOrganisation,
    Partnership,
    PartnershipEvent,
    Stage,
    TrainingLocation,
)

from .scope import is_programme_staff

logger = logging.getLogger(__name__)


# ==================== GATES ====================

def _gate_admin_training(organisation, today):
    return organisation.licences.filter(status__in=[Licence.Status.DRAFT, Licence.Status.ACTIVE]).exists()


def _gate_facilitator_training(organisation, today):
    return LocationMembership.objects.filter(
        location__organisation=organisation,
        role=LocationMembership.Role.COORDINATOR,
        is_active=True,
        trained_on__isnull=False,
    ).exists()


def _gate_launched(organisation, today):
    has_live_licence = organisation.licences.filter(
        status=Licence.Status.ACTIVE, starts_on__lte=today, ends_on__gte=today,
    ).exists()
    has_location = organisation.locations.filter(status=TrainingLocation.Status.ACTIVE).exists()
    has_cohort = Cohort.objects.filter(
        location__organisation=organisation, status=Cohort.Status.RUNNING,
    ).exists()
    return has_live_licence and has_location and has_cohort


GATES = {
    Stage.ADMIN_TRAINING: (
        _gate_admin_training,
        'Draft a licence for this partner before admin training.',
    ),
    Stage.FACILITATOR_TRAINING: (
        _gate_facilitator_training,
        'Add a coordinator with a METP 100a training date before facilitator training.',
    ),
    Stage.LAUNCHED: (
        _gate_launched,
        'A partner launches with an active licence, an active location and a running cohort.',
    ),
}


def unmet_gates(partnership, to_stage, today=None):
    """Messages for every gate between the current stage and to_stage that is not yet met."""
    today = today or timezone.localdate()
    if to_stage not in FORWARD_STAGES:
        return []
    start = partnership.stage_index
    if start is None:
        start = -1
    target = FORWARD_STAGES.index(to_stage)
    messages = []
    for stage in FORWARD_STAGES[start + 1:target + 1]:
        gate = GATES.get(stage)
        if gate and not gate[0](partnership.organisation, today):
            messages.append(gate[1])
    return messages


# ==================== MOVES ====================

def allowed_targets(partnership, actor):
    """Stages offered in the move form for this partnership and actor."""
    current = partnership.stage
    if current == Stage.LOST:
        return [Stage.ENQUIRY]
    if current == Stage.PAUSED:
        targets = [partnership.stage_before_pause] if partnership.stage_before_pause else []
        return targets + [Stage.LOST]
    index = partnership.stage_index
    staff = is_programme_staff(actor)
    targets = []
    for position, stage in enumerate(FORWARD_STAGES):
        if position == index:
            continue
        if position == index + 1 or staff:
            targets.append(stage)
    return targets + [Stage.PAUSED, Stage.LOST]


@transaction.atomic
def move(partnership, to_stage, actor, note='', today=None, system=False):
    """
    Move a partnership to another stage and record it.
    `system=True` is for automatic moves (a first cohort starting); it skips the staff-only
    rule for skipping stages but never skips gates.
    """
    today = today or timezone.localdate()
    note = (note or '').strip()
    partnership = (
        Partnership.objects.select_for_update(of=('self',))
        .select_related('organisation').get(pk=partnership.pk)
    )
    current = partnership.stage

    if to_stage not in Stage.values:
        raise InvalidTransition('Unknown stage.')
    if to_stage == current:
        raise InvalidTransition('The partnership is already at that stage.')

    if to_stage == Stage.LOST:
        if not note:
            raise InvalidTransition('Give a reason when marking a partnership as lost.')
        partnership.lost_reason = note
    elif to_stage == Stage.PAUSED:
        if current == Stage.LOST:
            raise InvalidTransition('Reopen a lost partnership before pausing it.')
        partnership.stage_before_pause = current
    elif current == Stage.PAUSED:
        if to_stage != partnership.stage_before_pause:
            raise InvalidTransition('A paused partnership resumes at the stage it left.')
        partnership.stage_before_pause = ''
    elif current == Stage.LOST:
        if to_stage != Stage.ENQUIRY:
            raise InvalidTransition('A lost partnership can only be reopened as an enquiry.')
        partnership.lost_reason = ''
    else:
        step = FORWARD_STAGES.index(to_stage) - FORWARD_STAGES.index(current)
        if step != 1:
            if not (system or is_programme_staff(actor)):
                raise InvalidTransition('Only programme staff can skip stages or move backwards.')
            if not note and not system:
                raise InvalidTransition('Add a note explaining why this stage is being skipped or reversed.')
        if step > 0:
            missing = unmet_gates(partnership, to_stage, today)
            if missing:
                raise GateNotMet(' '.join(missing))

    partnership.stage = to_stage
    partnership.stage_changed_at = timezone.now()
    partnership.save()
    PartnershipEvent.objects.create(
        partnership=partnership,
        kind=PartnershipEvent.Kind.STAGE,
        from_stage=current,
        to_stage=to_stage,
        body=note,
        actor=actor,
    )
    logger.info('Partnership %s moved %s -> %s', partnership.pk, current, to_stage)
    return partnership


def add_event(partnership, actor, kind, body):
    body = (body or '').strip()
    if not body:
        raise InvalidTransition('A note cannot be empty.')
    if kind not in {PartnershipEvent.Kind.NOTE, PartnershipEvent.Kind.CALL,
                    PartnershipEvent.Kind.EMAIL, PartnershipEvent.Kind.DOCUMENT}:
        raise InvalidTransition('Unknown note type.')
    return PartnershipEvent.objects.create(partnership=partnership, kind=kind, body=body, actor=actor)


def mark_launched_if_first_cohort(cohort, actor, today=None):
    """Called when a cohort starts. Moves the partner to launched when every gate is met."""
    partnership = Partnership.objects.filter(organisation_id=cohort.location.organisation_id).first()
    if partnership is None or partnership.stage in (Stage.LAUNCHED, Stage.LOST, Stage.PAUSED):
        return False
    try:
        move(partnership, Stage.LAUNCHED, actor, note=f'First cohort started: {cohort.name}.',
             today=today, system=True)
    except (GateNotMet, InvalidTransition) as exc:
        logger.info('Partnership %s not launched automatically: %s', partnership.pk, exc)
        return False
    return True


def ensure_partnership(organisation, actor=None, source=Partnership.Source.STAFF):
    partnership, created = Partnership.objects.get_or_create(
        organisation=organisation, defaults={'source': source, 'owner': actor if is_programme_staff(actor) else None},
    )
    return partnership


# ==================== WEBSITE ENQUIRIES ====================

def find_existing_organisation(name, country, email):
    """A likely match: the same contact email anywhere, or the same name in the same country."""
    if email:
        contact = (
            PartnerContact.objects.filter(email__iexact=email)
            .select_related('organisation').order_by('-is_primary', 'id').first()
        )
        if contact:
            return contact.organisation
    return (
        PartnerOrganisation.objects.filter(Q(name__iexact=name.strip()), country=country)
        .order_by('id').first()
    )


@transaction.atomic
def intake_enquiry(data):
    """
    Turn a website enquiry into an organisation, contact and partnership, or add it to an
    existing partner if one matches. Returns (partnership, created).
    """
    email = (data.get('email') or '').strip().lower()
    message = (data.get('message') or '').strip()
    header = [
        f"From: {data['contact_name']}" + (f" ({data['role_title']})" if data.get('role_title') else ''),
        f'Email: {email}' if email else '',
        f"Phone: {data['phone']}" if data.get('phone') else '',
        f"Expected participants: {data['expected_participants']}" if data.get('expected_participants') else '',
    ]
    summary = '\n'.join(line for line in header if line)
    if message:
        summary = f'{summary}\n\n{message}'

    organisation = find_existing_organisation(data['organisation_name'], data['country'], email)
    created = organisation is None
    if created:
        organisation = PartnerOrganisation.objects.create(
            name=data['organisation_name'].strip(),
            kind=data['kind'],
            country=data['country'],
            city=(data.get('city') or '').strip(),
        )
        PartnerContact.objects.create(
            organisation=organisation,
            name=data['contact_name'].strip(),
            role_title=(data.get('role_title') or '').strip(),
            email=email,
            phone=(data.get('phone') or '').strip(),
            is_primary=True,
        )
    elif email and not organisation.contacts.filter(email__iexact=email).exists():
        PartnerContact.objects.create(
            organisation=organisation,
            name=data['contact_name'].strip(),
            role_title=(data.get('role_title') or '').strip(),
            email=email,
            phone=(data.get('phone') or '').strip(),
            is_primary=not organisation.contacts.filter(is_primary=True).exists(),
        )

    partnership, partnership_created = Partnership.objects.get_or_create(
        organisation=organisation,
        defaults={
            'source': Partnership.Source.WEB_ENQUIRY,
            'expected_participants': data.get('expected_participants'),
        },
    )
    PartnershipEvent.objects.create(
        partnership=partnership, kind=PartnershipEvent.Kind.ENQUIRY, body=summary,
    )
    return partnership, created
