import uuid
from datetime import timedelta

from django.utils import timezone

from partners.models import (
    CohortMembership,
    CourseResult,
    LocationMembership,
    Participant,
    SessionAttendance,
    SyncOperation,
)
from partners.services import roster, sync

from .base import PartnersTestCase
from .helpers import add_member, enrol, make_cohort, make_location, make_organisation, make_session, seed_courses


class SyncTests(PartnersTestCase):
    def setUp(self):
        self.location = make_location()
        self.coordinator = add_member(self.location)
        self.facilitator = add_member(self.location, LocationMembership.Role.FACILITATOR)
        self.courses = seed_courses(2)
        self.cohort = make_cohort(self.location, facilitators=[self.facilitator])
        self.membership = enrol(self.cohort)
        self.session = make_session(self.cohort, self.courses[0])

    def op(self, kind, payload, **extra):
        return {'op_id': str(uuid.uuid4()), 'kind': kind, 'payload': payload,
                'recorded_at': timezone.now().isoformat(), **extra}

    def attendance_op(self, status='present', **extra):
        return self.op('attendance.set', {
            'session': str(self.session.public_id), 'membership': str(self.membership.public_id), 'status': status,
        }, **extra)

    def test_pack_holds_the_cohort_and_hides_names_when_sensitive(self):
        pack = sync.build_pack(self.cohort, self.facilitator)
        self.assertEqual(pack['cohort']['id'], str(self.cohort.public_id))
        self.assertTrue(pack['permissions']['record'])
        self.assertEqual(pack['memberships'][0]['name'], self.membership.participant.full_name)
        self.assertNotIn('phone', pack['memberships'][0])

        self.location.is_sensitive = True
        self.location.save()
        pack = sync.build_pack(self.cohort, self.facilitator)
        self.assertEqual(pack['memberships'][0]['name'], self.membership.participant.guarded_name)

    def test_apply_and_resend_returns_duplicate(self):
        operation = self.attendance_op()
        first = sync.apply_batch(self.facilitator, 'device-1', [operation])[0]
        second = sync.apply_batch(self.facilitator, 'device-1', [operation])[0]
        self.assertEqual(first['result'], 'applied')
        self.assertEqual(second['result'], 'duplicate')
        self.assertEqual(second['original'], 'applied')
        self.assertEqual(SessionAttendance.objects.count(), 1)
        self.assertEqual(SyncOperation.objects.count(), 1)

    def test_another_user_cannot_reuse_an_op_id(self):
        operation = self.attendance_op()
        sync.apply_batch(self.facilitator, 'device-1', [operation])
        reply = sync.apply_batch(self.coordinator, 'device-2', [operation])[0]
        self.assertEqual(reply['result'], 'rejected')

    def test_out_of_scope_session_is_rejected_and_stored(self):
        other_cohort = make_cohort(make_location(make_organisation()))
        other_session = make_session(other_cohort, self.courses[0])
        operation = self.op('attendance.set', {
            'session': str(other_session.public_id), 'membership': str(self.membership.public_id), 'status': 'present',
        })
        reply = sync.apply_batch(self.facilitator, 'd', [operation])[0]
        self.assertEqual(reply['result'], 'rejected')
        self.assertEqual(SyncOperation.objects.get().result, SyncOperation.Result.REJECTED)
        self.assertEqual(sync.apply_batch(self.facilitator, 'd', [operation])[0]['original'], 'rejected')

    def test_bad_shapes_are_rejected_without_raising(self):
        replies = sync.apply_batch(self.facilitator, 'd', [
            'not an object',
            {'op_id': 'nope'},
            self.op('unknown.kind', {}),
            self.attendance_op(status='asleep'),
        ])
        self.assertEqual([reply['result'] for reply in replies], ['rejected'] * 4)

    def test_future_device_clock_is_clamped(self):
        future = (timezone.now() + timedelta(days=3)).isoformat()
        sync.apply_batch(self.facilitator, 'd', [self.attendance_op(recorded_at=future)])
        mark = SessionAttendance.objects.get()
        self.assertLess(mark.recorded_at, timezone.now() + timedelta(minutes=11))

    def test_offline_participant_then_attendance_by_client_reference(self):
        client_uuid = str(uuid.uuid4())
        create = self.op('participant.create', {
            'client_uuid': client_uuid, 'cohort': str(self.cohort.public_id),
            'first_name': 'Lerato', 'last_name': 'Nkosi', 'phone': '+27731234567',
        })
        mark = self.op('attendance.set', {
            'session': str(self.session.public_id), 'membership': f'client:{client_uuid}', 'status': 'late',
        })
        replies = sync.apply_batch(self.facilitator, 'd', [create, mark])
        self.assertEqual([reply['result'] for reply in replies], ['applied', 'applied'])
        participant = Participant.objects.get(client_uuid=client_uuid)
        self.assertEqual(replies[0]['participant'], str(participant.public_id))
        membership = CohortMembership.objects.get(participant=participant)
        self.assertEqual(SessionAttendance.objects.get(membership=membership).status, 'late')

    def test_result_conflict_is_stored_and_resolvable(self):
        course = self.courses[0]
        first = roster.record_result(self.membership, course, 60, self.coordinator)
        roster.record_result(self.membership, course, 65, self.coordinator, base_version=first.version)
        operation = self.op('result.set', {
            'membership': str(self.membership.public_id), 'course': course.code,
            'exam_score': 90, 'base_version': first.version,
        })
        reply = sync.apply_batch(self.facilitator, 'd', [operation])[0]
        self.assertEqual(reply['result'], 'conflict')
        conflict = sync.open_conflicts(self.location).get()
        self.assertEqual(conflict.detail['server']['exam_score'], '65.00')

        sync.resolve_conflict(conflict, self.coordinator, accept_incoming=True)
        self.assertEqual(CourseResult.objects.get().exam_score, 90)
        self.assertFalse(sync.open_conflicts(self.location).exists())

    def test_result_reply_carries_the_new_version(self):
        operation = self.op('result.set', {
            'membership': str(self.membership.public_id), 'course': self.courses[0].code,
            'exam_score': '72.5', 'base_version': None,
        })
        reply = sync.apply_batch(self.facilitator, 'd', [operation])[0]
        self.assertEqual(reply['result'], 'applied')
        self.assertEqual(reply['version'], CourseResult.objects.get().version)

    def test_batch_size_is_limited(self):
        from partners.exceptions import RosterError
        with self.assertRaises(RosterError):
            sync.apply_batch(self.facilitator, 'd', [self.attendance_op() for _ in range(sync.MAX_BATCH + 1)])
