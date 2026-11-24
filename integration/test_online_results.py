from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from integration.models import Course, CourseCategory, MoodleInstance, MoodleUserMapping, OnlineQuizResult
from integration.online_results import OnlineResultSync, summarise
from partners.models import Cohort, CourseResult, LocationMembership
from partners.tests.base import PartnersTestCase
from partners.tests.helpers import (
    add_member, enrol, make_cohort, make_location, make_organisation, make_staff, make_user, seed_courses,
)


def quiz(name, score, maximum=10):
    return {'itemtype': 'mod', 'itemmodule': 'quiz', 'itemname': name, 'graderaw': score, 'grademax': maximum}


COURSE_TOTAL = {'itemtype': 'course', 'itemmodule': None, 'itemname': None, 'graderaw': 9, 'grademax': 10}
FORUM = {'itemtype': 'mod', 'itemmodule': 'forum', 'itemname': 'Forum', 'graderaw': 5, 'grademax': 5}


class SummariseTests(PartnersTestCase):
    def test_only_quizzes_count(self):
        summary = summarise([COURSE_TOTAL, FORUM, quiz('Check 1', 8), quiz('Check 2', None)])
        self.assertEqual((summary['quizzes_total'], summary['quizzes_attempted']), (2, 1))
        self.assertEqual(summary['points'], Decimal('8'))
        self.assertEqual(summary['max_points_total'], Decimal('20'))
        self.assertEqual([item['name'] for item in summary['items']], ['Check 1', 'Check 2'])

    def test_quizzes_without_a_maximum_are_ignored(self):
        self.assertEqual(summarise([quiz('Practice', 3, maximum=0)])['quizzes_total'], 0)

    def test_percentages_and_pass(self):
        result = OnlineQuizResult(
            **{k: v for k, v in summarise([quiz('A', 9), quiz('B', None)]).items()},
        )
        self.assertFalse(result.is_complete)
        self.assertEqual(result.attempted_percentage, Decimal('90.0'))
        self.assertEqual(result.overall_percentage, Decimal('45.0'))
        self.assertFalse(result.passed)

        result = OnlineQuizResult(**summarise([quiz('A', 9), quiz('B', 2)]))
        self.assertTrue(result.is_complete)
        self.assertEqual(result.overall_percentage, Decimal('55.0'))
        self.assertTrue(result.passed)

        result = OnlineQuizResult(**summarise([quiz('A', 4), quiz('B', 5)]))
        self.assertFalse(result.passed)

    def test_a_course_with_no_quizzes_is_never_complete(self):
        self.assertFalse(OnlineQuizResult(**summarise([])).is_complete)


@override_settings(SCHOLR_ONLINE_RESULTS_ENABLED=True)
class ResultsTestCase(PartnersTestCase):
    def setUp(self):
        super().setUp()
        MoodleInstance.objects.create(name='Test', base_url='http://moodle.test', ws_token='x',
                                      is_active=True, is_primary=True)
        category = CourseCategory.objects.create(name='Ministry', discipline='theology')
        self.programme = seed_courses(2)
        self.online = []
        for programme_course, moodle_id in zip(self.programme, (14, 16)):
            online = Course.objects.create(title=programme_course.title, short_name=f'METP{moodle_id}',
                                           category=category, moodle_course_id=moodle_id, auto_enroll_enabled=False)
            programme_course.online_course = online
            programme_course.save()
            self.online.append(online)

        self.location = make_location(organisation=make_organisation(country='KE'))
        self.coordinator = add_member(self.location)
        self.facilitator = add_member(self.location, role=LocationMembership.Role.FACILITATOR)
        self.cohort = make_cohort(self.location, facilitators=[self.facilitator], delivery=Cohort.Delivery.BLENDED)
        self.membership = enrol(self.cohort)
        self.student = make_user(User.STUDENT)
        self.membership.participant.user = self.student
        self.membership.participant.save()
        MoodleUserMapping.objects.update_or_create(
            cas_user=self.student, defaults={'moodle_user_id': 71, 'moodle_username': self.student.username,
                                             'sync_status': 'synced'},
        )

    def store(self, course, *quizzes):
        return OnlineQuizResult.objects.update_or_create(
            user=self.student, course=course, defaults={**summarise(list(quizzes)), 'synced_at': timezone.now()},
        )[0]


class SyncTests(ResultsTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch('integration.services.MoodleClient')
        self.client_class = patcher.start()
        self.addCleanup(patcher.stop)
        self.moodle = self.client_class.return_value
        self.moodle.get_course_grade_items.side_effect = lambda course_id: [
            {'userid': 71, 'gradeitems': [quiz('A', 7), quiz('B', None)] if course_id == 14 else [quiz('C', 10)]},
            {'userid': 9999, 'gradeitems': [quiz('A', 1)]},
        ]

    def test_saves_one_row_per_learner_and_course(self):
        report = OnlineResultSync().sync()
        self.assertEqual(report, {'courses': 2, 'saved': 2, 'unknown': 2})
        first = OnlineQuizResult.objects.get(user=self.student, course=self.online[0])
        self.assertEqual((first.quizzes_attempted, first.quizzes_total), (1, 2))
        self.assertTrue(OnlineQuizResult.objects.get(user=self.student, course=self.online[1]).passed)

    def test_running_again_updates_in_place(self):
        OnlineResultSync().sync()
        self.moodle.get_course_grade_items.side_effect = lambda course_id: [
            {'userid': 71, 'gradeitems': [quiz('A', 7), quiz('B', 6)]},
        ]
        OnlineResultSync().sync()
        self.assertEqual(OnlineQuizResult.objects.count(), 2)
        self.assertTrue(OnlineQuizResult.objects.get(user=self.student, course=self.online[0]).is_complete)

    def test_task_does_nothing_when_partners_are_off(self):
        from integration.tasks import sync_online_results_task
        with override_settings(SCHOLR_PARTNERS_ENABLED=False):
            self.assertEqual(sync_online_results_task.apply().get(), {'skipped': 'online results are off'})
        self.moodle.get_course_grade_items.assert_not_called()


class RefreshTests(ResultsTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch('integration.tasks.sync_online_results_task.delay')
        self.delay = patcher.start()
        self.addCleanup(patcher.stop)
        self.url = reverse('integration:refresh_online_results')

    def test_coordinator_can_refresh_once_per_window(self):
        self.client.force_login(self.coordinator)
        back = reverse('partners:cohort_results_overview',
                       kwargs={'location_id': self.location.public_id, 'cohort_id': self.cohort.public_id})
        response = self.client.post(self.url, {'next': back})
        self.assertRedirects(response, back, fetch_redirect_response=False)
        self.client.post(self.url, {'next': back})
        self.delay.assert_called_once()

    def test_off_site_next_is_ignored(self):
        self.client.force_login(make_staff())
        response = self.client.post(self.url, {'next': 'https://evil.example/'})
        self.assertRedirects(response, reverse('accounts:dashboard'), fetch_redirect_response=False)

    def test_students_cannot_refresh(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.delay.assert_not_called()

    def test_needs_post(self):
        self.client.force_login(self.coordinator)
        self.assertEqual(self.client.get(self.url).status_code, 405)


class EntryPageTests(ResultsTestCase):
    def url(self, course):
        return reverse('partners:cohort_results', kwargs={
            'location_id': self.location.public_id, 'cohort_id': self.cohort.public_id,
        }) + f'?course={course.code}'

    def test_complete_online_result_offers_use(self):
        self.store(self.online[0], quiz('A', 8), quiz('B', 7))
        self.client.force_login(self.coordinator)
        response = self.client.get(self.url(self.programme[0]))
        self.assertContains(response, 'Online quizzes')
        self.assertContains(response, '75%')
        self.assertContains(response, ".value = '75.0'")

    def test_partial_online_result_cannot_be_used(self):
        self.store(self.online[0], quiz('A', 8), quiz('B', None))
        self.client.force_login(self.coordinator)
        response = self.client.get(self.url(self.programme[0]))
        self.assertContains(response, '1 of 2 quizzes')
        self.assertNotContains(response, '>Use</button>')

    def test_signed_off_result_cannot_be_overwritten(self):
        self.store(self.online[0], quiz('A', 8), quiz('B', 7))
        CourseResult.objects.create(membership=self.membership, course=self.programme[0], exam_score=Decimal('60'),
                                    status=CourseResult.Status.PASSED, signed_off_by=self.coordinator,
                                    signed_off_at=timezone.now())
        self.client.force_login(self.coordinator)
        self.assertNotContains(self.client.get(self.url(self.programme[0])), '>Use</button>')

    def test_classroom_only_course_has_no_online_column(self):
        self.programme[0].online_course = None
        self.programme[0].save()
        self.client.force_login(self.coordinator)
        self.assertNotContains(self.client.get(self.url(self.programme[0])), 'Online quizzes')

    def test_using_the_score_still_goes_through_saving(self):
        self.store(self.online[0], quiz('A', 8), quiz('B', 7))
        self.client.force_login(self.coordinator)
        self.client.get(self.url(self.programme[0]))
        self.assertFalse(CourseResult.objects.filter(membership=self.membership).exists())


class OverviewTests(ResultsTestCase):
    def overview(self, view=''):
        url = reverse('partners:cohort_results_overview', kwargs={
            'location_id': self.location.public_id, 'cohort_id': self.cohort.public_id,
        })
        return self.client.get(url + (f'?view={view}' if view else ''))

    def test_programme_view_by_default(self):
        CourseResult.objects.create(membership=self.membership, course=self.programme[0],
                                    exam_score=Decimal('72'), status=CourseResult.Status.PASSED)
        self.client.force_login(self.coordinator)
        response = self.overview()
        self.assertContains(response, '72%')
        self.assertContains(response, 'Programme results are the scores recorded')

    def test_online_view(self):
        self.store(self.online[0], quiz('A', 8), quiz('B', 7))
        self.store(self.online[1], quiz('C', 5), quiz('D', None))
        self.client.force_login(self.facilitator)
        response = self.overview('online')
        self.assertContains(response, '75%')
        self.assertContains(response, '1 of 2')
        self.assertContains(response, 'Refresh from LMS')

    def test_a_facilitator_of_another_cohort_is_turned_away(self):
        other = add_member(self.location, role=LocationMembership.Role.FACILITATOR)
        self.client.force_login(other)
        self.assertEqual(self.overview().status_code, 404)


class StaffOverviewTests(ResultsTestCase):
    def setUp(self):
        super().setUp()
        self.store(self.online[0], quiz('A', 8), quiz('B', 7))
        elsewhere = make_location(organisation=make_organisation(country='NG'))
        self.other_membership = enrol(make_cohort(elsewhere, delivery=Cohort.Delivery.BLENDED))
        self.url = reverse('partners:results_overview')

    def test_staff_see_everyone_and_can_filter_by_country(self):
        self.client.force_login(make_staff())
        response = self.client.get(self.url)
        self.assertContains(response, self.membership.participant.full_name)
        self.assertContains(response, self.other_membership.participant.full_name)
        response = self.client.get(self.url, {'country': 'KE', 'view': 'online'})
        self.assertContains(response, self.membership.participant.full_name)
        self.assertNotContains(response, self.other_membership.participant.full_name)
        self.assertContains(response, '75%')

    def test_partner_staff_are_refused(self):
        self.client.force_login(self.coordinator)
        self.assertEqual(self.client.get(self.url).status_code, 403)


@override_settings(SCHOLR_COHORT_GROUPS_ENABLED=True, MOODLE_COHORT_VIEWER_ROLE_ID=9)
class SetupReportTests(ResultsTestCase):
    def test_people_without_an_lms_account_are_reported(self):
        self.membership.access_order = self._order()
        self.membership.save()
        with mock.patch('integration.services.MoodleClient') as client_class:
            moodle = client_class.return_value
            moodle.get_course_groups.return_value = []
            moodle.create_groups.side_effect = lambda specs: [{'id': i + 1, 'idnumber': s['idnumber']}
                                                              for i, s in enumerate(specs)]
            moodle.get_enrolled_user_ids.return_value = {71}
            moodle.get_group_member_ids.return_value = {}
            output = StringIO()
            call_command('setup_cohort_groups', '--apply', '--skip-group-mode', stdout=output)
        self.assertIn('2 person(s) have no LMS account yet', output.getvalue())

    def _order(self):
        from accounts.models import Order
        return Order.objects.create(user=self.student, status=Order.FREE, subtotal=Decimal('0'),
                                    discount_amount=Decimal('0'), total_amount=Decimal('0'), currency='CAD')
