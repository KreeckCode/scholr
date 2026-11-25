import uuid

from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse
from django.views import View

from accounts.models import RoleChange, User
from accounts.roles import RoleChangeError, change_role, role_changed
from partners import urls as partner_urls
from partners.exceptions import PartnersError
from partners.mixins import NetworkStaffMixin
from partners.models import Cohort, LocationEvent, Partnership, StaffAssignment
from partners.services import reach, scope

from .base import PartnersTestCase
from .helpers import (
    add_member, enrol, make_cohort, make_location, make_organisation, make_staff, make_user,
)


def give(user, *, countries=(), organisations=()):
    for code in countries:
        StaffAssignment.objects.create(user=user, country=code)
    for organisation in organisations:
        StaffAssignment.objects.create(user=user, organisation=organisation)
    return user


class NetworkTestCase(PartnersTestCase):
    def setUp(self):
        super().setUp()
        self.kenya = make_organisation(country='KE')
        self.kenya_two = make_organisation(country='KE')
        self.nigeria = make_organisation(country='NG')
        self.chad = make_organisation(country='TD')
        self.admin = make_user(User.ADMIN)
        self.staff = make_staff()
        self.hub = give(make_user(User.HUB_ADMIN), countries=['KE'], organisations=[self.chad])
        self.ambassador = give(make_user(User.AMBASSADOR), countries=['NG'], organisations=[self.kenya_two])
        self.leader = make_user(User.LEADER)


class ReachTests(NetworkTestCase):
    def reach(self, user):
        return set(scope.organisations_in_reach(user))

    def test_global_roles_see_every_partner(self):
        everyone = {self.kenya, self.kenya_two, self.nigeria, self.chad}
        for user in (self.admin, self.staff, self.leader):
            self.assertEqual(self.reach(user), everyone, user.role)

    def test_hub_admin_sees_their_countries_and_extra_partners(self):
        self.assertEqual(self.reach(self.hub), {self.kenya, self.kenya_two, self.chad})
        self.assertEqual(scope.assigned_countries(self.hub), ['KE'])

    def test_ambassador_sees_only_assigned_partners(self):
        # The leftover Nigeria country row must not widen an ambassador's reach
        self.assertEqual(self.reach(self.ambassador), {self.kenya_two})
        self.assertEqual(scope.assigned_countries(self.ambassador), [])

    def test_everyone_else_sees_nothing(self):
        partner = add_member(make_location(organisation=self.kenya))
        inactive = give(make_user(User.HUB_ADMIN, is_active=False), countries=['KE'])
        for user in (partner, make_user(User.STUDENT), make_user(User.DEAN), inactive):
            self.assertEqual(self.reach(user), set(), user.role)

    def test_a_location_belongs_to_its_partners_hub_wherever_it_is(self):
        across_the_border = make_location(organisation=self.kenya, country='UG')
        self.assertIn(across_the_border, scope.locations_in_reach(self.hub))
        uganda_hub = give(make_user(User.HUB_ADMIN), countries=['UG'])
        self.assertNotIn(across_the_border, scope.locations_in_reach(uganda_hub))

    def test_everything_hangs_off_the_partner(self):
        location = make_location(organisation=self.nigeria)
        cohort = make_cohort(location)
        licence = location.licence
        Partnership.objects.get_or_create(organisation=self.nigeria)
        self.assertFalse(scope.cohorts_in_reach(self.hub).filter(pk=cohort.pk).exists())
        self.assertFalse(scope.licences_in_reach(self.hub).filter(pk=licence.pk).exists())
        self.assertFalse(scope.partnerships_in_reach(self.hub).filter(organisation=self.nigeria).exists())
        self.assertTrue(scope.partnerships_in_reach(self.hub).filter(organisation=self.kenya).exists())
        self.assertTrue(scope.cohorts_in_reach(self.leader).filter(pk=cohort.pk).exists())

    def test_who_can_change_and_place_partners(self):
        self.assertTrue(scope.can_change_organisation(self.hub, self.kenya))
        self.assertFalse(scope.can_change_organisation(self.hub, self.nigeria))
        for user in (self.ambassador, self.leader):
            self.assertFalse(scope.can_change_organisation(user, self.kenya_two))
        self.assertTrue(scope.can_place_partner_in(self.hub, 'KE'))
        self.assertFalse(scope.can_place_partner_in(self.hub, 'TD'))
        self.assertTrue(scope.can_place_partner_in(self.staff, 'TD'))
        self.assertFalse(scope.can_place_partner_in(self.leader, 'KE'))

    def test_assignments_must_be_a_country_or_a_partner(self):
        user = make_user(User.HUB_ADMIN)
        for bad in ({'country': 'KE', 'organisation': self.kenya}, {}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                StaffAssignment.objects.create(user=user, **bad)
        StaffAssignment.objects.create(user=user, country='KE')
        with self.assertRaises(IntegrityError), transaction.atomic():
            StaffAssignment.objects.create(user=user, country='KE')


class GuardTests(NetworkTestCase):
    def view(self, roles=frozenset()):
        class Probe(NetworkStaffMixin, View):
            network_roles = roles

            def get(self, request):
                return HttpResponse('ok')

            def post(self, request):
                return HttpResponse('changed')
        return Probe.as_view()

    def call(self, view, user, method='get'):
        request = getattr(RequestFactory(), method)('/probe/')
        request.user = user
        return view(request)

    def test_new_roles_are_refused_unless_the_view_names_them(self):
        view = self.view()
        for user in (self.admin, self.staff):
            self.assertEqual(self.call(view, user).status_code, 200)
        for user in (self.hub, self.ambassador, self.leader):
            with self.assertRaises(PermissionDenied):
                self.call(view, user)

    def test_named_roles_pass_but_leaders_cannot_change_anything(self):
        view = self.view(frozenset({User.HUB_ADMIN, User.LEADER}))
        self.assertEqual(self.call(view, self.hub, 'post').status_code, 200)
        self.assertEqual(self.call(view, self.leader).status_code, 200)
        for method in ('post', 'put', 'patch', 'delete'):
            with self.assertRaises(PermissionDenied):
                self.call(view, self.leader, method)

    def test_signed_out_people_are_sent_to_sign_in(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertEqual(self.call(self.view(), AnonymousUser()).status_code, 302)


class FailClosedSweepTests(NetworkTestCase):
    """
    Until a phase opens a page to them, the new roles must get nothing from the partners
    app. This walks every partners URL, so a page added later is covered automatically.
    """

    OPEN_TO_EVERYONE = {
        'enquire', 'enquiry_thanks', 'activate', 'activate_done',
        'offline_app', 'service_worker', 'manifest',
        'api_me',
    }
    # Pages built only from location memberships, which the new roles do not have
    EMPTY_FOR_THEM = {'portal_home'}

    def kwargs_for(self, pattern):
        location = make_location(organisation=self.kenya)
        cohort = make_cohort(location, delivery=Cohort.Delivery.BLENDED)
        membership = enrol(cohort)
        event = LocationEvent.objects.filter(location=location).first()
        values = {
            'org_id': self.kenya.public_id,
            'location_id': location.public_id,
            'cohort_id': cohort.public_id,
            'licence_id': location.licence.public_id,
            'participant_id': membership.participant.public_id,
            'membership_id': membership.public_id,
            'event_id': event.public_id if event else uuid.uuid4(),
            'session_id': uuid.uuid4(),
            'op_id': uuid.uuid4(),
            'pk': 1,
            'action': 'approve',
            'uidb64': 'MQ',
            'token': 'x-y',
        }
        return {name: values[name] for name in pattern.pattern.converters} if hasattr(pattern.pattern, 'converters') \
            else {}

    def test_every_partners_page_refuses_the_new_roles(self):
        leaks = []
        for pattern in partner_urls.urlpatterns:
            if pattern.name in self.OPEN_TO_EVERYONE:
                continue
            url = reverse(f'partners:{pattern.name}', kwargs=self.kwargs_for(pattern))
            for user in (self.hub, self.ambassador, self.leader):
                self.client.force_login(user)
                for method in ('get', 'post'):
                    response = getattr(self.client, method)(url)
                    if not 200 <= response.status_code < 300:
                        continue
                    if pattern.name in self.EMPTY_FOR_THEM and self.shows_nothing(response):
                        continue
                    leaks.append(f'{user.role} {method.upper()} {pattern.name} -> {response.status_code}')
        self.assertEqual(leaks, [])

    def shows_nothing(self, response):
        html = response.content.decode()
        return 'No locations yet' in html and not any(
            location.name in html for location in self.kenya.locations.all()
        )


class RoleChangeTests(NetworkTestCase):
    def setUp(self):
        super().setUp()
        self.dean = make_user(User.DEAN)

    def test_admin_changes_a_staff_role_and_it_is_recorded(self):
        heard = []
        role_changed.connect(lambda **kw: heard.append((kw['old_role'], kw['new_role'])), weak=False,
                             dispatch_uid='test_listener')
        self.addCleanup(role_changed.disconnect, dispatch_uid='test_listener')

        change = change_role(self.dean, User.HUB_ADMIN, self.admin, note='Covering East Africa')
        self.dean.refresh_from_db()
        self.assertEqual(self.dean.role, User.HUB_ADMIN)
        self.assertEqual((change.from_role, change.to_role, change.changed_by), (User.DEAN, User.HUB_ADMIN, self.admin))
        self.assertEqual(change.note, 'Covering East Africa')
        self.assertEqual(heard, [(User.DEAN, User.HUB_ADMIN)])

    def test_guard_rails(self):
        cases = [
            (self.dean, User.HUB_ADMIN, self.staff, 'Only system administrators'),
            (self.admin, User.LEADER, self.admin, 'your own role'),
            (self.dean, User.DEAN, self.admin, 'already has that role'),
            (self.dean, 'emperor', self.admin, 'not a role'),
            (make_user(User.STUDENT), User.HUB_ADMIN, self.admin, 'Only staff roles'),
            (self.dean, User.STUDENT, self.admin, 'Only staff roles'),
            (add_member(make_location()), User.AMBASSADOR, self.admin, 'Only staff roles'),
        ]
        for user, role, actor, message in cases:
            with self.assertRaisesMessage(RoleChangeError, message):
                change_role(user, role, actor)
        self.assertFalse(RoleChange.objects.exists())

    def test_the_last_admin_cannot_be_demoted(self):
        User.objects.filter(role=User.ADMIN).exclude(pk=self.admin.pk).delete()
        with self.assertRaisesMessage(RoleChangeError, 'your own role'):
            change_role(self.admin, User.LEADER, self.admin)
        for actor in (self.staff, self.leader, make_user(User.ADMIN, is_active=False)):
            with self.assertRaises(RoleChangeError):
                change_role(self.admin, User.LEADER, actor)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, User.ADMIN)

    def test_an_admin_demoted_a_moment_ago_cannot_act(self):
        second = make_user(User.ADMIN)
        stale_copy = User.objects.get(pk=second.pk)
        change_role(second, User.LEADER, self.admin)
        with self.assertRaisesMessage(RoleChangeError, 'Only system administrators'):
            change_role(self.admin, User.LEADER, stale_copy)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, User.ADMIN)

    def test_reach_the_new_role_does_not_use_is_cleared(self):
        change_role(self.hub, User.AMBASSADOR, self.admin)
        self.assertEqual(list(self.hub.network_assignments.values_list('country', flat=True)), [''])
        change_role(self.hub, User.LEADER, self.admin)
        self.assertFalse(self.hub.network_assignments.exists())

    def test_cached_lms_access_is_forgotten(self):
        from integration.staff_access import _access_cache_key
        cache.set(_access_cache_key(self.dean), 'old fingerprint')
        change_role(self.dean, User.LEADER, self.admin)
        self.assertIsNone(cache.get(_access_cache_key(self.dean)))


class AssignmentServiceTests(NetworkTestCase):
    def test_set_countries_replaces_the_list(self):
        added, removed = reach.set_countries(self.hub, ['ng', 'TD '], self.admin)
        self.assertEqual((added, removed), (['NG', 'TD'], ['KE']))
        self.assertEqual(sorted(scope.assigned_countries(self.hub)), ['NG', 'TD'])
        self.assertTrue(self.hub.network_assignments.filter(organisation=self.chad).exists())

    def test_set_organisations_replaces_the_list(self):
        reach.set_organisations(self.ambassador, [self.kenya, self.nigeria], self.admin)
        self.assertEqual(set(scope.organisations_in_reach(self.ambassador)), {self.kenya, self.nigeria})
        self.assertEqual(
            self.ambassador.network_assignments.get(organisation=self.kenya).added_by, self.admin,
        )

    def test_refusals(self):
        with self.assertRaisesMessage(PartnersError, 'Only system administrators'):
            reach.set_countries(self.hub, ['KE'], self.staff)
        with self.assertRaisesMessage(PartnersError, 'Unknown country'):
            reach.set_countries(self.hub, ['XX'], self.admin)
        with self.assertRaisesMessage(PartnersError, 'Only hub administrators cover countries'):
            reach.set_countries(self.ambassador, ['KE'], self.admin)
        with self.assertRaisesMessage(PartnersError, 'Only hub administrators and ambassadors'):
            reach.set_organisations(self.leader, [self.kenya], self.admin)
