from datetime import timedelta
from io import BytesIO

from django.conf import settings
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from accounts.models import User
from partners.models import Cohort, CourseResult, Licence, LocationEvent, Partnership, StaffAssignment
from partners.services import exports, places
from partners.tasks import (
    notify_staff_of_enquiry, stalled_partnerships_digest, staff_recipients, update_licence_statuses,
)

from .base import PartnersTestCase
from .helpers import (
    add_member, enrol, make_cohort, make_location, make_organisation, make_staff, make_user, seed_courses,
)


class ScopedTestCase(PartnersTestCase):
    def setUp(self):
        super().setUp()
        self.courses = seed_courses(2)
        self.kenya = make_organisation(name='Nairobi Fellowship', country='KE')
        self.nigeria = make_organisation(name='Lagos/Grace Assembly of the Living God Ministries', country='NG')
        self.kenya_location = make_location(organisation=self.kenya, name='Kibera Centre',
                                            latitude=-1.31, longitude=36.78)
        self.kenya_near = make_location(organisation=self.kenya, licence=self.kenya_location.licence,
                                        name='Kawangware Hall', latitude=-1.28, longitude=36.75)
        self.nigeria_location = make_location(organisation=self.nigeria, name='Ikeja Centre',
                                              latitude=6.60, longitude=3.35)
        self.kenya_cohort = make_cohort(self.kenya_location, delivery=Cohort.Delivery.BLENDED)
        self.nigeria_cohort = make_cohort(self.nigeria_location, delivery=Cohort.Delivery.BLENDED)
        self.kenya_member = enrol(self.kenya_cohort)
        self.nigeria_member = enrol(self.nigeria_cohort)
        CourseResult.objects.create(membership=self.kenya_member, course=self.courses[0], exam_score=82,
                                    status=CourseResult.Status.PASSED)

        self.hub = make_user(User.HUB_ADMIN, email='hub.kenya@example.org')
        StaffAssignment.objects.create(user=self.hub, country='KE')
        self.ambassador = make_user(User.AMBASSADOR)
        StaffAssignment.objects.create(user=self.ambassador, organisation=self.nigeria)

    def workbook(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertIn('spreadsheetml', response['Content-Type'])
        self.assertIn('attachment', response['Content-Disposition'])
        return load_workbook(BytesIO(response.content))

    def column(self, sheet, heading):
        headings = [cell.value for cell in sheet[1]]
        index = headings.index(heading)
        return [row[index] for row in sheet.iter_rows(min_row=2, values_only=True)]


@override_settings(SCHOLR_ONLINE_RESULTS_ENABLED=True)
class ExportTests(ScopedTestCase):
    def export(self, **params):
        return self.client.get(reverse('partners:results_export'), params)

    def test_one_sheet_holds_what_the_screen_shows(self):
        self.client.force_login(make_staff())
        book = self.workbook(self.export(view='programme'))
        self.assertEqual(book.sheetnames, ['Summary', 'Results', 'About'])
        results = book['Results']
        self.assertEqual(sorted(self.column(results, 'Partner')), sorted([self.kenya.name, self.nigeria.name]))
        kenyan = [row for row in results.iter_rows(min_row=2, values_only=True) if row[1] == self.kenya.name][0]
        self.assertIn(82.0, kenyan)
        self.assertEqual(kenyan[-1], 1)

    def test_hub_admins_export_only_their_reach(self):
        self.client.force_login(self.hub)
        book = self.workbook(self.export(view='programme'))
        self.assertEqual(self.column(book['Results'], 'Partner'), [self.kenya.name])
        self.assertEqual(self.column(book['Summary'], 'Partner'), [self.kenya.name])

    def test_a_sheet_per_partner_or_per_cohort(self):
        self.client.force_login(make_staff())
        by_partner = self.workbook(self.export(view='programme', split='partner'))
        self.assertEqual(len(by_partner.sheetnames), 4)
        for title in by_partner.sheetnames:
            self.assertLessEqual(len(title), 31)
            self.assertNotIn('/', title)
        by_cohort = self.workbook(self.export(view='programme', split='cohort'))
        self.assertEqual(len(by_cohort.sheetnames), 4)

    def test_about_sheet_records_what_was_exported(self):
        staff = make_staff(first_name='Ada', last_name='Mensah')
        self.client.force_login(staff)
        book = self.workbook(self.export(view='online', country='KE'))
        about = dict(book['About'].iter_rows(values_only=True))
        self.assertEqual(about['Results'], 'Online quiz results')
        self.assertEqual(about['Country'], 'Kenya')
        self.assertEqual(about['Exported by'], 'Ada Mensah')
        self.assertEqual(about['Participants'], 1)

    def test_who_may_export(self):
        self.client.force_login(make_user(User.LEADER))
        self.workbook(self.export())
        self.client.force_login(self.ambassador)
        self.assertEqual(self.export().status_code, 403)

    @override_settings(SCHOLR_ONLINE_RESULTS_ENABLED=False)
    def test_off_without_the_results_switch(self):
        self.client.force_login(make_staff())
        self.assertEqual(self.export().status_code, 404)

    def test_coordinators_export_their_own_cohort_only(self):
        coordinator = add_member(self.kenya_location)
        self.client.force_login(coordinator)
        url = reverse('partners:cohort_results_export', kwargs={
            'location_id': self.kenya_location.public_id, 'cohort_id': self.kenya_cohort.public_id})
        book = self.workbook(self.client.get(url, {'view': 'programme'}))
        self.assertEqual(self.column(book['Results'], 'Last name'), [self.kenya_member.participant.last_name])
        other = reverse('partners:cohort_results_export', kwargs={
            'location_id': self.nigeria_location.public_id, 'cohort_id': self.nigeria_cohort.public_id})
        self.assertEqual(self.client.get(other).status_code, 404)

    def test_export_menu_on_the_results_pages(self):
        self.client.force_login(make_staff())
        page = self.client.get(reverse('partners:results_overview'))
        self.assertContains(page, 'A sheet per partner')
        self.assertContains(page, reverse('partners:results_export'))

    def test_sheet_names_stay_unique(self):
        taken = set()
        names = [exports._sheet_title('Same name', taken) for _ in range(3)]
        self.assertEqual(len(set(names)), 3)
        self.assertEqual(exports._sheet_title('Summary', taken), 'Summary (2)')


class MapTests(ScopedTestCase):
    def map_names(self):
        data = self.client.get(reverse('partners:map_data')).json()
        return {feature['name'] for feature in data['features']} | {row['name'] for row in data['missing']}

    def test_hub_admins_see_only_their_locations(self):
        self.client.force_login(self.hub)
        self.assertEqual(self.map_names(), {'Kibera Centre', 'Kawangware Hall'})
        panel = reverse('partners:map_panel', kwargs={'location_id': self.nigeria_location.public_id})
        self.assertEqual(self.client.get(panel).status_code, 404)

    def test_ambassadors_see_only_their_partners_locations(self):
        self.client.force_login(self.ambassador)
        self.assertEqual(self.map_names(), {'Ikeja Centre'})
        self.assertNotContains(self.client.get(reverse('partners:map')), 'Kibera Centre')

    def test_leaders_and_staff_see_everything(self):
        for user in (make_user(User.LEADER), make_staff()):
            self.client.force_login(user)
            self.assertEqual(self.map_names(), {'Kibera Centre', 'Kawangware Hall', 'Ikeja Centre'})

    def test_nearby_and_events_stay_inside_reach(self):
        from partners.services import scope
        within = scope.locations_in_reach(self.ambassador)
        self.assertEqual(places.nearby(self.nigeria_location, within=within), [])
        LocationEvent.objects.create(location=self.kenya_location, title='Graduation', kind=LocationEvent.Kind.OTHER,
                                     starts_on=timezone.localdate() + timedelta(days=3))
        self.assertFalse(places.upcoming_events(locations=within).exists())
        self.assertTrue(places.upcoming_events(locations=scope.locations_in_reach(self.hub)).exists())

    def test_trips_drop_stops_outside_reach(self):
        trip = places.plan_trip(
            [str(self.kenya_location.public_id), str(self.nigeria_location.public_id)], self.ambassador,
        )
        self.assertEqual([stop['location'].name for stop in trip['stops']], ['Ikeja Centre'])


class NotificationTests(ScopedTestCase):
    def setUp(self):
        super().setUp()
        self.staff = make_staff(email='programme@example.org')
        self.other_hub = make_user(User.HUB_ADMIN, email='hub.nigeria@example.org')
        StaffAssignment.objects.create(user=self.other_hub, country='NG')

    def test_recipients_add_the_covering_hub_admins(self):
        self.assertIn('hub.kenya@example.org', staff_recipients(self.kenya))
        self.assertNotIn('hub.nigeria@example.org', staff_recipients(self.kenya))
        self.assertNotIn('hub.kenya@example.org', staff_recipients())
        StaffAssignment.objects.create(user=self.other_hub, organisation=self.kenya)
        self.assertIn('hub.nigeria@example.org', staff_recipients(self.kenya))

    def test_enquiries_reach_the_hub_admin(self):
        partnership = Partnership.objects.get(organisation=self.kenya)
        notify_staff_of_enquiry.apply(args=[partnership.pk, True])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('hub.kenya@example.org', mail.outbox[0].to)
        self.assertNotIn('hub.nigeria@example.org', mail.outbox[0].to)

    def test_licence_reminders_copy_the_hub_admin(self):
        days = settings.PARTNERS_LICENCE_REMINDER_DAYS[0]
        Licence.objects.filter(pk=self.kenya_location.licence.pk).update(
            ends_on=timezone.localdate() + timedelta(days=days))
        update_licence_statuses()
        sent_to = [address for message in mail.outbox for address in message.to]
        self.assertIn('hub.kenya@example.org', sent_to)
        self.assertNotIn('hub.nigeria@example.org', sent_to)

    def test_digest_is_cut_down_for_each_hub_admin(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        Partnership.objects.filter(organisation__in=[self.kenya, self.nigeria]).update(next_action_due=yesterday)
        stalled_partnerships_digest()
        by_recipient = {tuple(message.to): message for message in mail.outbox}
        kenya_digest = by_recipient[('hub.kenya@example.org',)]
        self.assertIn('Nairobi Fellowship', kenya_digest.body)
        self.assertNotIn('Lagos', kenya_digest.body)
        self.assertIn('Lagos', by_recipient[('hub.nigeria@example.org',)].body)
        staff_digest = [message for message in mail.outbox if 'programme@example.org' in message.to][0]
        self.assertIn('Nairobi Fellowship', staff_digest.body)
        self.assertIn('Lagos', staff_digest.body)
