import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from partners.exceptions import PlaceError
from partners.models import LocationAccessLog, LocationEvent, LocationMembership, TrainingLocation
from partners.services import places

from .base import PartnersTestCase
from .helpers import add_member, make_cohort, make_location, make_organisation, make_staff, make_user

POLOKWANE = (Decimal('-23.904512'), Decimal('29.468901'))
MOKOPANE = (Decimal('-24.194470'), Decimal('29.009850'))
NAIROBI = (Decimal('-1.292066'), Decimal('36.821946'))


def place(location, position, **extra):
    location.latitude, location.longitude = position
    location.position_source = TrainingLocation.PositionSource.PIN
    location.position_confirmed_at = timezone.now()
    for key, value in extra.items():
        setattr(location, key, value)
    location.save()
    return location


class ParsePositionTests(TestCase):
    def assertParses(self, text, lat, lng):
        self.assertEqual(places.parse_position(text), (Decimal(lat), Decimal(lng)))

    def test_plain_coordinates(self):
        self.assertParses('-23.904512, 29.468901', '-23.904512', '29.468901')
        self.assertParses('-23.904512,29.468901', '-23.904512', '29.468901')
        self.assertParses('  -23.9045   29.4689 ', '-23.904500', '29.468900')

    def test_map_links(self):
        self.assertParses(
            'https://www.google.com/maps/place/Church/@-23.9001,29.4502,17z/data=!3m1!4b1!4m6!3m5!1s0x0:0x0'
            '!8m2!3d-23.904512!4d29.468901', '-23.904512', '29.468901',
        )
        self.assertParses('https://www.google.com/maps/@-1.292066,36.821946,15z', '-1.292066', '36.821946')
        self.assertParses(
            'https://www.google.com/maps/dir/?api=1&destination=-24.19447%2C29.00985', '-24.194470', '29.009850',
        )
        self.assertParses('https://maps.apple.com/?ll=-1.292066,36.821946&q=Church', '-1.292066', '36.821946')
        self.assertParses(
            'https://www.openstreetmap.org/?mlat=-23.9045&mlon=29.4689#map=17/-23.9/29.4', '-23.904500', '29.468900',
        )
        self.assertParses('https://www.openstreetmap.org/#map=16/-24.19447/29.00985', '-24.194470', '29.009850')
        self.assertParses('geo:-1.292066,36.821946?q=church', '-1.292066', '36.821946')

    def test_refuses_what_it_cannot_read(self):
        for text in ('', 'Polokwane', '91, 29', '-23.9, 181', '0, 0', 'https://example.org/page'):
            with self.subTest(text=text), self.assertRaises(PlaceError):
                places.parse_position(text)

    def test_short_links_are_not_followed(self):
        with self.assertRaisesMessage(PlaceError, 'Short links'):
            places.parse_position('https://maps.app.goo.gl/AbCdEf123')


class GeometryTests(TestCase):
    def test_approximate_is_stable_and_near(self):
        first = places.approximate(*POLOKWANE)
        self.assertEqual(first, places.approximate(Decimal('-23.95'), Decimal('29.41')))
        self.assertEqual(first, (Decimal('-23.950000'), Decimal('29.450000')))
        self.assertLess(places.distance_km(*POLOKWANE, *first), 8)
        self.assertNotEqual(first, POLOKWANE)

    def test_distance(self):
        self.assertAlmostEqual(places.distance_km(*POLOKWANE, *MOKOPANE), 58, delta=3)

    def test_route_url_uses_waypoints(self):
        url = places.route_url([{'lat': 1, 'lng': 2}, {'lat': 3, 'lng': 4}, {'lat': 5, 'lng': 6}])
        self.assertIn('destination=5%2C6', url)
        self.assertIn('waypoints=1%2C2%7C3%2C4', url)


class MapDataTests(PartnersTestCase):
    def setUp(self):
        super().setUp()
        self.staff = make_staff()
        self.open = place(make_location(name='Polokwane Centre', city='Polokwane'), POLOKWANE)
        self.secret = place(make_location(name='Hidden House', city='Secret Town', is_sensitive=True), MOKOPANE)
        self.secret_partner = make_organisation(name='Quiet Fellowship', country='KE', is_sensitive=True)
        self.under_secret_partner = place(make_location(self.secret_partner, name='Back Room'), NAIROBI)
        self.unplaced = make_location(name='Not Placed')
        LocationEvent.objects.create(
            location=self.secret, title='Graduation', starts_on=timezone.localdate() + timedelta(days=10),
        )
        LocationEvent.objects.create(
            location=self.open, title='Polokwane graduation', starts_on=timezone.localdate() + timedelta(days=20),
        )

    def payload(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('partners:map_data'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        return response

    def test_sensitive_locations_are_approximate_and_anonymous(self):
        response = self.payload()
        raw = response.content.decode()
        for secret in ('Hidden House', 'Secret Town', 'Back Room', 'Quiet Fellowship', '29.00985', '36.821946'):
            self.assertNotIn(secret, raw)
        features = {feature['id']: feature for feature in response.json()['features']}
        hidden = features[str(self.secret.public_id)]
        self.assertTrue(hidden['sensitive'])
        self.assertTrue(hidden['approximate'])
        self.assertEqual(hidden['name'], 'Sensitive location')
        self.assertEqual((hidden['partner'], hidden['partner_id'], hidden['city'], hidden['region']), ('', '', '', ''))
        self.assertIsNone(hidden['next_event'])
        self.assertTrue(features[str(self.under_secret_partner.public_id)]['approximate'])

    def test_ordinary_locations_are_exact_with_their_next_event(self):
        features = {feature['id']: feature for feature in self.payload().json()['features']}
        shown = features[str(self.open.public_id)]
        self.assertEqual((shown['lat'], shown['lng']), (float(POLOKWANE[0]), float(POLOKWANE[1])))
        self.assertFalse(shown['approximate'])
        self.assertEqual(shown['next_event']['title'], 'Polokwane graduation')

    def test_locations_without_a_position_are_listed_as_missing(self):
        missing = self.payload().json()['missing']
        self.assertEqual([item['name'] for item in missing], ['Not Placed'])

    def test_query_count_does_not_grow_with_locations(self):
        self.client.force_login(self.staff)
        with self.assertNumQueries(3):
            self.client.get(reverse('partners:map_data'))
        for number in range(5):
            place(make_location(name=f'Extra {number}'), NAIROBI)
        with self.assertNumQueries(3):
            self.client.get(reverse('partners:map_data'))

    def test_map_page_is_for_programme_staff_only(self):
        coordinator = add_member(self.open)
        self.client.force_login(coordinator)
        for name, kwargs in (
            ('partners:map', {}), ('partners:map_data', {}), ('partners:map_trip', {}),
            ('partners:map_panel', {'location_id': self.open.public_id}),
        ):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, kwargs=kwargs)).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('partners:map')).status_code, 302)
        self.client.force_login(self.staff)
        page = self.client.get(reverse('partners:map'))
        self.assertContains(page, 'data-map-app')
        self.assertContains(page, 'partners/vendor/leaflet/leaflet.js')

    def test_panel_for_a_sensitive_location_gives_nothing_away(self):
        self.secret.directions = 'Behind the blue gate'
        self.secret.arrival_contact_phone = '+27820001111'
        self.secret.save()
        self.client.force_login(self.staff)
        response = self.client.get(reverse('partners:map_panel', kwargs={'location_id': self.secret.public_id}))
        for secret in ('Hidden House', 'Behind the blue gate', '+27820001111', 'Graduation'):
            self.assertNotContains(response, secret)
        self.assertContains(response, reverse('partners:location_place', kwargs={'location_id': self.secret.public_id}))

    def test_panel_for_an_ordinary_location_has_directions_and_contacts(self):
        coordinator = add_member(self.open)
        self.open.directions = 'Turn left at the market'
        self.open.save()
        self.client.force_login(self.staff)
        response = self.client.get(reverse('partners:map_panel', kwargs={'location_id': self.open.public_id}))
        self.assertContains(response, 'Turn left at the market')
        self.assertContains(response, 'https://www.google.com/maps/dir/?api=1&amp;destination=-23.904512%2C29.468901')
        self.assertContains(response, f'tel:{coordinator.phone_number}')
        self.assertContains(response, 'data-trip-add')


class NearbyTests(PartnersTestCase):
    def test_nearest_first_without_sensitive_or_self(self):
        here = place(make_location(name='Here'), POLOKWANE)
        near = place(make_location(name='Near'), MOKOPANE)
        place(make_location(name='Secret', is_sensitive=True), (Decimal('-23.91'), Decimal('29.47')))
        place(make_location(name='Far'), NAIROBI)
        self.assertEqual([item['location'] for item in places.nearby(here)], [near])


class PlacePageTests(PartnersTestCase):
    def setUp(self):
        super().setUp()
        self.location = make_location(name='Mzuzu Centre', is_sensitive=True)
        self.coordinator = add_member(self.location)
        self.facilitator = add_member(self.location, role=LocationMembership.Role.FACILITATOR)
        self.staff = make_staff()
        self.view_url = reverse('partners:location_place', kwargs={'location_id': self.location.public_id})
        self.edit_url = reverse('partners:location_place_edit', kwargs={'location_id': self.location.public_id})

    def post_place(self, user, **data):
        self.client.force_login(user)
        payload = {'latitude': '', 'longitude': '', 'pasted': '', 'position_source': '', 'directions': ''}
        payload.update(data)
        return self.client.post(self.edit_url, payload)

    def test_coordinator_pastes_a_link_and_the_position_is_confirmed(self):
        response = self.post_place(
            self.coordinator, pasted='https://www.google.com/maps/@-11.456,34.021,15z', directions='Past the clinic',
        )
        self.assertRedirects(response, self.view_url)
        self.location.refresh_from_db()
        self.assertEqual(
            (self.location.latitude, self.location.longitude), (Decimal('-11.456000'), Decimal('34.021000')),
        )
        self.assertEqual(self.location.position_source, TrainingLocation.PositionSource.PASTED)
        self.assertEqual(self.location.position_confirmed_by, self.coordinator)
        self.assertEqual(self.location.directions, 'Past the clinic')

    def test_editing_directions_keeps_who_confirmed_the_pin(self):
        self.post_place(self.coordinator, latitude='-11.456', longitude='34.021', position_source='device')
        self.post_place(self.staff, latitude='-11.456000', longitude='34.021000', directions='New directions')
        self.location.refresh_from_db()
        self.assertEqual(self.location.position_confirmed_by, self.coordinator)
        self.assertEqual(self.location.position_source, TrainingLocation.PositionSource.DEVICE)
        self.assertEqual(self.location.directions, 'New directions')

    def test_clearing_and_half_positions(self):
        self.post_place(self.coordinator, latitude='-11.456', longitude='34.021')
        response = self.post_place(self.coordinator, latitude='-11.456', longitude='')
        self.assertEqual(response.status_code, 200)
        self.assertIn('latitude', response.context['form'].errors)
        self.post_place(self.coordinator, latitude='-11.456', longitude='34.021', clear_position='on')
        self.location.refresh_from_db()
        self.assertFalse(self.location.has_position)
        self.assertIsNone(self.location.position_confirmed_at)

    def test_facilitators_can_read_but_not_edit_or_see_phone_numbers(self):
        self.client.force_login(self.facilitator)
        page = self.client.get(self.view_url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, f'tel:{self.coordinator.phone_number}')
        self.assertNotContains(page, self.edit_url)
        self.assertEqual(self.client.get(self.edit_url).status_code, 403)

    def test_people_outside_the_location_get_404(self):
        outsider = add_member(make_location())
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(self.view_url).status_code, 404)
        self.assertEqual(self.client.get(self.edit_url).status_code, 404)

    def test_staff_views_of_a_sensitive_location_are_recorded(self):
        self.client.force_login(self.coordinator)
        self.client.get(self.view_url)
        self.assertFalse(LocationAccessLog.objects.exists())
        self.client.force_login(self.staff)
        page = self.client.get(self.view_url)
        self.assertContains(page, 'Your visit to this page has been recorded')
        log = LocationAccessLog.objects.get()
        self.assertEqual((log.user, log.location), (self.staff, self.location))
        self.assertContains(self.client.get(self.view_url), 'Recently viewed by')


class EventTests(PartnersTestCase):
    def setUp(self):
        super().setUp()
        self.location = place(make_location(name='Lilongwe Centre'), NAIROBI)
        self.coordinator = add_member(self.location)
        self.staff = make_staff()
        self.cohort = make_cohort(self.location)
        self.create_url = reverse('partners:event_create', kwargs={'location_id': self.location.public_id})

    def event_data(self, **extra):
        data = {
            'kind': 'graduation', 'title': 'Graduation 2026', 'cohort': self.cohort.pk,
            'starts_on': (timezone.localdate() + timedelta(days=30)).isoformat(), 'ends_on': '', 'starts_at': '10:00',
            'venue': '', 'notes': '',
        }
        data.update(extra)
        return data

    def test_coordinator_adds_a_graduation_without_choosing_staff(self):
        self.client.force_login(self.coordinator)
        page = self.client.get(self.create_url)
        self.assertNotIn('attendees', page.context['form'].fields)
        response = self.client.post(self.create_url, self.event_data())
        self.assertEqual(response.status_code, 302)
        event = LocationEvent.objects.get()
        self.assertEqual((event.created_by, event.cohort, event.status), (self.coordinator, self.cohort, 'planned'))
        self.assertEqual(places.map_data()['features'][0]['next_event']['title'], 'Graduation 2026')

    def test_staff_choose_who_travels_and_only_staff_can_be_chosen(self):
        traveller = make_staff()
        partner_user = make_user(User.PARTNER)
        self.client.force_login(self.staff)
        self.client.post(self.create_url, self.event_data(attendees=[traveller.pk]))
        event = LocationEvent.objects.get()
        self.assertEqual(list(event.attendees.all()), [traveller])
        places.save_event(event, self.staff, attendees=[traveller, partner_user])
        self.assertEqual(list(event.attendees.all()), [traveller])

    def test_a_cohort_from_another_location_is_refused(self):
        other_cohort = make_cohort(make_location())
        event = LocationEvent(
            location=self.location, title='Wrong', starts_on=timezone.localdate(), cohort=other_cohort,
        )
        with self.assertRaises(PlaceError):
            places.save_event(event, self.staff)

    def test_cancelled_events_leave_the_map(self):
        self.client.force_login(self.coordinator)
        self.client.post(self.create_url, self.event_data())
        event = LocationEvent.objects.get()
        cancel = reverse('partners:event_cancel', kwargs={
            'location_id': self.location.public_id, 'event_id': event.public_id,
        })
        self.assertEqual(self.client.get(cancel).status_code, 405)
        self.client.post(cancel)
        event.refresh_from_db()
        self.assertEqual(event.status, LocationEvent.Status.CANCELLED)
        self.assertIsNone(places.map_data()['features'][0]['next_event'])

    def test_facilitators_cannot_add_events(self):
        facilitator = add_member(self.location, role=LocationMembership.Role.FACILITATOR)
        self.client.force_login(facilitator)
        self.assertEqual(self.client.get(self.create_url).status_code, 403)
        self.assertEqual(self.client.post(self.create_url, self.event_data()).status_code, 403)
        self.assertFalse(LocationEvent.objects.exists())


class TripSheetTests(PartnersTestCase):
    def setUp(self):
        super().setUp()
        self.staff = make_staff()
        self.first = place(make_location(name='First Stop', directions='Gate 3'), POLOKWANE)
        self.second = place(make_location(name='Second Stop'), MOKOPANE)
        self.secret = place(make_location(name='Hidden House', is_sensitive=True), NAIROBI)

    def test_plan_leaves_out_sensitive_locations(self):
        trip = places.plan_trip(
            [str(self.first.public_id), str(self.secret.public_id), str(self.second.public_id)], self.staff,
        )
        self.assertEqual([stop['location'] for stop in trip['stops']], [self.first, self.second])
        self.assertEqual(trip['skipped'][0]['reason'], 'sensitive')
        self.assertNotIn('Hidden House', json.dumps(trip['skipped']))
        self.assertGreater(trip['total_km'], 40)
        self.assertIn('waypoints=', trip['route_url'])

    def test_limits(self):
        with self.assertRaises(PlaceError):
            places.plan_trip([], self.staff)
        with self.assertRaises(PlaceError):
            places.plan_trip([str(self.secret.public_id)], self.staff)
        many = [str(place(make_location(), NAIROBI).public_id) for _ in range(places.MAX_TRIP_STOPS + 1)]
        with self.assertRaises(PlaceError):
            places.plan_trip(many, self.staff)

    def test_pdf(self):
        self.client.force_login(self.staff)
        url = reverse('partners:map_trip')
        stops = f'location={self.first.public_id}&location={self.second.public_id}'
        response = self.client.get(f'{url}?{stops}&order=nearest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))
        bad = self.client.get(f'{url}?location=not-a-uuid')
        self.assertRedirects(bad, reverse('partners:map'))
