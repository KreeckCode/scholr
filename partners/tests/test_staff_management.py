from django.urls import reverse

from accounts.models import RoleChange, User
from partners.models import StaffAssignment
from partners.services import scope

from .base import PartnersTestCase
from .helpers import add_member, make_location, make_organisation, make_staff, make_user


class StaffManagementTestCase(PartnersTestCase):
    def setUp(self):
        super().setUp()
        self.admin = make_user(User.ADMIN, first_name='Ada', last_name='Admin')
        self.client.force_login(self.admin)
        self.kenya = make_organisation(name='Nairobi Fellowship', country='KE', region='Nairobi')
        self.kenya_coast = make_organisation(name='Mombasa Chapel', country='KE', region='Coast')
        self.nigeria = make_organisation(name='Lagos Grace', country='NG', region='Lagos')
        self.hub = make_user(User.HUB_ADMIN, first_name='Hana', last_name='Hub')
        StaffAssignment.objects.create(user=self.hub, country='KE')
        self.ambassador = make_user(User.AMBASSADOR, first_name='Amos', last_name='Ambassador')

    def url(self, name, **kwargs):
        return reverse(f'partners:{name}', kwargs=kwargs)


class AccessTests(StaffManagementTestCase):
    def test_only_system_administrators_get_in(self):
        pages = [self.url('staff_list'), self.url('staff_create'), self.url('staff_detail', pk=self.hub.pk),
                 self.url('staff_partners', pk=self.ambassador.pk)]
        for page in pages:
            self.assertEqual(self.client.get(page).status_code, 200, page)
        for user in (make_staff(), self.hub, self.ambassador, make_user(User.LEADER), make_user(User.STUDENT)):
            self.client.force_login(user)
            for page in pages:
                self.assertEqual(self.client.get(page).status_code, 403, (user.role, page))

    def test_people_outside_the_staff_roles_are_not_found(self):
        for user in (make_user(User.STUDENT), add_member(make_location())):
            self.assertEqual(self.client.get(self.url('staff_detail', pk=user.pk)).status_code, 404)

    def test_staff_tab_only_for_system_administrators(self):
        self.assertContains(self.client.get(self.url('pipeline')), self.url('staff_list'))
        self.client.force_login(make_staff())
        self.assertNotContains(self.client.get(self.url('pipeline')), self.url('staff_list'))


class AddStaffTests(StaffManagementTestCase):
    def post(self, **overrides):
        data = {'first_name': 'Ngozi', 'last_name': 'Okafor', 'role': User.HUB_ADMIN,
                'email': 'Ngozi@Example.org', 'phone': ''}
        data.update(overrides)
        return self.client.post(self.url('staff_create'), data)

    def test_adds_an_account_with_a_one_time_link_and_a_history_entry(self):
        response = self.post()
        member = User.objects.get(email='ngozi@example.org')
        # Not fetching the page here: that would use up the one-time link checked below
        self.assertRedirects(response, self.url('staff_detail', pk=member.pk), fetch_redirect_response=False)
        self.assertEqual(member.role, User.HUB_ADMIN)
        self.assertFalse(member.has_usable_password())
        created = RoleChange.objects.get(user=member)
        self.assertEqual((created.from_role, created.to_role, created.changed_by), ('', User.HUB_ADMIN, self.admin))

        page = self.client.get(self.url('staff_detail', pk=member.pk))
        self.assertContains(page, 'Set-password link for Ngozi Okafor')
        self.assertContains(page, 'Added as')
        self.assertNotContains(self.client.get(self.url('staff_detail', pk=member.pk)), 'Set-password link for')

    def test_an_existing_account_is_never_duplicated(self):
        make_user(User.STUDENT, email='ngozi@example.org')
        response = self.post()
        self.assertContains(response, 'change their role instead')
        self.assertEqual(User.objects.filter(email__iexact='ngozi@example.org').count(), 1)

    def test_needs_a_way_to_send_the_link(self):
        self.assertContains(self.post(email='', phone=''), 'Give an email address or a phone number')

    def test_partner_and_student_roles_cannot_be_created_here(self):
        for role in (User.PARTNER, User.STUDENT, User.DEAN):
            self.assertEqual(self.post(role=role, email=f'{role}@example.org').status_code, 200)
            self.assertFalse(User.objects.filter(email=f'{role}@example.org').exists())


class RoleTests(StaffManagementTestCase):
    def test_change_role_with_a_reason(self):
        response = self.client.post(self.url('staff_role', pk=self.hub.pk),
                                    {'new_role': User.AMBASSADOR, 'note': 'Moving to Lagos'}, follow=True)
        self.assertContains(response, 'is now Ambassador')
        self.hub.refresh_from_db()
        self.assertEqual(self.hub.role, User.AMBASSADOR)
        self.assertFalse(self.hub.network_assignments.exclude(country='').exists())
        self.assertContains(response, 'Moving to Lagos')

    def test_own_role_cannot_be_changed_from_the_page(self):
        page = self.client.get(self.url('staff_detail', pk=self.admin.pk))
        self.assertContains(page, 'You cannot change your own role')
        response = self.client.post(self.url('staff_role', pk=self.admin.pk), {'new_role': User.LEADER}, follow=True)
        self.assertContains(response, 'You cannot change your own role')
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, User.ADMIN)


class ReachEditingTests(StaffManagementTestCase):
    def test_hub_admin_countries(self):
        response = self.client.post(self.url('staff_countries', pk=self.hub.pk),
                                    {'countries': ['KE', 'UG']}, follow=True)
        self.assertContains(response, '1 country added')
        self.assertEqual(sorted(scope.assigned_countries(self.hub)), ['KE', 'UG'])

    def test_ambassadors_cannot_be_given_countries(self):
        response = self.client.post(self.url('staff_countries', pk=self.ambassador.pk),
                                    {'countries': ['KE']}, follow=True)
        self.assertContains(response, 'Only hub administrators cover countries')
        self.assertFalse(self.ambassador.network_assignments.exists())

    def test_picker_filters_by_country_and_region(self):
        page = self.client.get(self.url('staff_partners', pk=self.ambassador.pk), {'country': 'KE', 'region': 'coast'})
        self.assertContains(page, 'Mombasa Chapel')
        self.assertNotContains(page, 'Nairobi Fellowship')
        self.assertNotContains(page, 'Lagos Grace')

    def test_saving_a_filtered_list_keeps_what_was_filtered_out(self):
        StaffAssignment.objects.create(user=self.ambassador, organisation=self.nigeria)
        StaffAssignment.objects.create(user=self.ambassador, organisation=self.kenya)
        self.client.post(self.url('staff_partners', pk=self.ambassador.pk), {
            'country': 'KE',
            'shown': [self.kenya.public_id, self.kenya_coast.public_id],
            'partners': [self.kenya_coast.public_id],
        })
        self.assertEqual(set(scope.organisations_in_reach(self.ambassador)), {self.nigeria, self.kenya_coast})

    def test_a_forged_tick_for_a_partner_not_shown_is_ignored(self):
        self.client.post(self.url('staff_partners', pk=self.ambassador.pk), {
            'shown': [self.kenya.public_id], 'partners': [self.kenya.public_id, self.nigeria.public_id],
        })
        self.assertEqual(set(scope.organisations_in_reach(self.ambassador)), {self.kenya})

    def test_return_address_is_rebuilt_not_trusted(self):
        response = self.client.post(self.url('staff_partners', pk=self.ambassador.pk), {
            'shown': [], 'country': 'KE', 'back': 'https://evil.example/',
        })
        self.assertEqual(response['Location'], self.url('staff_partners', pk=self.ambassador.pk) + '?country=KE')

    def test_list_shows_what_each_person_covers(self):
        StaffAssignment.objects.create(user=self.ambassador, organisation=self.nigeria)
        page = self.client.get(self.url('staff_list'))
        self.assertContains(page, 'Kenya')
        self.assertContains(page, '1 partner')
        self.assertContains(page, 'All partners')


class ActivationLinkTests(StaffManagementTestCase):
    def test_new_link_for_someone_not_activated(self):
        member = make_user(User.LEADER, email='lee@example.org')
        member.set_unusable_password()
        member.save()
        page = self.client.post(self.url('staff_activation_link', pk=member.pk), follow=True)
        self.assertContains(page, '/partners/activate/')

    def test_no_link_once_a_password_is_set(self):
        member = make_user(User.LEADER)
        page = self.client.post(self.url('staff_activation_link', pk=member.pk), follow=True)
        self.assertContains(page, 'already set a password')


class CoverageTests(StaffManagementTestCase):
    def detail(self):
        return self.client.get(self.url('organisation_detail', org_id=self.kenya.public_id))

    def test_partner_page_lists_who_covers_it(self):
        StaffAssignment.objects.create(user=self.ambassador, organisation=self.kenya)
        page = self.detail()
        self.assertContains(page, 'Hana Hub')
        self.assertContains(page, 'Hub Administrator for Kenya')
        self.assertContains(page, 'Amos Ambassador')

    def test_admin_adds_and_removes_from_the_partner_page(self):
        coverage = self.url('organisation_coverage', org_id=self.kenya.public_id)
        self.client.post(coverage, {'user': self.ambassador.pk, 'action': 'add'})
        self.assertIn(self.kenya, scope.organisations_in_reach(self.ambassador))
        self.client.post(coverage, {'user': self.ambassador.pk, 'action': 'remove'})
        self.assertNotIn(self.kenya, scope.organisations_in_reach(self.ambassador))

    def test_programme_staff_see_coverage_but_cannot_change_it(self):
        self.client.force_login(make_staff())
        page = self.detail()
        self.assertContains(page, 'Covered by')
        self.assertNotContains(page, 'Add an ambassador or hub administrator')
        response = self.client.post(self.url('organisation_coverage', org_id=self.kenya.public_id),
                                    {'user': self.ambassador.pk, 'action': 'add'})
        self.assertEqual(response.status_code, 403)

    def test_only_scoped_roles_can_be_added(self):
        leader = make_user(User.LEADER)
        response = self.client.post(self.url('organisation_coverage', org_id=self.kenya.public_id),
                                    {'user': leader.pk, 'action': 'add'})
        self.assertEqual(response.status_code, 404)
