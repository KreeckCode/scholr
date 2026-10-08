from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import override_settings
from django.utils import timezone

from accounts.models import Order, StudentEnrollment, User
from partners.exceptions import AccessError, LicenceStateError, RosterError, SyncConflict
from partners.models import Cohort, CohortMembership, CourseResult, Licence, LocationMembership, SessionAttendance
from partners.services import access, accounts, roster

from .base import PartnersTestCase
from .helpers import (
    add_member,
    enrol,
    make_cohort,
    make_location,
    make_online_product,
    make_participant,
    make_session,
    make_staff,
    seed_courses,
)


@override_settings(PARTNERS_PASS_MARK=50, PARTNERS_MIN_ATTENDANCE_PERCENT=75)
class RosterTests(PartnersTestCase):
    def setUp(self):
        self.location = make_location()
        self.coordinator = add_member(self.location)
        self.facilitator = add_member(self.location, LocationMembership.Role.FACILITATOR)
        self.courses = seed_courses(2)
        self.cohort = make_cohort(self.location, facilitators=[self.facilitator])

    def test_create_cohort_needs_coordinator_and_live_licence(self):
        cohort = Cohort(location=self.location, name='Evening class', starts_on=timezone.localdate())
        with self.assertRaises(PermissionDenied):
            roster.create_cohort(cohort, self.facilitator)
        roster.create_cohort(cohort, self.coordinator, facilitators=[self.facilitator])
        self.assertEqual(cohort.status, Cohort.Status.PLANNED)

        self.location.licence.status = Licence.Status.SUSPENDED
        self.location.licence.save()
        with self.assertRaises(LicenceStateError):
            roster.create_cohort(Cohort(location=self.location, name='Another', starts_on=timezone.localdate()),
                                 self.coordinator)

    def test_facilitators_must_belong_to_the_location(self):
        outsider = add_member(make_location(), LocationMembership.Role.FACILITATOR)
        with self.assertRaises(RosterError):
            roster.validate_facilitators(self.location, [outsider])

    def test_starting_needs_a_facilitator_and_activates_the_location(self):
        cohort = make_cohort(self.location, status=Cohort.Status.PLANNED)
        with self.assertRaises(RosterError):
            roster.change_cohort_status(cohort, Cohort.Status.RUNNING, self.coordinator)
        cohort.facilitators.add(self.facilitator)
        with self.captureOnCommitCallbacks(execute=True):
            roster.change_cohort_status(cohort, Cohort.Status.RUNNING, self.coordinator)
        cohort.refresh_from_db()
        self.assertEqual(cohort.status, Cohort.Status.RUNNING)
        with self.assertRaises(RosterError):
            roster.change_cohort_status(cohort, Cohort.Status.PLANNED, self.coordinator)

    def test_unassigned_facilitator_cannot_record(self):
        other = add_member(self.location, LocationMembership.Role.FACILITATOR)
        membership = enrol(self.cohort)
        session = make_session(self.cohort, self.courses[0])
        with self.assertRaises(PermissionDenied):
            roster.set_attendance(session, membership, 'present', other)

    def test_attendance_is_last_write_wins(self):
        membership = enrol(self.cohort)
        session = make_session(self.cohort, self.courses[0])
        now = timezone.now()
        roster.set_attendance(session, membership, 'present', self.facilitator, recorded_at=now)
        _record, outcome = roster.set_attendance(
            session, membership, 'absent', self.facilitator, recorded_at=now - timedelta(minutes=5),
        )
        self.assertEqual(outcome, 'superseded')
        self.assertEqual(SessionAttendance.objects.get().status, 'present')
        session.refresh_from_db()
        self.assertEqual(session.status, session.Status.HELD)

    def test_results_need_score_and_attendance(self):
        membership = enrol(self.cohort)
        course = self.courses[0]
        session = make_session(self.cohort, course, hours=Decimal('6.0'))
        result = roster.record_result(membership, course, 80, self.facilitator)
        self.assertEqual(result.status, CourseResult.Status.INCOMPLETE)

        roster.set_attendance(session, membership, 'present', self.facilitator)
        result.refresh_from_db()
        self.assertEqual(result.status, CourseResult.Status.INCOMPLETE)

        roster.set_attendance(make_session(self.cohort, course, hours=Decimal('6.0')), membership, 'late',
                              self.facilitator)
        result.refresh_from_db()
        self.assertEqual(result.status, CourseResult.Status.PASSED)

        result = roster.record_result(membership, course, 30, self.facilitator, base_version=result.version)
        self.assertEqual(result.status, CourseResult.Status.FAILED)

    def test_stale_version_raises_conflict(self):
        membership = enrol(self.cohort)
        course = self.courses[0]
        first = roster.record_result(membership, course, 70, self.facilitator)
        roster.record_result(membership, course, 75, self.coordinator, base_version=first.version)
        with self.assertRaises(SyncConflict):
            roster.record_result(membership, course, 90, self.facilitator, base_version=first.version)

    def test_signed_off_results_are_locked(self):
        membership = enrol(self.cohort)
        result = roster.record_result(membership, self.courses[0], 70, self.facilitator)
        with self.assertRaises(PermissionDenied):
            roster.sign_off_result(result, self.facilitator)
        roster.sign_off_result(result, self.coordinator)
        with self.assertRaises(RosterError):
            roster.record_result(membership, self.courses[0], 90, self.coordinator)

    def test_membership_completes_when_every_course_passes(self):
        membership = enrol(self.cohort)
        for course in self.courses:
            roster.set_attendance(make_session(self.cohort, course), membership, 'present', self.facilitator)
            roster.record_result(membership, course, 65, self.facilitator)
        membership.refresh_from_db()
        self.assertEqual(membership.status, CohortMembership.Status.COMPLETED)

    def test_participants_only_join_cohorts_at_home(self):
        stranger = make_participant(make_location())
        with self.assertRaises(RosterError):
            roster.enrol(self.cohort, stranger, self.coordinator)

    def test_session_with_register_cannot_be_deleted(self):
        membership = enrol(self.cohort)
        session = make_session(self.cohort, self.courses[0])
        roster.set_attendance(session, membership, 'present', self.facilitator)
        with self.assertRaises(RosterError):
            roster.delete_session(session, self.coordinator)


class TeamTests(PartnersTestCase):
    def setUp(self):
        self.location = make_location()
        self.coordinator = add_member(self.location)

    def test_invite_creates_partner_account_without_password(self):
        membership, created = accounts.invite_member(
            self.location, self.coordinator, 'Naledi', 'Dube', LocationMembership.Role.FACILITATOR,
            email='naledi@example.org',
        )
        self.assertTrue(created)
        self.assertEqual(membership.user.role, User.PARTNER)
        self.assertFalse(membership.user.has_usable_password())
        self.assertTrue(accounts.activation_path(membership.user).startswith('/partners/activate/'))

    def test_coordinator_cannot_appoint_coordinators(self):
        with self.assertRaises(RosterError):
            accounts.invite_member(self.location, self.coordinator, 'A', 'B', LocationMembership.Role.COORDINATOR,
                                   email='x@example.org')

    def test_existing_non_partner_account_is_refused(self):
        make_staff(email='taken@example.org')
        with self.assertRaises(AccessError):
            accounts.invite_member(self.location, self.coordinator, 'A', 'B', LocationMembership.Role.FACILITATOR,
                                   email='taken@example.org')

    def test_deactivating_removes_cohort_assignments(self):
        facilitator = add_member(self.location, LocationMembership.Role.FACILITATOR)
        cohort = make_cohort(self.location, facilitators=[facilitator])
        membership = LocationMembership.objects.get(user=facilitator)
        accounts.deactivate_member(membership, self.coordinator)
        self.assertFalse(cohort.facilitators.filter(pk=facilitator.pk).exists())


@override_settings(PARTNERS_ONLINE_PRODUCT_SLUG='metp-online-programme')
class OnlineAccessTests(PartnersTestCase):
    def setUp(self):
        self.location = make_location()
        self.coordinator = add_member(self.location)
        self.cohort = make_cohort(self.location, delivery=Cohort.Delivery.BLENDED)
        self.membership = enrol(self.cohort, make_participant(self.location, email='learner@example.org'))
        self.product = make_online_product()

    def grant(self, actor=None):
        with mock.patch('partners.services.access._queue_enrolment') as queued:
            with self.captureOnCommitCallbacks(execute=True):
                order, new_user = access.grant_online_access(self.membership, actor or self.coordinator)
        return order, new_user, queued

    def test_grant_creates_free_licence_order_and_queues_existing_pipeline(self):
        order, new_user, queued = self.grant()
        self.assertEqual(order.status, Order.FREE)
        self.assertEqual(order.payment_method, 'partner_licence')
        self.assertEqual(order.total_amount, Decimal('0.00'))
        self.assertEqual(order.items.get().product, self.product)
        self.assertEqual(order.payment_metadata['cohort'], str(self.cohort.public_id))
        queued.assert_called_once_with(order.pk)
        self.assertEqual(new_user.role, User.STUDENT)
        self.assertTrue(new_user.is_verified)
        self.assertEqual(new_user.verified_by, self.coordinator)

    def test_grant_is_idempotent(self):
        first, _user, _queued = self.grant()
        self.membership.refresh_from_db()
        second, new_user, queued = self.grant()
        self.assertEqual(first.pk, second.pk)
        self.assertIsNone(new_user)
        queued.assert_not_called()
        self.assertEqual(Order.objects.count(), 1)

    def test_classroom_cohorts_get_no_access(self):
        self.cohort.delivery = Cohort.Delivery.CLASSROOM
        self.cohort.save()
        with self.assertRaises(AccessError):
            self.grant()

    def test_inactive_licence_blocks_access(self):
        licence = self.location.licence
        licence.status = Licence.Status.SUSPENDED
        licence.save()
        with self.assertRaises(LicenceStateError):
            self.grant()

    def test_facilitators_cannot_grant(self):
        facilitator = add_member(self.location, LocationMembership.Role.FACILITATOR)
        with self.assertRaises(AccessError):
            self.grant(actor=facilitator)

    def test_phone_already_on_another_account_is_left_off(self):
        self.membership.participant.phone = self.coordinator.phone_number
        self.membership.participant.save()
        _order, new_user, _queued = self.grant()
        self.assertIsNone(new_user.phone_number)

    def test_withdraw_deactivates_the_product_enrolment(self):
        order, new_user, _queued = self.grant()
        enrolment = StudentEnrollment.objects.create(
            user=new_user, product=self.product, order=order,
            expires_at=timezone.now() + timedelta(days=365),
        )
        self.membership.refresh_from_db()
        access.withdraw_online_access(self.membership, self.coordinator)
        enrolment.refresh_from_db()
        self.assertFalse(enrolment.is_active)
        self.membership.refresh_from_db()
        self.assertEqual(access.access_status(self.membership), access.ACCESS_WITHDRAWN)
        with self.assertRaises(AccessError):
            self.grant()

    def test_missing_product_is_explained(self):
        self.product.delete()
        with self.assertRaises(AccessError):
            self.grant()
