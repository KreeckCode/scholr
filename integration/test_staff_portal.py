from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User, UserProfile
from integration.exceptions import MoodleAPIError, MoodleConnectionError
from integration.models import Course, CourseCategory, MoodleInstance, MoodleUserMapping
from integration.services import MoodleIntegrationService
from integration.staff_access import StaffAccessError, StaffLMSAccess, country_code

LOCMEM_CACHES = {
    alias: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'staff-portal-{alias}'}
    for alias in ('default', 'sessions', 'moodle')
}

SSO_URL = 'http://moodle.test/auth/userkey/login.php?key=abc'
FALLBACK_URL = 'http://moodle.test/login/index.php'


def make_user(username, role, email=None, first='', last='', country='', phone=None):
    user = User.objects.create(
        username=username, role=role, email=email, first_name=first, last_name=last,
        phone_number=phone, is_verified=True,
    )
    user.set_password('pass-12345!')
    user.save()
    UserProfile.objects.create(user=user, country=country, region='')
    return user


def map_user(user, moodle_user_id, username=None):
    # Verified students already get a mapping from the post_save signal
    mapping, _created = MoodleUserMapping.objects.update_or_create(
        cas_user=user,
        defaults={
            'moodle_user_id': moodle_user_id,
            'moodle_username': username or user.username,
            'sync_status': 'synced',
        },
    )
    return mapping


@override_settings(CACHES=LOCMEM_CACHES, SCHOLR_STAFF_LMS_PORTAL_ENABLED=True)
class StaffPortalTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.instance = MoodleInstance.objects.create(
            name='Test', base_url='http://moodle.test', ws_token='x', is_active=True, is_primary=True,
        )
        patcher = mock.patch('integration.services.MoodleClient')
        self.client_class = patcher.start()
        self.addCleanup(patcher.stop)
        self.moodle = self.client_class.return_value
        self.moodle.get_user_by_username.return_value = None
        self.moodle.get_user_by_email.return_value = None
        self.moodle.create_user.return_value = 700

    def access_for(self, user):
        return StaffLMSAccess(user, MoodleIntegrationService(self.instance))

    def add_courses(self, *moodle_ids):
        category, _created = CourseCategory.objects.get_or_create(
            discipline='theology', defaults={'name': 'Ministry'},
        )
        for index, moodle_id in enumerate(moodle_ids):
            Course.objects.create(
                title=f'Course {index}', short_name=f'T{moodle_id}', category=category,
                moodle_course_id=moodle_id, auto_enroll_enabled=False,
            )


class AccountTests(StaffPortalTestCase):
    def test_students_are_turned_away(self):
        student = make_user('pastor.one', User.STUDENT, email='p@example.org')
        with self.assertRaises(StaffAccessError):
            self.access_for(student)

    def test_synced_mapping_is_trusted_without_calling_moodle(self):
        admin = make_user('ops', User.ADMIN, email='ops@example.org')
        map_user(admin, 42)
        result = self.access_for(admin).ensure_account()
        self.assertEqual(result, {'status': 'existing', 'moodle_user_id': 42})
        self.moodle.get_user_by_username.assert_not_called()
        self.moodle.create_user.assert_not_called()

    def test_creates_account_filling_blank_names(self):
        admin = make_user('Admin', User.ADMIN, email='dave@example.org', country='Canada', phone='+27710009999')
        result = self.access_for(admin).ensure_account()

        self.assertEqual(result['status'], 'created')
        payload = self.moodle.create_user.call_args.args[0]
        self.assertEqual(payload['username'], 'admin')
        self.assertEqual(payload['firstname'], 'Admin')
        self.assertEqual(payload['lastname'], 'System Administrator')
        self.assertEqual(payload['email'], 'dave@example.org')
        self.assertEqual(payload['country'], 'CA')
        self.assertEqual(payload['phone1'], '+27710009999')
        self.assertRegex(payload['password'], r'^.{24}$')

        mapping = MoodleUserMapping.objects.get(cas_user=admin)
        self.assertEqual(
            (mapping.moodle_user_id, mapping.moodle_username, mapping.sync_status), (700, 'admin', 'synced'),
        )

    def test_unknown_country_is_left_to_moodle(self):
        dean = make_user('dean.x', User.DEAN, email='x@example.org', country='Atlantis')
        self.access_for(dean).ensure_account()
        self.assertNotIn('country', self.moodle.create_user.call_args.args[0])

    def test_links_same_username_when_email_matches(self):
        dean = make_user('dean.one', User.DEAN, email='Dean@Example.org')
        self.moodle.get_user_by_username.return_value = {'id': 55, 'username': 'dean.one', 'email': 'dean@example.org'}
        result = self.access_for(dean).ensure_account()
        self.assertEqual(result, {'status': 'linked', 'moodle_user_id': 55})
        self.moodle.create_user.assert_not_called()

    def test_refuses_same_username_with_different_email(self):
        dean = make_user('dean.one', User.DEAN, email='dean@example.org')
        self.moodle.get_user_by_username.return_value = {'id': 55, 'username': 'dean.one', 'email': 'someone@else.org'}
        with self.assertRaisesMessage(StaffAccessError, 'different email'):
            self.access_for(dean).ensure_account()
        self.assertFalse(MoodleUserMapping.objects.filter(cas_user=dean).exists())

    def test_refuses_same_username_when_we_have_no_email(self):
        dean = make_user('dean.one', User.DEAN, email=None)
        self.moodle.get_user_by_username.return_value = {'id': 55, 'username': 'dean.one', 'email': 'dean@example.org'}
        with self.assertRaises(StaffAccessError):
            self.access_for(dean).ensure_account()

    def test_links_by_email_when_moodle_username_differs(self):
        admin = make_user('admin', User.ADMIN, email='dave@example.org')
        self.moodle.get_user_by_email.return_value = {'id': 2, 'username': 'dave.moodle', 'email': 'dave@example.org'}
        result = self.access_for(admin).ensure_account()
        self.assertEqual(result['status'], 'linked')
        self.assertEqual(MoodleUserMapping.objects.get(cas_user=admin).moodle_username, 'dave.moodle')

    def test_refuses_an_account_already_linked_to_someone_else(self):
        other = make_user('other', User.STUDENT, email='other@example.org')
        map_user(other, 2, username='other')
        admin = make_user('admin', User.ADMIN, email='dave@example.org')
        self.moodle.get_user_by_email.return_value = {'id': 2, 'username': 'other', 'email': 'dave@example.org'}
        with self.assertRaisesMessage(StaffAccessError, 'already linked to other'):
            self.access_for(admin).ensure_account()

    def test_failed_mapping_is_retried(self):
        staff = make_user('prog', User.PROGRAMME_STAFF, email='prog@example.org')
        MoodleUserMapping.objects.create(cas_user=staff, moodle_username='prog', sync_status='failed')
        result = self.access_for(staff).ensure_account()
        self.assertEqual(result['status'], 'created')
        self.assertEqual(MoodleUserMapping.objects.get(cas_user=staff).sync_status, 'synced')


class AccessTests(StaffPortalTestCase):
    def test_needs_an_account_first(self):
        dean = make_user('dean.one', User.DEAN, email='d@example.org')
        with self.assertRaises(StaffAccessError):
            self.access_for(dean).grant_access()

    def test_dean_gets_non_editing_teacher_in_one_request(self):
        dean = make_user('dean.one', User.DEAN, email='d@example.org')
        map_user(dean, 80)
        self.add_courses(14, 9, 12)
        result = self.access_for(dean).grant_access()

        self.moodle.enrol_user_in_courses.assert_called_once_with(80, [9, 12, 14], 4)
        self.assertEqual((result['status'], result['courses'], result['role']), ('granted', 3, 'Non-editing teacher'))

    def test_admin_gets_editing_teacher(self):
        admin = make_user('ops', User.ADMIN, email='o@example.org')
        map_user(admin, 81)
        self.add_courses(14)
        self.access_for(admin).grant_access()
        self.moodle.enrol_user_in_courses.assert_called_once_with(81, [14], 3)

    def test_second_visit_is_served_from_cache(self):
        dean = make_user('dean.one', User.DEAN, email='d@example.org')
        map_user(dean, 80)
        self.add_courses(14)
        self.access_for(dean).grant_access()
        result = self.access_for(dean).grant_access()
        self.assertEqual(result['status'], 'current')
        self.assertEqual(self.moodle.enrol_user_in_courses.call_count, 1)

    def test_a_new_course_breaks_the_cache(self):
        dean = make_user('dean.one', User.DEAN, email='d@example.org')
        map_user(dean, 80)
        self.add_courses(14)
        self.access_for(dean).grant_access()
        self.add_courses(15)
        self.access_for(dean).grant_access()
        self.assertEqual(self.moodle.enrol_user_in_courses.call_count, 2)
        self.assertEqual(self.moodle.enrol_user_in_courses.call_args.args[1], [14, 15])

    def test_refused_batch_falls_back_course_by_course(self):
        dean = make_user('dean.one', User.DEAN, email='d@example.org')
        map_user(dean, 80)
        self.add_courses(14, 15, 16)
        self.moodle.enrol_user_in_courses.side_effect = MoodleAPIError('course 15 missing')

        def enrol_one(user_id, course_id, role_id):
            if course_id == 15:
                raise MoodleAPIError('gone')
            return True

        self.moodle.enrol_user.side_effect = enrol_one
        result = self.access_for(dean).grant_access()

        self.assertEqual(result['skipped'], [15])
        self.assertEqual(result['courses'], 2)
        self.assertEqual(self.moodle.enrol_user.call_count, 3)
        self.access_for(dean).grant_access()
        self.assertEqual(self.moodle.enrol_user_in_courses.call_count, 2)

    def test_no_courses_means_no_request(self):
        dean = make_user('dean.one', User.DEAN, email='d@example.org')
        map_user(dean, 80)
        result = self.access_for(dean).grant_access()
        self.assertEqual(result['courses'], 0)
        self.moodle.enrol_user_in_courses.assert_not_called()

    @override_settings(MOODLE_ASSIGN_MANAGER_TO_ADMINS=True)
    def test_admins_can_be_made_managers(self):
        admin = make_user('ops', User.ADMIN, email='o@example.org')
        map_user(admin, 81)
        result = self.access_for(admin).grant_access()
        self.moodle.assign_system_role.assert_called_once_with(81, 1)
        self.assertIs(result['manager'], True)

    @override_settings(MOODLE_ASSIGN_MANAGER_TO_ADMINS=True)
    def test_manager_refusal_does_not_block_and_is_retried(self):
        admin = make_user('ops', User.ADMIN, email='o@example.org')
        map_user(admin, 81)
        self.moodle.assign_system_role.side_effect = MoodleAPIError('accessexception')
        result = self.access_for(admin).grant_access()
        self.assertIs(result['manager'], False)
        self.access_for(admin).grant_access()
        self.assertEqual(self.moodle.assign_system_role.call_count, 2)

    @override_settings(MOODLE_ASSIGN_MANAGER_TO_ADMINS=True)
    def test_deans_are_never_made_managers(self):
        dean = make_user('dean.one', User.DEAN, email='d@example.org')
        map_user(dean, 80)
        self.access_for(dean).grant_access()
        self.moodle.assign_system_role.assert_not_called()


class CountryCodeTests(TestCase):
    def test_names_and_codes(self):
        self.assertEqual(country_code('Canada'), 'CA')
        self.assertEqual(country_code(' south africa '), 'ZA')
        self.assertEqual(country_code('ng'), 'NG')
        self.assertIsNone(country_code(''))
        self.assertIsNone(country_code('Atlantis'))


class ViewTests(StaffPortalTestCase):
    def setUp(self):
        super().setUp()
        self.dean = make_user('dean.one', User.DEAN, email='d@example.org')
        self.client.force_login(self.dean)

    def test_account_step_reports_what_happened(self):
        response = self.client.post(reverse('integration:staff_lms_account'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True, 'status': 'created', 'message': 'Created your LMS account.'})

    def test_account_step_needs_post(self):
        self.assertEqual(self.client.get(reverse('integration:staff_lms_account')).status_code, 405)

    def test_students_get_a_json_refusal(self):
        student = make_user('pastor.one', User.STUDENT, email='p@example.org')
        self.client.force_login(student)
        response = self.client.post(reverse('integration:staff_lms_account'))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()['ok'])

    def test_signed_out_gets_json_not_a_login_page(self):
        self.client.logout()
        response = self.client.post(reverse('integration:staff_lms_account'))
        self.assertEqual(response.status_code, 401)
        self.assertIn('Sign in again', response.json()['message'])

    def test_missing_moodle_connection_is_explained(self):
        MoodleInstance.objects.all().delete()
        response = self.client.post(reverse('integration:staff_lms_account'))
        self.assertEqual(response.status_code, 409)
        self.assertIn('not set up yet', response.json()['message'])

    def test_offline_moodle_is_explained(self):
        self.moodle.get_user_by_username.side_effect = MoodleConnectionError('timed out')
        response = self.client.post(reverse('integration:staff_lms_account'))
        self.assertEqual(response.status_code, 503)
        self.assertIn('not responding', response.json()['message'])

    def test_moodle_refusals_are_passed_on(self):
        self.moodle.create_user.side_effect = MoodleAPIError('Moodle error (invalidusername): bad')
        response = self.client.post(reverse('integration:staff_lms_account'))
        self.assertEqual(response.status_code, 502)
        self.assertIn('invalidusername', response.json()['message'])

    def test_unexpected_errors_stay_in_the_log(self):
        self.moodle.create_user.side_effect = RuntimeError('secret internals')
        with self.assertLogs('integration.staff_portal', level='ERROR'):
            response = self.client.post(reverse('integration:staff_lms_account'))
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('secret', response.json()['message'])

    def test_access_step_message(self):
        map_user(self.dean, 80)
        self.add_courses(14, 15)
        response = self.client.post(reverse('integration:staff_lms_access'))
        self.assertEqual(response.json()['message'], 'Non-editing teacher access to 2 courses.')
        response = self.client.post(reverse('integration:staff_lms_access'))
        self.assertEqual(response.json()['message'], 'Non-editing teacher access to 2 courses is up to date.')

    def test_access_step_with_no_courses(self):
        map_user(self.dean, 80)
        response = self.client.post(reverse('integration:staff_lms_access'))
        self.assertIn('No courses are in the LMS yet', response.json()['message'])

    def test_open_signs_in_through_userkey(self):
        map_user(self.dean, 80)
        self.moodle.generate_login_token.return_value = SSO_URL
        response = self.client.get(reverse('integration:staff_lms_open'))
        self.assertRedirects(response, SSO_URL, fetch_redirect_response=False)

    def test_open_refuses_the_plain_login_fallback(self):
        map_user(self.dean, 80)
        self.moodle.generate_login_token.return_value = FALLBACK_URL
        response = self.client.get(reverse('integration:staff_lms_open'))
        self.assertRedirects(response, reverse('accounts:dashboard'), fetch_redirect_response=False)

    def test_open_without_an_account_points_back_to_the_button(self):
        response = self.client.get(reverse('integration:staff_lms_open'))
        self.assertRedirects(response, reverse('accounts:dashboard'), fetch_redirect_response=False)

    @override_settings(SCHOLR_LMS_ENABLED=False)
    def test_everything_is_hidden_when_the_lms_is_off(self):
        self.assertEqual(self.client.post(reverse('integration:staff_lms_account')).status_code, 404)
        self.assertEqual(self.client.get(reverse('integration:staff_lms_open')).status_code, 404)


class DashboardButtonTests(StaffPortalTestCase):
    def test_admin_dashboard_shows_the_button(self):
        self.client.force_login(make_user('ops', User.ADMIN, email='o@example.org'))
        response = self.client.get(reverse('accounts:admin_dashboard'))
        self.assertContains(response, 'LMS Portal')
        self.assertContains(response, reverse('integration:staff_lms_account'))

    def test_dean_dashboard_shows_the_button(self):
        self.client.force_login(make_user('dean.one', User.DEAN, email='d@example.org'))
        response = self.client.get(reverse('accounts:dean_dashboard'))
        self.assertContains(response, 'LMS Portal')

    @override_settings(SCHOLR_LMS_ENABLED=False)
    def test_button_is_hidden_when_the_lms_is_off(self):
        self.client.force_login(make_user('ops', User.ADMIN, email='o@example.org'))
        response = self.client.get(reverse('accounts:admin_dashboard'))
        self.assertNotContains(response, 'LMS Portal')
