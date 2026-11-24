from decimal import Decimal
from itertools import count
from unittest import mock

from django.template.loader import render_to_string
from django.test import RequestFactory, override_settings
from django.urls import reverse

from accounts.models import Order, User
from integration.cohort_groups import GROUP_IDNUMBER_PREFIX, CohortGroupSync, programme_courses
from integration.exceptions import MoodleAPIError
from integration.models import (
    Course, CourseCategory, CourseEnrollment, MoodleCohortGroup, MoodleInstance, MoodleUserMapping,
)
from integration.staff_access import StaffAccessError, StaffLMSAccess
from partners.models import Cohort, LocationMembership
from partners.tests.base import PartnersTestCase
from partners.tests.helpers import add_member, enrol, make_cohort, make_location, make_user, seed_courses

VIEWER_ROLE = 9
_moodle_ids = count(500)


class FakeMoodle:
    """A small in-memory Moodle: courses, enrolments and groups, enough to check end state."""

    def __init__(self):
        self.groups = {}
        self.enrolled = {}
        self.next_group = count(1000)
        self.group_mode = {}
        self.enrolments = []
        self.refuse_names = set()

    def members_of(self, course_id, idnumber):
        for group in self.groups.values():
            if group['courseid'] == course_id and group['idnumber'] == idnumber:
                return group['members']
        return None

    def get_course_groups(self, course_id):
        return [dict(g, members=None) for g in self.groups.values() if g['courseid'] == course_id]

    def create_groups(self, specs):
        created = []
        for spec in specs:
            taken = {g['name'] for g in self.groups.values() if g['courseid'] == spec['courseid']}
            if spec['name'] in taken or spec['name'] in self.refuse_names:
                raise MoodleAPIError('Moodle error (groupnameexists): name in use')
        for spec in specs:
            group_id = next(self.next_group)
            self.groups[group_id] = dict(spec, id=group_id, members=set())
            created.append({'id': group_id, 'idnumber': spec['idnumber'], 'name': spec['name']})
        return created

    def get_group_member_ids(self, group_ids):
        return {gid: set(self.groups[gid]['members']) for gid in group_ids if gid in self.groups}

    def add_group_members(self, members):
        for member in members:
            group = self.groups.get(member['groupid'])
            if group is None:
                raise MoodleAPIError('Moodle error (invalidrecord): group missing')
            if member['userid'] not in self.enrolled.get(group['courseid'], set()):
                raise MoodleAPIError('Moodle error (userisnotincourse)')
        for member in members:
            self.groups[member['groupid']]['members'].add(member['userid'])
        return True

    def delete_group_members(self, members):
        for member in members:
            self.groups[member['groupid']]['members'].discard(member['userid'])
        return True

    def get_enrolled_user_ids(self, course_id):
        return set(self.enrolled.get(course_id, set()))

    def set_separate_groups(self, course_id):
        self.group_mode[course_id] = 'separate-forced'
        return True

    def enrol_user_in_courses(self, user_id, course_ids, role_id):
        for course_id in course_ids:
            self.enrolled.setdefault(course_id, set()).add(user_id)
            self.enrolments.append((user_id, course_id, role_id))
        return True

    def enrol_user(self, user_id, course_id, role_id=5):
        return self.enrol_user_in_courses(user_id, [course_id], role_id)


def give_access(membership, moodle_user_id=None):
    """Mark a cohort place as having online access, with a linked Moodle account."""
    user = membership.participant.user or make_user(User.STUDENT)
    membership.participant.user = user
    membership.participant.save()
    membership.access_order = Order.objects.create(
        user=user, status=Order.FREE, subtotal=Decimal('0'), discount_amount=Decimal('0'),
        total_amount=Decimal('0'), currency='CAD',
    )
    membership.save()
    return map_to_moodle(user, moodle_user_id)


def map_to_moodle(user, moodle_user_id=None):
    moodle_user_id = moodle_user_id or next(_moodle_ids)
    MoodleUserMapping.objects.update_or_create(
        cas_user=user, defaults={
            'moodle_user_id': moodle_user_id, 'moodle_username': user.username, 'sync_status': 'synced',
        },
    )
    return moodle_user_id


@override_settings(SCHOLR_COHORT_GROUPS_ENABLED=True, MOODLE_COHORT_VIEWER_ROLE_ID=VIEWER_ROLE)
class CohortGroupTestCase(PartnersTestCase):
    def setUp(self):
        super().setUp()
        MoodleInstance.objects.create(name='Test', base_url='http://moodle.test', ws_token='x',
                                      is_active=True, is_primary=True)
        self.fake = FakeMoodle()
        patcher = mock.patch('integration.services.MoodleClient', return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        queue = mock.patch('integration.tasks.sync_cohort_groups_task.apply_async')
        self.queued = queue.start()
        self.addCleanup(queue.stop)

        category = CourseCategory.objects.create(name='Ministry', discipline='theology')
        self.courses = []
        for programme_course, moodle_id in zip(seed_courses(2), (14, 16)):
            online = Course.objects.create(
                title=programme_course.title, short_name=f'METP{moodle_id}', category=category,
                moodle_course_id=moodle_id, auto_enroll_enabled=False,
            )
            programme_course.online_course = online
            programme_course.save()
            self.courses.append(online)

        self.location = make_location()
        self.coordinator = add_member(self.location)
        self.facilitator = add_member(self.location, role=LocationMembership.Role.FACILITATOR)
        self.idle_facilitator = add_member(self.location, role=LocationMembership.Role.FACILITATOR)
        self.cohort_a = make_cohort(self.location, facilitators=[self.facilitator], delivery=Cohort.Delivery.BLENDED)
        self.cohort_b = make_cohort(self.location, delivery=Cohort.Delivery.BLENDED)
        self.classroom = make_cohort(self.location)
        self.queued.reset_mock()

    def enrol_everywhere(self, *moodle_user_ids):
        for course in self.courses:
            self.fake.enrolled.setdefault(course.moodle_course_id, set()).update(moodle_user_ids)

    def group(self, cohort, course=None):
        course = course or self.courses[0]
        return self.fake.members_of(course.moodle_course_id, f'{GROUP_IDNUMBER_PREFIX}{cohort.public_id}')

    def sync(self):
        return CohortGroupSync().sync()


class ReconcileTests(CohortGroupTestCase):
    def setUp(self):
        super().setUp()
        self.alice = give_access(enrol(self.cohort_a))
        self.bob = give_access(enrol(self.cohort_b))
        self.coordinator_id = map_to_moodle(self.coordinator)
        self.facilitator_id = map_to_moodle(self.facilitator)
        self.idle_id = map_to_moodle(self.idle_facilitator)
        self.enrol_everywhere(self.alice, self.bob, self.coordinator_id, self.facilitator_id, self.idle_id)

    def test_one_group_per_online_cohort_in_every_course(self):
        report = self.sync()
        self.assertEqual(report['created'], 4)
        for course in self.courses:
            self.assertIsNotNone(self.group(self.cohort_a, course))
            self.assertIsNotNone(self.group(self.cohort_b, course))
            self.assertIsNone(self.group(self.classroom, course))
        self.assertEqual(MoodleCohortGroup.objects.count(), 4)

    def test_cohorts_are_isolated_and_staff_see_only_their_cohorts(self):
        self.sync()
        self.assertEqual(self.group(self.cohort_a), {self.alice, self.coordinator_id, self.facilitator_id})
        self.assertEqual(self.group(self.cohort_b), {self.bob, self.coordinator_id})
        self.assertNotIn(self.idle_id, self.group(self.cohort_a) | self.group(self.cohort_b))

    @override_settings(MOODLE_COHORT_VIEWER_ROLE_ID=0)
    def test_staff_are_left_out_until_the_viewer_role_exists(self):
        self.sync()
        self.assertEqual(self.group(self.cohort_a), {self.alice})

    def test_running_twice_changes_nothing(self):
        self.sync()
        report = self.sync()
        self.assertEqual((report['created'], report['added'], report['removed']), (0, 0, 0))

    def test_withdrawn_access_and_deactivated_staff_are_removed(self):
        self.sync()
        membership = self.cohort_a.memberships.get()
        membership.access_withdrawn_at = membership.created_at
        membership.save()
        LocationMembership.objects.filter(user=self.coordinator).update(is_active=False)
        self.sync()
        self.assertEqual(self.group(self.cohort_a), {self.facilitator_id})
        self.assertEqual(self.group(self.cohort_b), {self.bob})

    def test_unassigning_a_facilitator_removes_them(self):
        self.sync()
        self.cohort_a.facilitators.clear()
        self.sync()
        self.assertNotIn(self.facilitator_id, self.group(self.cohort_a))

    def test_people_not_enrolled_yet_wait_for_a_later_run(self):
        carol = give_access(enrol(self.cohort_a))
        report = self.sync()
        self.assertNotIn(carol, self.group(self.cohort_a))
        self.assertEqual(report['waiting'], 2)
        self.enrol_everywhere(carol)
        self.sync()
        self.assertIn(carol, self.group(self.cohort_a))

    def test_a_group_deleted_in_moodle_is_recreated(self):
        self.sync()
        stored = MoodleCohortGroup.objects.get(cohort=self.cohort_a, course=self.courses[0])
        del self.fake.groups[stored.moodle_group_id]
        self.sync()
        stored.refresh_from_db()
        self.assertIn(stored.moodle_group_id, self.fake.groups)
        self.assertIn(self.alice, self.group(self.cohort_a))

    def test_a_clashing_group_name_gets_a_suffix(self):
        self.fake.refuse_names.add(f'{self.cohort_a.name} ({self.location.name})')
        self.sync()
        names = {g['name'] for g in self.fake.groups.values() if g['idnumber'].endswith(str(self.cohort_a.public_id))}
        self.assertTrue(all('[' in name for name in names))
        self.assertIn(self.alice, self.group(self.cohort_a))

    def test_groups_scholr_did_not_create_are_left_alone(self):
        self.fake.groups[1] = {'id': 1, 'courseid': 14, 'name': 'Tutors', 'idnumber': 'tutors', 'members': {42}}
        self.sync()
        self.assertEqual(self.fake.groups[1]['members'], {42})

    def test_separate_groups_switched_on_last(self):
        sync = CohortGroupSync()
        sync.sync()
        self.assertEqual(sync.apply_separate_groups(), ['METP14', 'METP16'])
        self.assertEqual(self.fake.group_mode, {14: 'separate-forced', 16: 'separate-forced'})


class TriggerTests(CohortGroupTestCase):
    def test_granting_access_queues_the_cohort_once(self):
        with self.captureOnCommitCallbacks(execute=True):
            membership = enrol(self.cohort_a)
            give_access(membership)
        self.queued.assert_called_once()
        self.assertEqual(self.queued.call_args.kwargs['args'], [[self.cohort_a.pk]])

    def test_assigning_a_facilitator_queues_the_cohort(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.cohort_b.facilitators.add(self.idle_facilitator)
        self.assertEqual(self.queued.call_args.kwargs['args'], [[self.cohort_b.pk]])

    def test_a_team_change_queues_every_online_cohort_at_the_location(self):
        with self.captureOnCommitCallbacks(execute=True):
            LocationMembership.objects.get(user=self.coordinator).save()
        self.assertEqual(sorted(self.queued.call_args.kwargs['args'][0]), sorted([self.cohort_a.pk, self.cohort_b.pk]))

    def test_finished_enrolment_queues_but_progress_saves_do_not(self):
        membership = enrol(self.cohort_a)
        give_access(membership)
        enrolment = CourseEnrollment.objects.create(user=membership.participant.user, course=self.courses[0],
                                                    is_active=True, sync_status='pending')
        self.queued.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            enrolment.progress_percentage = Decimal('40')
            enrolment.sync_status = 'synced'
            enrolment.save()
        self.queued.assert_not_called()
        with self.captureOnCommitCallbacks(execute=True):
            enrolment.mark_synced(321)
        self.assertEqual(self.queued.call_args.kwargs['args'], [[self.cohort_a.pk]])

    @override_settings(SCHOLR_COHORT_GROUPS_ENABLED=False)
    def test_nothing_is_queued_when_the_feature_is_off(self):
        with self.captureOnCommitCallbacks(execute=True):
            give_access(enrol(self.cohort_a))
        self.queued.assert_not_called()


class PartnerPortalTests(CohortGroupTestCase):
    def setUp(self):
        super().setUp()
        self.alice = give_access(enrol(self.cohort_a))
        self.enrol_everywhere(self.alice)
        CohortGroupSync().sync()
        map_to_moodle(self.facilitator, 800)
        self.queued.reset_mock()

    def test_partners_need_groups_and_the_viewer_role(self):
        self.assertTrue(self.facilitator.can_open_lms_as_staff())
        with override_settings(MOODLE_COHORT_VIEWER_ROLE_ID=0):
            self.assertFalse(self.facilitator.can_open_lms_as_staff())
        with override_settings(SCHOLR_COHORT_GROUPS_ENABLED=False):
            self.assertFalse(self.facilitator.can_open_lms_as_staff())

    def test_facilitator_gets_viewer_role_in_programme_courses_and_their_cohort_group(self):
        result = StaffLMSAccess(self.facilitator).grant_access()
        self.assertEqual(sorted(self.fake.enrolments), [(800, 14, VIEWER_ROLE), (800, 16, VIEWER_ROLE)])
        self.assertEqual(result['role'], 'Cohort viewer')
        self.assertEqual((result['cohorts'], result['pending_cohorts']), (1, 0))
        for course in self.courses:
            self.assertIn(800, self.group(self.cohort_a, course))
            self.assertNotIn(800, self.group(self.cohort_b, course))

    def test_only_programme_courses_are_touched(self):
        Course.objects.create(title='Other', short_name='OTHER', category=self.courses[0].category,
                              moodle_course_id=99, auto_enroll_enabled=False)
        StaffLMSAccess(self.facilitator).grant_access()
        self.assertNotIn(99, {course for _user, course, _role in self.fake.enrolments})
        self.assertEqual(programme_courses().count(), 2)

    def test_a_new_cohort_refreshes_cached_access(self):
        StaffLMSAccess(self.facilitator).grant_access()
        self.assertEqual(StaffLMSAccess(self.facilitator).grant_access()['status'], 'current')
        self.cohort_b.facilitators.add(self.facilitator)
        result = StaffLMSAccess(self.facilitator).grant_access()
        self.assertEqual(result['status'], 'granted')
        self.assertEqual(result['cohorts'], 2)

    def test_cohort_without_a_group_yet_queues_a_sync_and_still_signs_in(self):
        late = make_cohort(self.location, facilitators=[self.facilitator], delivery=Cohort.Delivery.BLENDED)
        with self.captureOnCommitCallbacks(execute=True):
            result = StaffLMSAccess(self.facilitator).grant_access()
        self.assertEqual(result['pending_cohorts'], 1)
        self.assertIn([late.pk], [call.kwargs['args'][0] for call in self.queued.call_args_list])

    def test_facilitator_with_no_online_cohort_is_told_why(self):
        with self.assertRaisesMessage(StaffAccessError, 'not looking after any online cohort'):
            map_to_moodle(self.idle_facilitator)
            StaffLMSAccess(self.idle_facilitator).grant_access()

    def test_access_message_names_the_cohorts(self):
        self.client.force_login(self.facilitator)
        response = self.client.post(reverse('integration:staff_lms_access'))
        self.assertEqual(response.json()['message'], 'Cohort viewer access to 2 courses. You can see 1 cohort.')

    def test_location_dashboard_shows_the_button_to_partners(self):
        self.client.force_login(self.facilitator)
        url = reverse('partners:location_dashboard', kwargs={'location_id': self.location.public_id})
        response = self.client.get(url)
        self.assertContains(response, 'LMS Portal')


class PurchaseFlagTests(PartnersTestCase):
    @override_settings(SCHOLR_PURCHASES_ENABLED=False)
    def test_buying_pages_are_gone_but_my_learning_stays(self):
        for path in ('/courses/', '/courses/cart/', '/courses/checkout/', '/courses/product/anything/',
                     '/courses/payment/ORD1/', '/courses/orders/'):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertNotEqual(self.client.get('/courses/my-courses/').status_code, 404)

    def test_buying_pages_stay_by_default(self):
        self.assertEqual(self.client.get('/courses/').status_code, 200)

    def test_metp_home_offers_sign_in_when_both_are_off(self):
        request = RequestFactory().get('/')
        request.user = mock.Mock(is_authenticated=False)
        html = render_to_string('main/home_metp.html', {
            'SCHOLR_PURCHASES_ENABLED': False, 'SCHOLR_SELF_REGISTRATION': False,
            'brand': {'name': 'METP'}, 'request': request,
        })
        self.assertIn('Sign in to your courses', html)
        self.assertNotIn('Create an account', html)
        self.assertNotIn('Browse online courses', html)


class EnrolmentStatusTests(PartnersTestCase):
    def setUp(self):
        super().setUp()
        category = CourseCategory.objects.create(name='Ministry', discipline='theology')
        course = Course.objects.create(title='T', short_name='T1', category=category,
                                       moodle_course_id=5, auto_enroll_enabled=False)
        self.enrolment = CourseEnrollment.objects.create(
            user=make_user(User.STUDENT), course=course, is_active=True, sync_status='pending',
        )

    def test_mark_synced_stays_synced(self):
        self.enrolment.mark_synced(77)
        self.enrolment.refresh_from_db()
        self.assertEqual(self.enrolment.sync_status, 'synced')

    def test_mark_failed_stays_failed(self):
        self.enrolment.mark_failed('boom')
        self.enrolment.refresh_from_db()
        self.assertEqual(self.enrolment.sync_status, 'failed')

    def test_other_edits_still_flag_a_synced_enrolment(self):
        self.enrolment.mark_synced(77)
        self.enrolment.is_active = False
        self.enrolment.save()
        self.enrolment.refresh_from_db()
        self.assertEqual(self.enrolment.sync_status, 'pending')


@override_settings(SCHOLR_PURCHASES_ENABLED=False, SCHOLR_SELF_REGISTRATION=False)
class PurchasesOffPageTests(PartnersTestCase):
    BUYING_LINKS = ('href="/courses/"', '/courses/cart/', '/courses/orders/', 'Browse Courses', 'Browse courses')

    def assert_no_buying_or_signup(self, response):
        html = response.content.decode()
        for marker in self.BUYING_LINKS + ('/dashboard/register/',):
            self.assertNotIn(marker, html, marker)

    def test_guest_pages(self):
        response = self.client.get(reverse('accounts:login'))
        self.assertEqual(response.status_code, 200)
        self.assert_no_buying_or_signup(response)

    def test_student_pages_keep_my_learning(self):
        self.client.force_login(make_user(User.STUDENT))
        response = self.client.get(reverse('accounts:student_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assert_no_buying_or_signup(response)
        self.assertContains(response, '/courses/my-courses/')
        response = self.client.get(reverse('shop:my_courses'))
        self.assertEqual(response.status_code, 200)
        self.assert_no_buying_or_signup(response)
        self.assertContains(response, 'your coordinator')

    def test_registration_sends_people_to_sign_in(self):
        response = self.client.get(reverse('accounts:register'))
        self.assertRedirects(response, reverse('accounts:login'), fetch_redirect_response=False)


class SetupCommandTests(CohortGroupTestCase):
    def setUp(self):
        super().setUp()
        self.alice = give_access(enrol(self.cohort_a))
        self.enrol_everywhere(self.alice)

    def run_command(self, *args):
        from io import StringIO
        from django.core.management import call_command
        output = StringIO()
        call_command('setup_cohort_groups', *args, stdout=output)
        return output.getvalue()

    def test_dry_run_changes_nothing(self):
        output = self.run_command()
        self.assertIn('Dry run', output)
        self.assertEqual(self.fake.groups, {})
        self.assertEqual(self.fake.group_mode, {})

    def test_apply_fills_groups_before_switching_mode(self):
        order = []
        original_add, original_mode = self.fake.add_group_members, self.fake.set_separate_groups
        self.fake.add_group_members = lambda members: order.append('fill') or original_add(members)
        self.fake.set_separate_groups = lambda course_id: order.append('mode') or original_mode(course_id)
        output = self.run_command('--apply')
        self.assertEqual(order, ['fill', 'fill', 'mode', 'mode'])
        self.assertIn(self.alice, self.group(self.cohort_a))
        self.assertIn('Forced separate groups on 2 course(s)', output)

    def test_skip_group_mode(self):
        self.run_command('--apply', '--skip-group-mode')
        self.assertEqual(self.fake.group_mode, {})
        self.assertIn(self.alice, self.group(self.cohort_a))

    @override_settings(MOODLE_COHORT_VIEWER_ROLE_ID=0)
    def test_warns_when_the_viewer_role_is_missing(self):
        self.assertIn('MOODLE_COHORT_VIEWER_ROLE_ID is not set', self.run_command())

    def test_refuses_without_linked_courses(self):
        from django.core.management.base import CommandError
        from partners.models import ProgrammeCourse
        ProgrammeCourse.objects.update(online_course=None)
        with self.assertRaisesMessage(CommandError, 'seed_metp_programme'):
            self.run_command()
