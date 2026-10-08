"""
Offline packs and the sync endpoint's operation handler.

A device downloads a pack for a cohort, works offline, and later sends a batch of
operations. Each operation is applied in its own transaction and stored once by its
op_id, so a resend returns the stored answer instead of applying twice.
"""

import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import (
    MultipleObjectsReturned,
    ObjectDoesNotExist,
    PermissionDenied,
    ValidationError,
)
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from partners.exceptions import PartnersError, RosterError, SyncConflict
from partners.models import (
    CohortMembership,
    CohortSession,
    CourseResult,
    Participant,
    ProgrammeCourse,
    SessionAttendance,
    SyncOperation,
)

from . import licences, roster, scope

logger = logging.getLogger(__name__)

PACK_VERSION = 1
MAX_BATCH = 100
FUTURE_TOLERANCE = timedelta(minutes=10)

# I'm not storing these; the device keeps the operation and sends it again later
RETRY = 'retry'

Result = SyncOperation.Result
Kind = SyncOperation.Kind


# ==================== PACKS ====================

def build_pack(cohort, user, today=None):
    """Everything a facilitator needs to take registers and record results for one cohort."""
    today = today or timezone.localdate()
    now = timezone.now()
    location = cohort.location
    hide = location.hides_identities

    memberships = list(
        cohort.memberships.exclude(status=CohortMembership.Status.WITHDRAWN)
        .select_related('participant').order_by('participant__last_name', 'participant__first_name')
    )
    sessions = list(
        cohort.sessions.exclude(status=CohortSession.Status.CANCELLED).select_related('course')
    )
    courses = list(ProgrammeCourse.objects.filter(is_active=True))
    attendance = SessionAttendance.objects.filter(
        session__in=sessions, membership__in=memberships,
    ).values('session__public_id', 'membership__public_id', 'status')
    results = CourseResult.objects.filter(membership__in=memberships).values(
        'membership__public_id', 'course__code', 'exam_score', 'status', 'version', 'signed_off_at',
    )

    return {
        'pack_version': PACK_VERSION,
        'generated_at': now.isoformat(),
        'expires_at': (now + timedelta(days=settings.PARTNERS_PACK_DAYS)).isoformat(),
        'cohort': {
            'id': str(cohort.public_id),
            'name': cohort.name,
            'status': cohort.status,
            'delivery': cohort.delivery,
            'location': {'id': str(location.public_id), 'name': location.name},
        },
        'permissions': {
            'record': scope.can_record_for_cohort(user, cohort)
            and licences.allows_recording(location.licence, cohort, today),
            'add_participants': scope.can_record_for_cohort(user, cohort)
            and licences.allows_new_participants(location.licence, today),
        },
        'courses': [
            {'code': course.code, 'title': course.title, 'hours': str(course.classroom_hours)}
            for course in courses
        ],
        'sessions': [
            {
                'id': str(session.public_id),
                'course': session.course.code,
                'date': session.session_date.isoformat(),
                'hours': str(session.hours),
                'status': session.status,
            }
            for session in sessions
        ],
        'memberships': [
            {
                'id': str(membership.public_id),
                'name': membership.participant.guarded_name if hide else membership.participant.full_name,
                'status': membership.status,
            }
            for membership in memberships
        ],
        'attendance': [
            {
                'session': str(row['session__public_id']),
                'membership': str(row['membership__public_id']),
                'status': row['status'],
            }
            for row in attendance
        ],
        'results': [
            {
                'membership': str(row['membership__public_id']),
                'course': row['course__code'],
                'exam_score': None if row['exam_score'] is None else str(row['exam_score']),
                'status': row['status'],
                'version': row['version'],
                'signed_off': row['signed_off_at'] is not None,
            }
            for row in results
        ],
    }


# ==================== BATCH APPLY ====================

class OperationRejected(Exception):
    """The operation's shape is invalid; nothing was attempted."""


def _uuid(value, field):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise OperationRejected(f'{field} is not a valid identifier.')


def _recorded_at(value, now):
    if not value:
        return now
    parsed = parse_datetime(str(value))
    if parsed is None:
        raise OperationRejected('recorded_at is not a valid date and time.')
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    # I'm clamping future times so a fast device clock cannot win every last-write-wins comparison
    return min(parsed, now + FUTURE_TOLERANCE)


def _scoped_session(user, session_id):
    return (
        CohortSession.objects.select_related('cohort__location__licence', 'course')
        .get(public_id=_uuid(session_id, 'session'), cohort__in=scope.cohorts_for(user))
    )


def _scoped_membership(user, reference, cohort=None):
    """A membership by its public id, or by 'client:<uuid>' for a participant created offline."""
    reference = str(reference or '')
    queryset = CohortMembership.objects.select_related('cohort__location__licence', 'participant').filter(
        cohort__in=scope.cohorts_for(user),
    )
    if reference.startswith('client:'):
        client_uuid = _uuid(reference.split(':', 1)[1], 'membership')
        queryset = queryset.filter(participant__client_uuid=client_uuid)
        if cohort is not None:
            queryset = queryset.filter(cohort=cohort)
        return queryset.get()
    return queryset.get(public_id=_uuid(reference, 'membership'))


def _apply_attendance(user, payload, recorded_at):
    session = _scoped_session(user, payload.get('session'))
    membership = _scoped_membership(user, payload.get('membership'), cohort=session.cohort)
    status = payload.get('status')
    if status not in SessionAttendance.Status.values:
        raise OperationRejected('Unknown attendance status.')
    record, outcome = roster.set_attendance(session, membership, status, user, recorded_at=recorded_at)
    location = session.cohort.location
    if outcome == 'superseded':
        return Result.SUPERSEDED, {'kept': record.status, 'incoming': status}, {}, location
    return Result.APPLIED, {}, {}, location


def _apply_result(user, payload, recorded_at):
    membership = _scoped_membership(user, payload.get('membership'))
    course = ProgrammeCourse.objects.get(code=str(payload.get('course') or ''))
    raw_score = payload.get('exam_score')
    try:
        score = None if raw_score in (None, '') else float(raw_score)
    except (TypeError, ValueError):
        raise OperationRejected('exam_score must be a number.')
    base_version = payload.get('base_version')
    if base_version is not None and not isinstance(base_version, int):
        raise OperationRejected('base_version must be a whole number.')
    location = membership.cohort.location
    try:
        result = roster.record_result(
            membership, course, score, user, base_version=base_version, recorded_at=recorded_at,
        )
    except SyncConflict as conflict:
        detail = {
            'membership': str(membership.public_id),
            'participant': membership.participant.full_name,
            'course': course.code,
            'incoming': {'exam_score': raw_score, 'base_version': base_version},
            'server': conflict.server_state,
        }
        return Result.CONFLICT, detail, {'message': conflict.message}, location
    return Result.APPLIED, {}, {'version': result.version, 'status': result.status}, location


def _apply_participant(user, payload, recorded_at):
    client_uuid = _uuid(payload.get('client_uuid'), 'client_uuid')
    cohort = scope.cohorts_for(user).select_related('location__licence').get(
        public_id=_uuid(payload.get('cohort'), 'cohort'),
    )
    existing = Participant.objects.filter(client_uuid=client_uuid).first()
    if existing is not None:
        if existing.home_location_id != cohort.location_id:
            raise OperationRejected('client_uuid is already in use.')
        participant = existing
    else:
        participant = Participant(
            home_location=cohort.location,
            first_name=str(payload.get('first_name') or '').strip()[:100],
            last_name=str(payload.get('last_name') or '').strip()[:100],
            phone=str(payload.get('phone') or '').strip()[:20],
            church_name=str(payload.get('church_name') or '').strip()[:200],
            client_uuid=client_uuid,
        )
        if not participant.first_name or not participant.last_name:
            raise OperationRejected('A first name and a last name are required.')
        participant.full_clean(exclude=['home_location', 'user', 'created_by', 'country'])
        roster.register_participant(participant, user)
    membership, _created = roster.enrol(cohort, participant, user)
    response = {
        'client_uuid': str(client_uuid),
        'participant': str(participant.public_id),
        'membership': str(membership.public_id),
    }
    return Result.APPLIED, {}, response, cohort.location


HANDLERS = {
    Kind.ATTENDANCE_SET: _apply_attendance,
    Kind.RESULT_SET: _apply_result,
    Kind.PARTICIPANT_CREATE: _apply_participant,
}


def _message_for(exc):
    if isinstance(exc, (ObjectDoesNotExist, MultipleObjectsReturned)):
        return 'Not found, or not available to you.'
    if isinstance(exc, PermissionDenied):
        return str(exc) or 'You do not have permission to do that.'
    if isinstance(exc, ValidationError):
        return '; '.join(exc.messages)
    if isinstance(exc, PartnersError):
        return exc.message
    return str(exc)


def _response(op_id, result, response, duplicate=False):
    body = {'op_id': str(op_id), 'result': 'duplicate' if duplicate else result}
    if duplicate:
        body['original'] = result
    body.update(response or {})
    return body


def apply_operation(user, device_id, operation, now=None):
    """Apply one operation and return its response dict. Never raises for bad input."""
    now = now or timezone.now()
    if not isinstance(operation, dict):
        return {'op_id': None, 'result': Result.REJECTED, 'message': 'Each operation must be an object.'}
    try:
        op_id = _uuid(operation.get('op_id'), 'op_id')
    except OperationRejected as exc:
        return {'op_id': operation.get('op_id'), 'result': Result.REJECTED, 'message': str(exc)}

    stored = SyncOperation.objects.filter(op_id=op_id).first()
    if stored is not None:
        if stored.user_id != user.pk:
            return {'op_id': str(op_id), 'result': Result.REJECTED, 'message': 'op_id is already in use.'}
        return _response(op_id, stored.result, stored.response, duplicate=True)

    kind = operation.get('kind')
    payload = operation.get('payload') if isinstance(operation.get('payload'), dict) else {}
    handler = HANDLERS.get(kind)

    result, detail, response, location, recorded_at = Result.REJECTED, {}, {}, None, None
    try:
        if handler is None:
            raise OperationRejected('Unknown operation kind.')
        recorded_at = _recorded_at(operation.get('recorded_at'), now)
        with transaction.atomic():
            result, detail, response, location = handler(user, payload, recorded_at)
            SyncOperation.objects.create(
                op_id=op_id, user=user, location=location, device_id=str(device_id or '')[:64],
                kind=kind, payload=payload, result=result, detail=detail, response=response,
                recorded_at=recorded_at,
            )
        return _response(op_id, result, response)
    except IntegrityError:
        # I'm treating a race on op_id or a unique row as retryable; the next send gets a definite answer
        stored = SyncOperation.objects.filter(op_id=op_id).first()
        if stored is not None and stored.user_id == user.pk:
            return _response(op_id, stored.result, stored.response, duplicate=True)
        return {'op_id': str(op_id), 'result': RETRY, 'message': 'Busy; this change will be sent again.'}
    except (OperationRejected, ObjectDoesNotExist, MultipleObjectsReturned, PermissionDenied,
            ValidationError, PartnersError) as exc:
        result, response = Result.REJECTED, {'message': _message_for(exc)}
    except Exception:
        logger.exception('Unexpected error applying sync operation %s', op_id)
        return {'op_id': str(op_id), 'result': RETRY, 'message': 'Server error; this change will be sent again.'}

    # I'm storing rejections too, so a resend gets the same answer instead of a second attempt
    try:
        with transaction.atomic():
            SyncOperation.objects.create(
                op_id=op_id, user=user, location=location, device_id=str(device_id or '')[:64],
                kind=kind if kind in Kind.values else '', payload=payload, result=result,
                detail=detail, response=response, recorded_at=recorded_at,
            )
    except IntegrityError:
        pass
    return _response(op_id, result, response)


def apply_batch(user, device_id, operations, now=None):
    if not isinstance(operations, list):
        raise RosterError('operations must be a list.')
    if len(operations) > MAX_BATCH:
        raise RosterError(f'Send at most {MAX_BATCH} operations at a time.')
    now = now or timezone.now()
    return [apply_operation(user, device_id, operation, now) for operation in operations]


# ==================== CONFLICTS ====================

def open_conflicts(location):
    return SyncOperation.objects.filter(
        location=location, result=Result.CONFLICT, resolved_at__isnull=True,
    ).select_related('user')


@transaction.atomic
def resolve_conflict(operation, actor, accept_incoming):
    operation = SyncOperation.objects.select_for_update(of=('self',)).select_related('location').get(pk=operation.pk)
    if not operation.is_open_conflict or operation.location is None:
        raise RosterError('This conflict has already been resolved.')
    if not scope.can_manage_location(actor, operation.location):
        raise PermissionDenied
    if accept_incoming:
        membership = CohortMembership.objects.get(
            public_id=_uuid(operation.payload.get('membership'), 'membership'),
            cohort__location=operation.location,
        )
        course = ProgrammeCourse.objects.get(code=operation.payload.get('course'))
        raw = operation.payload.get('exam_score')
        score = None if raw in (None, '') else float(raw)
        roster.record_result(membership, course, score, actor, base_version=None)
    operation.resolved_at = timezone.now()
    operation.resolved_by = actor
    operation.detail = {**operation.detail, 'resolution': 'incoming' if accept_incoming else 'server'}
    operation.save(update_fields=['resolved_at', 'resolved_by', 'detail'])
    return operation
