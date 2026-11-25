from unittest import mock

from django.test import override_settings
from django.urls import reverse

from accounts.models import User
from integration.cohort_groups import desired_user_ids, staff_cohorts
from partners.forms import PartnershipForm
from partners.models import Cohort, Licence, PartnerOrganisation, Partnership, StaffAssignment
from partners.services import scope

from .base import PartnersTestCase
from .helpers import enrol, make_cohort, make_location, make_organisation, make_user


class NetworkStaffTestCase(PartnersTestCase):
    def setUp(self):
        super().setUp()
        self.kenya = make_organisation(name='Nairobi Fellowship', country='KE')
        self.nigeria = make_organisation(name='Lagos Grace', country='NG')
        self.chad = make_organisation(name="N'Djamena Hope", country='TD')
        self.kenya_location = make_location(organisation=self.kenya, name='Kibera Centre')
        self.nigeria_location = make_location(organisation=self.nigeria, name='Ikeja Centre')
        self.kenya_cohort = make_cohort(self.kenya_location, delivery=Cohort.Delivery.BLENDED)
        self.nigeria_cohort = make_cohort(self.nigeria_location, delivery=Cohort.Delivery.BLENDED)
        self.kenya_member = enrol(self.kenya_cohort)
        self.nigeria_member = enrol(self.nigeria_cohort)

        self.hub = make_user(User.HUB_ADMIN, first_name='Wanjiku', last_name='Mwangi')
        StaffAssignment.objects.create(user=self.hub, country='KE')
        StaffAssignment.objects.create(user=self.hub, organisation=self.chad)
        self.ambassador = make_user(User.AMBASSADOR, first_name='Tunde', last_name='Bakare')
        StaffAssignment.objects.create(user=self.ambassador, organisation=self.nigeria)

    def url(self, name, **kwargs):
        return reverse(f'partners:{name}', kwargs=kwargs)

    def org_url(self, name, organisation):
        return self.url(name, org_id=organisation.public_id)


class HubAdminReachTests(NetworkStaffTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hub)

    def test_lists_show_only_their_countries_and_extra_partners(self):
        for page in (self.url('organisation_list'), self.url('pipeline')):
            response = self.client.get(page)
            self.assertContains(response, 'Nairobi Fellowship')
            self.assertNotContains(response, 'Lagos Grace')
        self.assertContains(self.client.get(self.url('organisation_list')), 'Djamena Hope')
        licences = self.client.get(self.url('licence_list'))
        self.assertContains(licences, self.kenya_location.licence.number)
        self.assertNotContains(licences, self.nigeria_location.licence.number)

    def test_partners_outside_their_reach_do_not_exist_for_them(self):
        self.assertEqual(self.client.get(self.org_url('organisation_detail', self.nigeria)).status_code, 404)
        self.assertEqual(self.client.get(
            self.url('licence_detail', licence_id=self.nigeria_location.licence.public_id)).status_code, 404)
        self.assertEqual(self.client.post(self.org_url('organisation_archive', self.nigeria),
                                          {'archive': '1'}).status_code, 404)

    def test_network_figures_cover_only_their_reach(self):
        response = self.client.get(self.url('network'))
        self.assertEqual(response.context['totals']['partners'], 2)
        self.assertContains(response, 'Kibera Centre')
        self.assertNotContains(response, 'Ikeja Centre')

    @override_settings(SCHOLR_ONLINE_RESULTS_ENABLED=True)
    def test_results_cover_only_their_reach(self):
        response = self.client.get(self.url('results_overview'))
        self.assertContains(response, self.kenya_member.participant.last_name)
        self.assertNotContains(response, self.nigeria_member.participant.last_name)

    def test_portal_as_staff_inside_reach_only(self):
        self.assertEqual(scope.role_at(self.hub, self.kenya_location), scope.STAFF)
        self.assertIsNone(scope.role_at(self.hub, self.nigeria_location))
        kenya = reverse('partners:location_dashboard', kwargs={'location_id': self.kenya_location.public_id})
        nigeria = reverse('partners:location_dashboard', kwargs={'location_id': self.nigeria_location.public_id})
        self.assertEqual(self.client.get(kenya).status_code, 200)
        self.assertEqual(self.client.get(nigeria).status_code, 404)

    def test_lands_on_the_pipeline_with_no_map(self):
        self.assertRedirects(self.client.get(reverse('accounts:dashboard')), self.url('pipeline'),
                             fetch_redirect_response=False)
        page = self.client.get(self.url('pipeline'))
        self.assertNotContains(page, self.url('map'))
        self.assertContains(page, 'Hub admin')
        self.assertEqual(self.client.get(self.url('map')).status_code, 403)


class HubAdminEditingTests(NetworkStaffTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hub)

    def partner_form(self, **overrides):
        data = {'name': 'Mombasa Chapel', 'kind': PartnerOrganisation._meta.get_field('kind').choices[0][0],
                'country': 'KE', 'region': 'Coast', 'city': 'Mombasa', 'website': '', 'notes': ''}
        data.update(overrides)
        return data

    def test_adds_partners_only_in_their_countries(self):
        self.client.post(self.url('organisation_create'), self.partner_form())
        self.assertTrue(PartnerOrganisation.objects.filter(name='Mombasa Chapel').exists())
        response = self.client.post(self.url('organisation_create'), self.partner_form(name='Abuja Word', country='NG'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PartnerOrganisation.objects.filter(name='Abuja Word').exists())

    def test_country_list_only_offers_their_countries(self):
        choices = self.client.get(self.url('organisation_create')).context['form'].fields['country'].choices
        self.assertEqual([code for code, _name in choices if code], ['KE'])

    def test_cannot_move_a_partner_out_of_their_countries(self):
        self.client.post(self.org_url('organisation_edit', self.kenya),
                         self.partner_form(name='Nairobi Fellowship', country='NG'))
        self.kenya.refresh_from_db()
        self.assertEqual(self.kenya.country, 'KE')

    def test_edits_contacts_and_moves_the_partnership(self):
        self.client.post(self.org_url('contact_create', self.kenya),
                         {'name': 'Pastor Otieno', 'role_title': '', 'email': '', 'phone': '', 'is_primary': ''})
        self.assertTrue(self.kenya.contacts.filter(name='Pastor Otieno').exists())
        self.client.post(self.org_url('partnership_note', self.kenya), {'kind': 'note', 'body': 'Met the board'})
        self.assertTrue(Partnership.objects.get(organisation=self.kenya).events.filter(body='Met the board').exists())

    def test_licences_can_be_edited_but_not_activated_or_deleted(self):
        licence = self.kenya_location.licence
        for action in ('activate', 'suspend', 'terminate'):
            response = self.client.post(self.url('licence_action', licence_id=licence.public_id, action=action))
            self.assertEqual(response.status_code, 403, action)
        self.assertEqual(self.client.post(self.url('licence_delete', licence_id=licence.public_id)).status_code, 403)
        self.assertEqual(self.client.get(self.url('licence_edit', licence_id=licence.public_id)).status_code, 200)
        page = self.client.get(self.url('licence_detail', licence_id=licence.public_id))
        self.assertNotContains(page, self.url('licence_delete', licence_id=licence.public_id))
        self.assertTrue(Licence.objects.filter(pk=licence.pk).exists())

    def test_no_export_coverage_or_staff_management(self):
        self.assertEqual(self.client.get(self.url('network_export')).status_code, 403)
        self.assertEqual(self.client.get(self.url('staff_list')).status_code, 403)
        self.assertEqual(self.client.post(self.org_url('organisation_coverage', self.kenya),
                                          {'user': self.hub.pk, 'action': 'remove'}).status_code, 403)


class AmbassadorTests(NetworkStaffTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.ambassador)

    def test_sees_only_their_partners(self):
        page = self.client.get(self.url('organisation_list'))
        self.assertContains(page, 'Lagos Grace')
        self.assertNotContains(page, 'Nairobi Fellowship')
        self.assertEqual(self.client.get(self.org_url('organisation_detail', self.kenya)).status_code, 404)

    def test_logs_notes_and_sets_the_next_step(self):
        self.client.post(self.org_url('partnership_note', self.nigeria), {'kind': 'note', 'body': 'Called the pastor'})
        partnership = Partnership.objects.get(organisation=self.nigeria)
        self.assertTrue(partnership.events.filter(body='Called the pastor').exists())
        self.client.post(self.org_url('partnership_update', self.nigeria), {
            'owner': self.ambassador.pk, 'next_action': 'Visit in April', 'next_action_due': '',
            'source': partnership.source, 'expected_participants': '',
        })
        partnership.refresh_from_db()
        self.assertEqual((partnership.next_action, partnership.owner), ('Visit in April', self.ambassador))

    def test_changes_nothing_else(self):
        attempts = [
            self.org_url('organisation_edit', self.nigeria), self.org_url('organisation_archive', self.nigeria),
            self.org_url('partnership_move', self.nigeria), self.org_url('contact_create', self.nigeria),
            self.org_url('licence_create', self.nigeria), self.org_url('location_create', self.nigeria),
        ]
        for target in attempts:
            self.assertEqual(self.client.post(target, {'archive': '1'}).status_code, 403, target)

    def test_partner_page_offers_only_notes_and_next_step(self):
        page = self.client.get(self.org_url('organisation_detail', self.nigeria))
        self.assertContains(page, self.org_url('partnership_note', self.nigeria))
        self.assertContains(page, self.org_url('partnership_update', self.nigeria))
        for hidden in ('organisation_edit', 'partnership_move', 'contact_create', 'licence_create'):
            self.assertNotContains(page, self.org_url(hidden, self.nigeria))
        self.assertNotContains(page, reverse('partners:location_dashboard',
                                             kwargs={'location_id': self.nigeria_location.public_id}))

    def test_a_partner_they_add_is_assigned_to_them(self):
        self.client.post(self.url('organisation_create'), {
            'name': 'Kumasi Light', 'kind': PartnerOrganisation._meta.get_field('kind').choices[0][0],
            'country': 'GH', 'region': '', 'city': '', 'website': '', 'notes': '',
        })
        added = PartnerOrganisation.objects.get(name='Kumasi Light')
        self.assertIn(added, scope.organisations_in_reach(self.ambassador))

    def test_no_results_map_or_portal(self):
        with override_settings(SCHOLR_ONLINE_RESULTS_ENABLED=True):
            self.assertEqual(self.client.get(self.url('results_overview')).status_code, 403)
        self.assertEqual(self.client.get(self.url('map')).status_code, 403)
        dashboard = reverse('partners:location_dashboard', kwargs={'location_id': self.nigeria_location.public_id})
        self.assertEqual(self.client.get(dashboard).status_code, 404)

    def test_can_own_their_partners_next_step(self):
        form = PartnershipForm(instance=Partnership.objects.get(organisation=self.nigeria))
        self.assertIn(self.ambassador, form.fields['owner'].queryset)
        form = PartnershipForm(instance=Partnership.objects.get(organisation=self.kenya))
        self.assertNotIn(self.ambassador, form.fields['owner'].queryset)
        self.assertIn(self.hub, form.fields['owner'].queryset)


@override_settings(SCHOLR_COHORT_GROUPS_ENABLED=True, MOODLE_COHORT_VIEWER_ROLE_ID=9,
                   SCHOLR_STAFF_LMS_PORTAL_ENABLED=True)
class MoodleAccessTests(NetworkStaffTestCase):
    def test_both_roles_open_the_lms_as_cohort_viewers(self):
        from integration.models import MoodleInstance
        from integration.staff_access import StaffLMSAccess
        MoodleInstance.objects.create(name='Test', base_url='http://moodle.test', ws_token='x',
                                      is_active=True, is_primary=True)
        for user in (self.hub, self.ambassador):
            self.assertTrue(user.can_open_lms_as_staff())
            self.assertEqual(StaffLMSAccess(user)._course_role(), 9)

    @override_settings(MOODLE_COHORT_VIEWER_ROLE_ID=None)
    def test_not_without_the_cohort_viewer_role(self):
        self.assertFalse(self.hub.can_open_lms_as_staff())

    def test_cohort_groups_follow_reach(self):
        self.assertIn(self.hub.pk, desired_user_ids(self.kenya_cohort))
        self.assertNotIn(self.hub.pk, desired_user_ids(self.nigeria_cohort))
        self.assertIn(self.ambassador.pk, desired_user_ids(self.nigeria_cohort))
        self.assertNotIn(self.ambassador.pk, desired_user_ids(self.kenya_cohort))
        self.assertEqual(list(staff_cohorts(self.ambassador)), [self.nigeria_cohort])

    def test_changing_reach_queues_a_group_sync(self):
        with mock.patch('integration.tasks.queue_cohort_sync') as queued:
            StaffAssignment.objects.create(user=self.ambassador, organisation=self.kenya)
            StaffAssignment.objects.filter(user=self.hub, country='KE').delete()
        cohort_ids = {pk for call in queued.call_args_list for pk in call.args[0]}
        self.assertEqual(cohort_ids, {self.kenya_cohort.pk})


class LeaderResultsTests(NetworkStaffTestCase):
    @override_settings(SCHOLR_ONLINE_RESULTS_ENABLED=True)
    def test_leaders_see_every_cohorts_results(self):
        self.client.force_login(make_user(User.LEADER))
        response = self.client.get(self.url('results_overview'))
        self.assertContains(response, self.kenya_member.participant.last_name)
        self.assertContains(response, self.nigeria_member.participant.last_name)
