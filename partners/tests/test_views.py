import json
import uuid
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from accounts.models import User
from partners.models import Cohort, CourseResult, LocationMembership, PartnerOrganisation, SessionAttendance
from partners.services import accounts as account_service

from .base import PartnersTestCase
from .helpers import (
    add_member,
    enrol,
    make_cohort,
    make_location,
    make_online_product,
    make_organisation,
    make_participant,
    make_session,
    make_staff,
    make_user,
    seed_courses,
)


class PortalFixtureMixin:
    def build(self):
        self.staff = make_staff()
        self.organisation = make_organisation()
        self.location = make_location(self.organisation)
        self.coordinator = add_member(self.location)
        self.facilitator = add_member(self.location, LocationMembership.Role.FACILITATOR)
        self.unassigned = add_member(self.location, LocationMembership.Role.FACILITATOR)
        self.other_location = make_location(make_organisation())
        self.other_coordinator = add_member(self.other_location)
        self.student = make_user(User.STUDENT)
        self.courses = seed_courses(2)
        self.cohort = make_cohort(self.location, facilitators=[self.facilitator])
        self.membership = enrol(self.cohort)
        self.session = make_session(self.cohort, self.courses[0])

    def location_kwargs(self):
        return {'location_id': self.location.public_id}

    def cohort_kwargs(self):
        return {'location_id': self.location.public_id, 'cohort_id': self.cohort.public_id}

    def session_kwargs(self):
        return {**self.cohort_kwargs(), 'session_id': self.session.public_id}


class PermissionMatrixTests(PortalFixtureMixin, PartnersTestCase):
    """Every screen against every kind of user. 302 means sent to sign in."""

    def setUp(self):
        super().setUp()
        self.build()

    def users(self):
        return {
            'anonymous': None, 'staff': self.staff, 'coordinator': self.coordinator,
            'facilitator': self.facilitator, 'unassigned': self.unassigned,
            'other_coordinator': self.other_coordinator, 'student': self.student,
        }

    def check(self, url, expected):
        users = self.users()
        for name, status in expected.items():
            with self.subTest(url=url, user=name):
                self.client.logout()
                if users[name] is not None:
                    self.client.force_login(users[name])
                response = self.client.get(url)
                self.assertEqual(response.status_code, status, f'{name} on {url}')

    def test_staff_screens(self):
        expected = {'anonymous': 302, 'staff': 200, 'coordinator': 403, 'facilitator': 403, 'student': 403}
        for url in [
            reverse('partners:pipeline'),
            reverse('partners:network'),
            reverse('partners:organisation_list'),
            reverse('partners:organisation_create'),
            reverse('partners:organisation_detail', kwargs={'org_id': self.organisation.public_id}),
            reverse('partners:licence_list'),
            reverse('partners:licence_detail', kwargs={'licence_id': self.location.licence.public_id}),
            reverse('partners:location_create', kwargs={'org_id': self.organisation.public_id}),
            reverse('partners:location_edit', kwargs=self.location_kwargs()),
        ]:
            self.check(url, expected)

    def test_location_screens_everyone_at_the_location_can_open(self):
        expected = {'anonymous': 302, 'staff': 200, 'coordinator': 200, 'facilitator': 200,
                    'unassigned': 200, 'other_coordinator': 404, 'student': 404}
        for name in ['location_dashboard', 'participant_list', 'team']:
            self.check(reverse(f'partners:{name}', kwargs=self.location_kwargs()), expected)

    def test_location_screens_for_working_members(self):
        self.check(reverse('partners:participant_create', kwargs=self.location_kwargs()), {
            'staff': 403, 'coordinator': 200, 'facilitator': 200, 'other_coordinator': 404,
        })

    def test_coordinator_only_screens(self):
        expected = {'staff': 403, 'coordinator': 200, 'facilitator': 403, 'other_coordinator': 404}
        for name in ['participant_import', 'cohort_create', 'conflicts']:
            self.check(reverse(f'partners:{name}', kwargs=self.location_kwargs()), expected)
        self.check(reverse('partners:session_edit', kwargs=self.session_kwargs()), expected)
        self.check(reverse('partners:session_create', kwargs=self.cohort_kwargs()), expected)

    def test_cohort_screens_follow_assignment(self):
        expected = {'anonymous': 302, 'staff': 200, 'coordinator': 200, 'facilitator': 200,
                    'unassigned': 404, 'other_coordinator': 404, 'student': 404}
        self.check(reverse('partners:cohort_detail', kwargs=self.cohort_kwargs()), expected)
        self.check(reverse('partners:cohort_results', kwargs=self.cohort_kwargs()), expected)
        self.check(reverse('partners:session_register', kwargs=self.session_kwargs()), expected)

    def test_member_invite(self):
        self.check(reverse('partners:member_invite', kwargs=self.location_kwargs()), {
            'staff': 200, 'coordinator': 200, 'facilitator': 403, 'other_coordinator': 404,
        })

    def test_pack_api(self):
        url = reverse('partners:api_pack', kwargs={'cohort_id': self.cohort.public_id})
        self.check(url, {'anonymous': 401, 'facilitator': 200, 'unassigned': 404, 'other_coordinator': 404})


class StaffFlowTests(PortalFixtureMixin, PartnersTestCase):
    def setUp(self):
        super().setUp()
        self.build()
        self.client.force_login(self.staff)

    def test_create_organisation_starts_a_partnership(self):
        response = self.client.post(reverse('partners:organisation_create'), {
            'name': 'New Life Fellowship', 'kind': 'church', 'country': 'GH', 'region': '', 'city': 'Accra',
            'website': '', 'notes': '',
        })
        organisation = PartnerOrganisation.objects.get(name='New Life Fellowship')
        self.assertRedirects(
            response, reverse('partners:organisation_detail', kwargs={'org_id': organisation.public_id}),
        )
        self.assertEqual(organisation.partnership.stage, 'enquiry')

    def test_move_stage_from_the_detail_page(self):
        url = reverse('partners:partnership_move', kwargs={'org_id': self.organisation.public_id})
        self.client.post(url, {'to_stage': 'review', 'note': ''})
        self.organisation.partnership.refresh_from_db()
        self.assertEqual(self.organisation.partnership.stage, 'review')

    def test_network_export_withholds_sensitive_names(self):
        self.location.is_sensitive = True
        self.location.save()
        response = self.client.get(reverse('partners:network_export'))
        body = response.content.decode()
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('Withheld', body)
        self.assertNotIn(self.location.name, body)

    def test_agreement_download_is_staff_only(self):
        url = reverse('partners:licence_agreement', kwargs={'licence_id': self.location.licence.public_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.client.force_login(self.coordinator)
        self.assertEqual(self.client.get(url).status_code, 403)


class PortalFlowTests(PortalFixtureMixin, PartnersTestCase):
    def setUp(self):
        super().setUp()
        self.build()

    def test_register_post_saves_marks(self):
        self.client.force_login(self.facilitator)
        url = reverse('partners:session_register', kwargs=self.session_kwargs())
        response = self.client.post(url, {f'mark_{self.membership.public_id}': 'present'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SessionAttendance.objects.get().status, 'present')

    def test_staff_viewer_cannot_post_a_register(self):
        self.client.force_login(self.staff)
        url = reverse('partners:session_register', kwargs=self.session_kwargs())
        self.assertEqual(self.client.post(url, {f'mark_{self.membership.public_id}': 'present'}).status_code, 403)

    def test_results_post_and_stale_version(self):
        self.client.force_login(self.facilitator)
        url = reverse('partners:cohort_results', kwargs=self.cohort_kwargs())
        field = f'score_{self.membership.public_id}'
        self.client.post(url, {'course': self.courses[0].code, field: '70'})
        result = CourseResult.objects.get()
        self.assertEqual(result.exam_score, 70)

        self.client.post(url, {'course': self.courses[0].code, field: '80',
                               f'version_{self.membership.public_id}': str(result.version - 1 or 99)})
        result.refresh_from_db()
        self.assertEqual(result.exam_score, 70)

    def test_create_participant_warns_about_duplicates(self):
        existing = make_participant(self.location, first_name='Grace', last_name='Banda')
        self.client.force_login(self.coordinator)
        url = reverse('partners:participant_create', kwargs=self.location_kwargs())
        data = {'first_name': 'Grace', 'last_name': 'Banda', 'phone': '', 'email': '', 'church_name': '',
                'position_title': '', 'country': 'ZA'}
        response = self.client.post(url, data)
        self.assertContains(response, existing.full_name)
        response = self.client.post(url, {**data, 'confirm_duplicate': '1'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.location.participants.filter(last_name='Banda').count(), 2)

    @override_settings(PARTNERS_ONLINE_PRODUCT_SLUG='metp-online-programme')
    def test_grant_access_view_shows_the_link_once(self):
        make_online_product()
        self.cohort.delivery = Cohort.Delivery.BLENDED
        self.cohort.save()
        self.client.force_login(self.coordinator)
        url = reverse('partners:membership_action', kwargs={
            **self.cohort_kwargs(), 'membership_id': self.membership.public_id, 'action': 'grant_access',
        })
        with mock.patch('partners.services.access._queue_enrolment'):
            response = self.client.post(url, follow=True)
        self.assertContains(response, '/partners/activate/')
        again = self.client.get(reverse('partners:cohort_detail', kwargs=self.cohort_kwargs()))
        self.assertNotContains(again, '/partners/activate/')

    def test_pdf_register(self):
        self.client.force_login(self.coordinator)
        response = self.client.get(reverse('partners:session_register_pdf', kwargs=self.session_kwargs()))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_import_preview_then_confirm(self):
        self.client.force_login(self.coordinator)
        url = reverse('partners:participant_import', kwargs=self.location_kwargs())
        from django.core.files.uploadedfile import SimpleUploadedFile
        csv_file = SimpleUploadedFile('people.csv', (
            'first_name,last_name,phone\nAmos,Phiri,+260970000001\n,Missing,\nRuth,Zulu,not-a-phone\n'
        ).encode(), content_type='text/csv')
        response = self.client.post(url, {'file': csv_file})
        self.assertContains(response, 'can be imported')
        response = self.client.post(url, {'confirm': '1', 'cohort': str(self.cohort.public_id)})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.location.participants.filter(last_name='Phiri').exists())
        self.assertFalse(self.location.participants.filter(last_name='Zulu').exists())
        self.assertTrue(self.cohort.memberships.filter(participant__last_name='Phiri').exists())


class PublicAndOfflineTests(PortalFixtureMixin, PartnersTestCase):
    def setUp(self):
        super().setUp()
        self.build()

    ENQUIRY = {
        'organisation_name': 'River of Life', 'kind': 'church', 'country': 'MW', 'city': 'Lilongwe',
        'contact_name': 'Peter Banda', 'role_title': '', 'email': 'peter@example.org', 'phone': '',
        'expected_participants': '', 'message': 'Hello', 'website': '',
    }

    def test_enquiry_creates_partner_and_notifies(self):
        with mock.patch('partners.views.public.enqueue') as notify:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse('partners:enquire'), self.ENQUIRY)
        self.assertRedirects(response, reverse('partners:enquiry_thanks'))
        self.assertTrue(PartnerOrganisation.objects.filter(name='River of Life').exists())
        notify.assert_called_once()

    def test_honeypot_blocks_bots(self):
        response = self.client.post(reverse('partners:enquire'), {**self.ENQUIRY, 'website': 'http://spam'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PartnerOrganisation.objects.filter(name='River of Life').exists())

    def test_enquiries_are_rate_limited(self):
        with mock.patch('partners.views.public.enqueue'):
            for _ in range(5):
                self.client.post(reverse('partners:enquire'), self.ENQUIRY)
            response = self.client.post(reverse('partners:enquire'), {**self.ENQUIRY, 'organisation_name': 'Sixth'})
        self.assertRedirects(response, reverse('partners:enquire'))
        self.assertFalse(PartnerOrganisation.objects.filter(name='Sixth').exists())

    @override_settings(SCHOLR_PARTNERS_ENABLED=False)
    def test_feature_flag_hides_everything(self):
        self.assertEqual(self.client.get('/partners/enquire/').status_code, 404)

    def test_activation_link_sets_a_password_once(self):
        membership, _created = account_service.invite_member(
            self.location, self.coordinator, 'Joy', 'Mwale', LocationMembership.Role.FACILITATOR,
            email='joy@example.org',
        )
        path = account_service.activation_path(membership.user)
        response = self.client.get(path, follow=True)
        self.assertContains(response, 'Set your password')
        form_url = response.redirect_chain[-1][0]
        password = 'Shepherd-Field-2026'
        response = self.client.post(form_url, {'new_password1': password, 'new_password2': password})
        self.assertRedirects(response, reverse('partners:activate_done'))
        membership.user.refresh_from_db()
        self.assertTrue(membership.user.check_password('Shepherd-Field-2026'))
        self.assertContains(self.client.get(path, follow=True), 'This link has expired')

    def test_dashboard_routing_for_new_roles(self):
        self.client.force_login(self.staff)
        self.assertRedirects(self.client.get(reverse('accounts:dashboard')), reverse('partners:pipeline'),
                             fetch_redirect_response=False)
        self.client.force_login(self.facilitator)
        self.assertRedirects(self.client.get(reverse('accounts:dashboard')), reverse('partners:portal_home'),
                             fetch_redirect_response=False)
        response = self.client.get(reverse('partners:portal_home'))
        self.assertRedirects(response, reverse('partners:location_dashboard', kwargs=self.location_kwargs()))

    def test_offline_shell_worker_and_manifest(self):
        self.client.force_login(self.facilitator)
        response = self.client.get(reverse('partners:offline_app'))
        self.assertContains(response, 'offline-config')
        worker = self.client.get(reverse('partners:service_worker'))
        self.assertEqual(worker['Content-Type'], 'application/javascript')
        self.assertIn(b'importScripts("/static/partners/offline-core.js")', worker.content)
        manifest = json.loads(self.client.get(reverse('partners:manifest')).content)
        self.assertEqual(manifest['start_url'], reverse('partners:offline_app'))

    def test_pack_requires_remember_me(self):
        self.client.force_login(self.facilitator)
        session = self.client.session
        session.set_expiry(0)
        session.save()
        response = self.client.get(reverse('partners:api_pack', kwargs={'cohort_id': self.cohort.public_id}))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'remember_me_required')

    def test_sync_endpoint(self):
        self.client.force_login(self.facilitator)
        url = reverse('partners:api_sync')
        payload = {'device_id': 'phone', 'operations': [{
            'op_id': str(uuid.uuid4()), 'kind': 'attendance.set',
            'payload': {'session': str(self.session.public_id), 'membership': str(self.membership.public_id),
                        'status': 'present'},
        }]}
        response = self.client.post(url, json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'][0]['result'], 'applied')
        self.assertEqual(response['Cache-Control'].split(',')[0].strip() in ('max-age=0', 'no-cache', 'no-store'), True)
        self.assertEqual(self.client.post(url, 'nope', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post(url, 'a=b', content_type='text/plain').status_code, 415)

    def test_sync_endpoint_enforces_csrf(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.facilitator)
        response = client.post(reverse('partners:api_sync'), '{"operations": []}', content_type='application/json')
        self.assertEqual(response.status_code, 403)


class EveryPageRendersTests(PortalFixtureMixin, PartnersTestCase):
    """Each screen renders with real data for the people who use it, with bounded queries."""

    def setUp(self):
        super().setUp()
        self.build()
        from partners.services import roster
        contact = self.organisation.contacts.first()
        self.contact_kwargs = {'org_id': self.organisation.public_id, 'pk': contact.pk}
        roster.set_attendance(self.session, self.membership, 'present', self.facilitator)
        roster.record_result(self.membership, self.courses[0], 72, self.facilitator)
        self.cohort.delivery = Cohort.Delivery.BLENDED
        self.cohort.save()

    def test_staff_pages(self):
        self.client.force_login(self.staff)
        org = {'org_id': self.organisation.public_id}
        licence = {'licence_id': self.location.licence.public_id}
        for url in [
            reverse('partners:pipeline') + '?owner=me&country=ZA',
            reverse('partners:organisation_list') + '?q=Grace&archived=1',
            reverse('partners:organisation_edit', kwargs=org),
            reverse('partners:contact_create', kwargs=org),
            reverse('partners:contact_edit', kwargs=self.contact_kwargs),
            reverse('partners:licence_create', kwargs=org),
            reverse('partners:licence_edit', kwargs=licence),
            reverse('partners:licence_list') + '?status=active&expiring=1',
            reverse('partners:location_edit', kwargs=self.location_kwargs()),
            reverse('partners:network'),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_coordinator_pages(self):
        self.client.force_login(self.coordinator)
        participant = {**self.location_kwargs(), 'participant_id': self.membership.participant.public_id}
        for url in [
            reverse('partners:location_dashboard', kwargs=self.location_kwargs()),
            reverse('partners:participant_list', kwargs=self.location_kwargs()) + '?q=Thabo',
            reverse('partners:participant_detail', kwargs=participant),
            reverse('partners:participant_edit', kwargs=participant),
            reverse('partners:participant_create', kwargs=self.location_kwargs())
            + f'?cohort={self.cohort.public_id}',
            reverse('partners:cohort_edit', kwargs=self.cohort_kwargs()),
            reverse('partners:cohort_results', kwargs=self.cohort_kwargs()) + f'?course={self.courses[1].code}',
            reverse('partners:conflicts', kwargs=self.location_kwargs()),
            reverse('partners:team', kwargs=self.location_kwargs()),
            reverse('partners:enquire'),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_cohort_and_results_queries_do_not_grow_with_participants(self):
        self.client.force_login(self.coordinator)
        detail = reverse('partners:cohort_detail', kwargs=self.cohort_kwargs())
        results = reverse('partners:cohort_results', kwargs=self.cohort_kwargs())
        baseline_detail = self._count(detail)
        baseline_results = self._count(results)
        for _ in range(10):
            enrol(self.cohort)
        self.assertEqual(self._count(detail), baseline_detail)
        self.assertEqual(self._count(results), baseline_results)

    def _count(self, url):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(captured)
