from django.test import override_settings
from django.urls import reverse

from accounts.models import LoginAttempt, User
from partners.models import Partnership
from partners.services import people

from .base import PartnersTestCase
from .helpers import make_location, make_organisation, make_staff, make_user


class LeaderTestCase(PartnersTestCase):
    def setUp(self):
        super().setUp()
        self.leader = make_user(User.LEADER, first_name='Lee', last_name='Leader')
        self.organisation = make_organisation(name='Nairobi Fellowship', country='KE')
        self.location = make_location(organisation=self.organisation, name='Kibera Centre')
        Partnership.objects.filter(organisation=self.organisation).update(next_action='Visit in March')
        self.client.force_login(self.leader)

    def url(self, name, **kwargs):
        return reverse(f'partners:{name}', kwargs=kwargs)


class LeaderReadsEverythingTests(LeaderTestCase):
    @override_settings(SCHOLR_ONLINE_RESULTS_ENABLED=True)
    def test_every_network_page_opens(self):
        pages = [
            self.url('pipeline'), self.url('organisation_list'),
            self.url('organisation_detail', org_id=self.organisation.public_id),
            self.url('licence_list'), self.url('licence_detail', licence_id=self.location.licence.public_id),
            self.url('network'), self.url('results_overview'), self.url('map'), self.url('map_data'),
        ]
        for page in pages:
            self.assertEqual(self.client.get(page).status_code, 200, page)

    def test_lands_on_the_global_dashboard(self):
        response = self.client.get(reverse('accounts:dashboard'))
        self.assertRedirects(response, reverse('accounts:admin_dashboard'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('accounts:admin_dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('accounts:analytics')).status_code, 200)

    def test_navigation_offers_the_network(self):
        page = self.client.get(reverse('accounts:admin_dashboard'))
        for name in ('pipeline', 'organisation_list', 'licence_list', 'network', 'map'):
            self.assertContains(page, self.url(name))
        self.assertNotContains(page, self.url('staff_list'))


class LeaderChangesNothingTests(LeaderTestCase):
    def test_partner_page_has_no_edit_controls_but_shows_the_next_step(self):
        page = self.client.get(self.url('organisation_detail', org_id=self.organisation.public_id))
        for hidden in (
            self.url('organisation_edit', org_id=self.organisation.public_id),
            self.url('organisation_archive', org_id=self.organisation.public_id),
            self.url('partnership_move', org_id=self.organisation.public_id),
            self.url('partnership_update', org_id=self.organisation.public_id),
            self.url('partnership_note', org_id=self.organisation.public_id),
            self.url('contact_create', org_id=self.organisation.public_id),
            self.url('licence_create', org_id=self.organisation.public_id),
            self.url('location_create', org_id=self.organisation.public_id),
            self.url('location_edit', location_id=self.location.public_id),
            self.url('location_dashboard', location_id=self.location.public_id),
        ):
            self.assertNotContains(page, hidden)
        self.assertContains(page, 'Visit in March')
        self.assertContains(page, 'Kibera Centre')

    def test_licence_page_has_no_actions(self):
        licence = self.location.licence
        page = self.client.get(self.url('licence_detail', licence_id=licence.public_id))
        self.assertNotContains(page, self.url('licence_edit', licence_id=licence.public_id))
        self.assertNotContains(page, 'licences/' + str(licence.public_id) + '/suspend/')
        self.assertNotContains(page, self.url('licence_delete', licence_id=licence.public_id))

    def test_no_contacts_export(self):
        self.assertNotContains(self.client.get(self.url('network')), self.url('network_export'))
        self.assertEqual(self.client.get(self.url('network_export')).status_code, 403)

    @override_settings(SCHOLR_ONLINE_RESULTS_ENABLED=True)
    def test_results_without_the_refresh_button(self):
        self.assertNotContains(self.client.get(self.url('results_overview') + '?view=online'), 'Refresh from LMS')

    def test_changes_are_refused_even_when_sent_directly(self):
        attempts = [
            self.url('organisation_archive', org_id=self.organisation.public_id),
            self.url('partnership_note', org_id=self.organisation.public_id),
            self.url('organisation_create'),
            self.url('pipeline'),
        ]
        for target in attempts:
            self.assertEqual(self.client.post(target, {'body': 'x', 'archive': '1'}).status_code, 403, target)
        self.organisation.refresh_from_db()
        self.assertFalse(self.organisation.is_archived)

    def test_dashboard_leaves_out_verification_and_security(self):
        LoginAttempt.objects.create(username='someone', success=False, ip_address='10.0.0.1')
        make_user(User.STUDENT, is_verified=False)
        page = self.client.get(reverse('accounts:admin_dashboard'))
        self.assertNotContains(page, 'awaiting verification</a>')
        self.assertNotContains(page, reverse('accounts:student_list'))
        self.assertNotContains(page, '10.0.0.1')
        self.assertNotContains(page, reverse('accounts:dean_dashboard'))
        self.assertNotContains(page, reverse('integration:sync_dashboard'))

    def test_programme_staff_keep_every_control(self):
        self.client.force_login(make_staff())
        page = self.client.get(self.url('organisation_detail', org_id=self.organisation.public_id))
        self.assertContains(page, self.url('organisation_edit', org_id=self.organisation.public_id))
        self.assertContains(page, self.url('location_dashboard', location_id=self.location.public_id))
        self.assertContains(self.client.get(self.url('network')), self.url('network_export'))


class StaffAutoVerifiedTests(PartnersTestCase):
    def test_staff_added_by_an_admin_are_verified_by_them(self):
        admin = make_user(User.ADMIN)
        member = people.add_staff_member(admin, 'Grace', 'Okonkwo', User.LEADER, email='grace@example.org')
        member.refresh_from_db()
        self.assertTrue(member.is_verified)
        self.assertEqual(member.verified_by, admin)
        self.assertIsNotNone(member.verified_at)

    def test_staff_page_shows_the_username(self):
        admin = make_user(User.ADMIN)
        self.client.force_login(admin)
        member = make_user(User.LEADER)
        self.assertContains(self.client.get(reverse('partners:staff_detail', kwargs={'pk': member.pk})),
                            member.username)
