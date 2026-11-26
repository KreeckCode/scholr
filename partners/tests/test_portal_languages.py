from django.urls import reverse

from accounts.test_languages import MULTILINGUAL
from partners.models import Cohort

from .base import PartnersTestCase
from .helpers import add_member, enrol, make_cohort, make_location


@MULTILINGUAL
class PortalInFrenchTests(PartnersTestCase):
    def setUp(self):
        super().setUp()
        self.location = make_location()
        self.cohort = make_cohort(self.location, delivery=Cohort.Delivery.BLENDED, status=Cohort.Status.RUNNING)
        enrol(self.cohort)
        self.coordinator = add_member(self.location)
        self.coordinator.preferred_language = 'fr'
        self.coordinator.save(update_fields=['preferred_language'])
        self.client.force_login(self.coordinator)

    def test_dashboard_headings_buttons_and_statuses_are_french(self):
        page = self.client.get(reverse('partners:location_dashboard', kwargs={'location_id': self.location.public_id}))
        self.assertContains(page, 'Nouvelle cohorte')
        self.assertContains(page, 'À venir')
        self.assertNotContains(page, 'New cohort')

    def test_form_labels_are_french(self):
        page = self.client.get(reverse('partners:participant_create', kwargs={'location_id': self.location.public_id}))
        self.assertContains(page, 'Prénom')
        self.assertNotContains(page, '>First name')

    def test_confirmation_messages_are_french(self):
        url = reverse('partners:cohort_status', kwargs={
            'location_id': self.location.public_id, 'cohort_id': self.cohort.public_id})
        response = self.client.post(url, {'status': Cohort.Status.COMPLETED}, follow=True)
        self.assertContains(response, 'Statut de la cohorte mis à jour.')

    def test_switched_off_the_portal_stays_english(self):
        from django.test import override_settings
        from django.conf import settings
        middleware = [m for m in settings.MIDDLEWARE if 'Locale' not in m and 'UserLanguage' not in m]
        from django.utils import translation
        translation.deactivate()
        with override_settings(SCHOLR_MULTILINGUAL_ENABLED=False, MIDDLEWARE=middleware):
            page = self.client.get(reverse('partners:location_dashboard', kwargs={'location_id': self.location.public_id}))
        self.assertContains(page, 'New cohort')
