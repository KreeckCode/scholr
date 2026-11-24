"""
Every push deploys to both METP and SNAP, so METP-only features must be off unless METP's
.env turns them on. These tests use the defaults SNAP runs with, and switch partners on as
the worst case, to prove nothing leaks.
"""
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from accounts.models import User
from integration.models import MoodleInstance
from integration.tasks import sync_online_results_task
from partners.models import Cohort
from partners.tests.base import PartnersTestCase
from partners.tests.helpers import add_member, make_cohort, make_location, make_staff, make_user, seed_courses


class DefaultsTests(PartnersTestCase):
    def setUp(self):
        super().setUp()
        MoodleInstance.objects.create(name='Test', base_url='http://moodle.test', ws_token='x',
                                      is_active=True, is_primary=True)
        seed_courses(2)
        self.location = make_location()
        self.coordinator = add_member(self.location)
        self.cohort = make_cohort(self.location, delivery=Cohort.Delivery.BLENDED)
        self.admin = make_user(User.ADMIN)
        self.dean = make_user(User.DEAN)

    def cohort_url(self, name):
        return reverse(f'partners:{name}', kwargs={
            'location_id': self.location.public_id, 'cohort_id': self.cohort.public_id,
        })

    def test_staff_lms_portal_is_off(self):
        for user in (self.admin, self.dean, make_staff(), self.coordinator):
            self.assertFalse(user.can_open_lms_as_staff(), user.role)
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(reverse('accounts:admin_dashboard')), 'LMS Portal')
        self.assertEqual(self.client.post(reverse('integration:staff_lms_account')).status_code, 404)
        self.assertEqual(self.client.post(reverse('integration:staff_lms_access')).status_code, 404)
        self.assertEqual(self.client.get(reverse('integration:staff_lms_open')).status_code, 404)

    @override_settings(SCHOLR_COHORT_GROUPS_ENABLED=True, MOODLE_COHORT_VIEWER_ROLE_ID=9)
    def test_partner_lms_access_also_needs_the_portal_switch(self):
        self.assertFalse(self.coordinator.can_open_lms_as_staff())

    def test_results_overviews_are_off(self):
        self.client.force_login(self.coordinator)
        self.assertEqual(self.client.get(self.cohort_url('cohort_results_overview')).status_code, 404)
        detail = self.client.get(self.cohort_url('cohort_detail'))
        self.assertContains(detail, self.cohort_url('cohort_results'))
        self.assertNotContains(detail, self.cohort_url('cohort_results_overview'))

        self.client.force_login(make_staff())
        self.assertEqual(self.client.get(reverse('partners:results_overview')).status_code, 404)
        self.assertNotContains(self.client.get(reverse('partners:pipeline')), reverse('partners:results_overview'))

    def test_entry_page_has_no_online_column(self):
        self.client.force_login(self.coordinator)
        response = self.client.get(self.cohort_url('cohort_results'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Online quizzes')
        self.assertNotContains(response, self.cohort_url('cohort_results_overview'))

    def test_refresh_and_nightly_sync_do_nothing(self):
        self.client.force_login(make_staff())
        with mock.patch('integration.tasks.sync_online_results_task.delay') as delay:
            self.assertEqual(self.client.post(reverse('integration:refresh_online_results')).status_code, 404)
        delay.assert_not_called()
        with mock.patch('integration.services.MoodleClient') as client_class:
            self.assertEqual(sync_online_results_task.apply().get(), {'skipped': 'online results are off'})
        client_class.assert_not_called()

    def test_purchases_and_registration_are_unchanged(self):
        self.assertEqual(self.client.get('/courses/').status_code, 200)
        self.assertEqual(self.client.get(reverse('accounts:register')).status_code, 200)
