"""
Cohorts, participants, sessions, attendance and results at a training location.
Both the portal and the offline sync call these functions, so the rules are identical
whether a register was taken online, offline or from paper.
"""

import logging
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from partners.exceptions import RosterError, SyncConflict
from partners.models import (
    Cohort,
    CohortMembership,
    CohortSession,
    CourseResult,
    LocationMembership,
    Participant,
    ProgrammeCourse,
    SessionAttendance,
)

from . import licences, pipeline, scope

logger = logging.getLogger(__name__)


def _require(condition, message='You do not have permission to do that.'):
    if not condition:
        raise PermissionDenied(message)


# ==================== COHORTS ====================

def validate_facilitators(location, users):
    """Every facilitator must hold an active membership at the cohort's location."""
    user_ids = {user.pk for user in users}
    if not user_ids:
        return
    eligible = set(
        LocationMembership.objects.filter(
            location=location, user_id__in=user_ids, is_active=True,
        ).values_list('user_id', flat=True)
    )
    missing = user_ids - eligible
    if missing:
        raise RosterError('Facilitators must be active members of this location.')


@transaction.atomic
def create_cohort(cohort, actor, facilitators=(), today=None):
    """Save a cohort built from a form, after checking the licence and the team."""
    _require(scope.can_manage_location(actor, cohort.location))
    licences.ensure(
        licences.allows_new_cohorts(cohort.location.licence, today),
        'The licence for this location is not active, so new cohorts cannot be opened.',
    )
    validate_facilitators(cohort.location, facilitators)
    cohort.created_by = actor
    cohort.status = Cohort.Status.PLANNED
    cohort.save()
    cohort.facilitators.set(facilitators)
    return cohort


@transaction.atomic
def update_cohort(cohort, actor, facilitators=None):
    _require(scope.can_manage_location(actor, cohort.location))
    if not cohort.is_open:
        raise RosterError('A completed or cancelled cohort cannot be changed.')
    cohort.save()
    if facilitators is not None:
        validate_facilitators(cohort.location, facilitators)
        cohort.facilitators.set(facilitators)
    return cohort


@transaction.atomic
def change_cohort_status(cohort, to_status, actor, today=None):
    """planned -> running -> completed; planned or running -> cancelled."""
    _require(scope.can_manage_location(actor, cohort.location))
    cohort = Cohort.objects.select_for_update(of=('self',)).select_related('location__licence').get(pk=cohort.pk)
    allowed = {
        Cohort.Status.PLANNED: {Cohort.Status.RUNNING, Cohort.Status.CANCELLED},
        Cohort.Status.RUNNING: {Cohort.Status.COMPLETED, Cohort.Status.CANCELLED},
    }
    if to_status not in allowed.get(cohort.status, set()):
        raise RosterError(
            f'A {cohort.get_status_display().lower()} cohort cannot be moved to {to_status}.'
        )
    if to_status == Cohort.Status.RUNNING:
        licences.ensure(
            licences.allows_new_cohorts(cohort.location.licence, today),
            'The licence for this location is not active, so this cohort cannot start.',
        )
        if not cohort.facilitators.exists():
            raise RosterError('Assign at least one facilitator before starting the cohort.')
    cohort.status = to_status
    if to_status in (Cohort.Status.COMPLETED, Cohort.Status.CANCELLED) and not cohort.ends_on:
        cohort.ends_on = today or timezone.localdate()
    cohort.save(update_fields=['status', 'ends_on', 'updated_at'])
    if to_status == Cohort.Status.RUNNING:
        location = cohort.location
        if location.status == location.Status.PREPARING:
            location.status = location.Status.ACTIVE
            location.save(update_fields=['status', 'updated_at'])
        transaction.on_commit(lambda: pipeline.mark_launched_if_first_cohort(cohort, actor, today))
    return cohort


# ==================== PARTICIPANTS ====================

def possible_duplicates(location, first_name, last_name, phone='', exclude=None):
    """Participants at the same location who share a phone, or the same full name."""
    query = Q(first_name__iexact=first_name.strip(), last_name__iexact=last_name.strip())
    if phone:
        query |= Q(phone=phone.strip())
    queryset = Participant.objects.filter(query, home_location=location)
    if exclude is not None:
        queryset = queryset.exclude(pk=exclude.pk)
    return queryset.order_by('last_name', 'first_name')[:5]


def register_participant(participant, actor, today=None):
    """Save a participant built from a form at its home location."""
    location = participant.home_location
    _require(
        scope.can_manage_location(actor, location)
        or LocationMembership.objects.filter(
            user=actor, location=location, role=LocationMembership.Role.FACILITATOR, is_active=True,
        ).exists()
    )
    licences.ensure(
        licences.allows_new_participants(location.licence, today),
        'The licence for this location is not active, so new participants cannot be added.',
    )
    participant.created_by = actor
    if not participant.country:
        participant.country = location.country
    participant.save()
    return participant


def update_participant(participant, actor):
    _require(scope.can_manage_location(actor, participant.home_location))
    participant.save()
    return participant


@transaction.atomic
def enrol(cohort, participant, actor, today=None):
    """Place a participant in a cohort. Returns (membership, created)."""
    _require(scope.can_record_for_cohort(actor, cohort))
    if participant.home_location_id != cohort.location_id:
        raise RosterError('Participants can only join cohorts at their own location.')
    if not cohort.is_open:
        raise RosterError('Participants cannot join a completed or cancelled cohort.')
    licences.ensure(
        licences.allows_new_participants(cohort.location.licence, today),
        'The licence for this location is not active, so participants cannot be enrolled.',
    )
    membership, created = CohortMembership.objects.get_or_create(cohort=cohort, participant=participant)
    if not created and membership.status == CohortMembership.Status.WITHDRAWN:
        membership.status = CohortMembership.Status.ENROLLED
        membership.withdrawn_on = None
        membership.save(update_fields=['status', 'withdrawn_on', 'updated_at'])
    return membership, created


@transaction.atomic
def withdraw(membership, actor, today=None):
    _require(scope.can_manage_location(actor, membership.cohort.location))
    if membership.status != CohortMembership.Status.ENROLLED:
        raise RosterError('Only an enrolled participant can be withdrawn.')
    membership.status = CohortMembership.Status.WITHDRAWN
    membership.withdrawn_on = today or timezone.localdate()
    membership.save(update_fields=['status', 'withdrawn_on', 'updated_at'])
    if membership.has_online_access:
        from .access import withdraw_online_access
        withdraw_online_access(membership, actor)
    return membership


# ==================== SESSIONS ====================

def create_session(session, actor):
    cohort = session.cohort
    _require(scope.can_manage_location(actor, cohort.location))
    if not cohort.is_open:
        raise RosterError('Sessions cannot be added to a completed or cancelled cohort.')
    if session.facilitator_id:
        validate_facilitators(cohort.location, [session.facilitator])
    session.created_by = actor
    session.save()
    return session


def update_session(session, actor):
    _require(scope.can_manage_location(actor, session.cohort.location))
    if session.facilitator_id:
        validate_facilitators(session.cohort.location, [session.facilitator])
    session.save()
    if session.status == CohortSession.Status.CANCELLED:
        refresh_results_for_course(session.cohort, session.course)
    return session


@transaction.atomic
def delete_session(session, actor):
    _require(scope.can_manage_location(actor, session.cohort.location))
    if session.attendance.exists():
        raise RosterError('This session has a register. Cancel it instead of deleting it.')
    session.delete()


# ==================== ATTENDANCE ====================

def _ensure_recordable(actor, cohort, today=None):
    _require(scope.can_record_for_cohort(actor, cohort))
    licences.ensure(
        licences.allows_recording(cohort.location.licence, cohort, today),
        'The licence for this location no longer allows records to be changed for this cohort.',
    )


@transaction.atomic
def set_attendance(session, membership, status, actor, recorded_at=None, today=None):
    """
    Upsert one register mark with last-write-wins on recorded_at.
    Returns (record, outcome) where outcome is 'applied' or 'superseded'.
    """
    if status not in SessionAttendance.Status.values:
        raise RosterError('Unknown attendance status.')
    if membership.cohort_id != session.cohort_id:
        raise RosterError('That participant is not in this session’s cohort.')
    if session.status == CohortSession.Status.CANCELLED:
        raise RosterError('A cancelled session has no register.')
    if membership.status == CohortMembership.Status.WITHDRAWN:
        raise RosterError('That participant has withdrawn from the cohort.')
    _ensure_recordable(actor, session.cohort, today)

    recorded_at = recorded_at or timezone.now()
    record = (
        SessionAttendance.objects.select_for_update()
        .filter(session=session, membership=membership).first()
    )
    if record and record.recorded_at > recorded_at:
        return record, 'superseded'
    if record is None:
        record = SessionAttendance(session=session, membership=membership)
    record.status = status
    record.recorded_by = actor
    record.recorded_at = recorded_at
    record.received_at = timezone.now()
    record.save()

    if session.status == CohortSession.Status.PLANNED:
        session.status = CohortSession.Status.HELD
        session.save(update_fields=['status', 'updated_at'])
    refresh_result(membership, session.course)
    return record, 'applied'


@transaction.atomic
def save_register(session, marks, actor, today=None):
    """Bulk entry from the online or paper register: {membership: status}."""
    outcomes = {}
    for membership, status in marks.items():
        _record, outcome = set_attendance(session, membership, status, actor, today=today)
        outcomes[membership.pk] = outcome
    return outcomes


def attended_hours(membership, course):
    total = (
        SessionAttendance.objects.filter(
            membership=membership,
            session__course=course,
            status__in=SessionAttendance.ATTENDED,
        )
        .exclude(session__status=CohortSession.Status.CANCELLED)
        .aggregate(total=Sum('session__hours'))['total']
    )
    return total or Decimal('0')


def attended_hours_by_membership(memberships, course):
    """{membership.pk: hours} for one course in a single query."""
    rows = (
        SessionAttendance.objects.filter(
            membership__in=memberships, session__course=course, status__in=SessionAttendance.ATTENDED,
        )
        .exclude(session__status=CohortSession.Status.CANCELLED)
        .values('membership_id')
        .annotate(total=Sum('session__hours'))
    )
    totals = {row['membership_id']: row['total'] for row in rows}
    return {membership.pk: totals.get(membership.pk) or Decimal('0') for membership in memberships}


def required_hours(course):
    percent = Decimal(settings.PARTNERS_MIN_ATTENDANCE_PERCENT) / Decimal('100')
    return (course.classroom_hours * percent).quantize(Decimal('0.1'))


# ==================== RESULTS ====================

def _status_for(membership, course, exam_score):
    if exam_score is None:
        return CourseResult.Status.IN_PROGRESS
    if Decimal(exam_score) < Decimal(settings.PARTNERS_PASS_MARK):
        return CourseResult.Status.FAILED
    if attended_hours(membership, course) < required_hours(course):
        return CourseResult.Status.INCOMPLETE
    return CourseResult.Status.PASSED


def refresh_result(membership, course):
    """Recalculate a result's status after attendance changed. No-op when no exam score exists."""
    result = CourseResult.objects.filter(membership=membership, course=course).first()
    if result is None or result.exam_score is None:
        return result
    new_status = _status_for(membership, course, result.exam_score)
    if new_status != result.status:
        result.status = new_status
        result.version += 1
        result.save(update_fields=['status', 'version', 'updated_at'])
        refresh_completion(membership)
    return result


def refresh_results_for_course(cohort, course):
    for membership in cohort.memberships.all():
        refresh_result(membership, course)


@transaction.atomic
def record_result(membership, course, exam_score, actor, base_version=None, recorded_at=None, today=None):
    """
    Set a participant's exam score for a course.
    base_version is the version the editor last saw; a mismatch means someone else
    changed it in the meantime and raises SyncConflict instead of overwriting.
    """
    if not course.is_active:
        raise RosterError('That course is not active.')
    if membership.status == CohortMembership.Status.WITHDRAWN:
        raise RosterError('That participant has withdrawn from the cohort.')
    if exam_score is not None:
        exam_score = Decimal(str(exam_score))
        if not (Decimal('0') <= exam_score <= Decimal('100')):
            raise RosterError('Exam scores are percentages between 0 and 100.')
    _ensure_recordable(actor, membership.cohort, today)

    result = (
        CourseResult.objects.select_for_update()
        .filter(membership=membership, course=course).first()
    )
    if result is not None:
        if base_version is not None and int(base_version) != result.version:
            raise SyncConflict(
                'This result was changed by someone else after your copy was taken.',
                server_state={'exam_score': _decimal_or_none(result.exam_score), 'version': result.version},
            )
        if result.is_signed_off:
            raise RosterError('This result has been signed off and can no longer be changed.')
        result.version += 1
    else:
        result = CourseResult(membership=membership, course=course)

    result.exam_score = exam_score
    result.status = _status_for(membership, course, exam_score)
    result.recorded_by = actor
    result.recorded_at = recorded_at or timezone.now()
    result.received_at = timezone.now()
    result.save()
    refresh_completion(membership, today)
    return result


@transaction.atomic
def sign_off_result(result, actor):
    _require(scope.can_manage_location(actor, result.membership.cohort.location))
    if result.is_signed_off:
        raise RosterError('This result is already signed off.')
    if result.status not in (CourseResult.Status.PASSED, CourseResult.Status.FAILED,
                             CourseResult.Status.INCOMPLETE):
        raise RosterError('Only a result with an exam score can be signed off.')
    result.signed_off_by = actor
    result.signed_off_at = timezone.now()
    result.version += 1
    result.save(update_fields=['signed_off_by', 'signed_off_at', 'version', 'updated_at'])
    return result


def refresh_completion(membership, today=None):
    """A membership completes when every active programme course has a passed result."""
    required = ProgrammeCourse.objects.filter(is_active=True).count()
    if required == 0:
        return membership
    passed = membership.results.filter(status=CourseResult.Status.PASSED, course__is_active=True).count()
    if passed >= required and membership.status == CohortMembership.Status.ENROLLED:
        membership.status = CohortMembership.Status.COMPLETED
        membership.completed_on = today or timezone.localdate()
        membership.save(update_fields=['status', 'completed_on', 'updated_at'])
    elif passed < required and membership.status == CohortMembership.Status.COMPLETED:
        membership.status = CohortMembership.Status.ENROLLED
        membership.completed_on = None
        membership.save(update_fields=['status', 'completed_on', 'updated_at'])
    return membership


def _decimal_or_none(value):
    return None if value is None else str(value)
