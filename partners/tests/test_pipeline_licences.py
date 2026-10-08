from datetime import timedelta

from django.core.files.base import ContentFile
from django.test import override_settings
from django.utils import timezone

from partners.exceptions import GateNotMet, InvalidTransition, LicenceStateError
from partners.models import Cohort, Licence, LocationMembership, PartnerOrganisation, PartnershipEvent, Stage
from partners.services import licences, pipeline

from .base import PartnersTestCase
from .helpers import add_member, make_cohort, make_licence, make_location, make_organisation, make_staff, make_user


class PipelineMoveTests(PartnersTestCase):
    def setUp(self):
        self.staff = make_staff()
        self.organisation = make_organisation()
        self.partnership = self.organisation.partnership

    def test_forward_one_step_records_event(self):
        pipeline.move(self.partnership, Stage.REVIEW, self.staff)
        self.partnership.refresh_from_db()
        self.assertEqual(self.partnership.stage, Stage.REVIEW)
        event = self.partnership.events.get(kind=PartnershipEvent.Kind.STAGE)
        self.assertEqual((event.from_stage, event.to_stage), (Stage.ENQUIRY, Stage.REVIEW))

    def test_skipping_needs_a_note(self):
        with self.assertRaises(InvalidTransition):
            pipeline.move(self.partnership, Stage.STRATEGY, self.staff)
        pipeline.move(self.partnership, Stage.STRATEGY, self.staff, note='Met at the conference')
        self.partnership.refresh_from_db()
        self.assertEqual(self.partnership.stage, Stage.STRATEGY)

    def test_only_staff_can_skip(self):
        outsider = make_user('partner')
        with self.assertRaises(InvalidTransition):
            pipeline.move(self.partnership, Stage.STRATEGY, outsider, note='skip')

    def test_admin_training_gate_needs_a_licence(self):
        pipeline.move(self.partnership, Stage.STRATEGY, self.staff, note='fast track')
        with self.assertRaises(GateNotMet):
            pipeline.move(self.partnership, Stage.ADMIN_TRAINING, self.staff)
        make_licence(self.organisation, status=Licence.Status.DRAFT)
        pipeline.move(self.partnership, Stage.ADMIN_TRAINING, self.staff)

    def test_skip_checks_every_gate_it_passes(self):
        make_licence(self.organisation, status=Licence.Status.DRAFT)
        with self.assertRaises(GateNotMet) as raised:
            pipeline.move(self.partnership, Stage.LAUNCHED, self.staff, note='skip all')
        self.assertIn('coordinator', raised.exception.message)

    def test_launch_gate_and_automatic_launch(self):
        location = make_location(self.organisation)
        coordinator = add_member(location)
        pipeline.move(self.partnership, Stage.FACILITATOR_TRAINING, self.staff, note='trained elsewhere')
        cohort = make_cohort(location, facilitators=[coordinator], status=Cohort.Status.PLANNED)
        self.assertFalse(pipeline.mark_launched_if_first_cohort(cohort, coordinator))
        cohort.status = Cohort.Status.RUNNING
        cohort.save()
        self.assertTrue(pipeline.mark_launched_if_first_cohort(cohort, coordinator))
        self.partnership.refresh_from_db()
        self.assertEqual(self.partnership.stage, Stage.LAUNCHED)

    def test_pause_resumes_where_it_left(self):
        pipeline.move(self.partnership, Stage.REVIEW, self.staff)
        pipeline.move(self.partnership, Stage.PAUSED, self.staff)
        with self.assertRaises(InvalidTransition):
            pipeline.move(self.partnership, Stage.INTRO_CALL, self.staff)
        pipeline.move(self.partnership, Stage.REVIEW, self.staff)
        self.partnership.refresh_from_db()
        self.assertEqual(self.partnership.stage_before_pause, '')

    def test_lost_needs_reason_and_reopens_as_enquiry(self):
        with self.assertRaises(InvalidTransition):
            pipeline.move(self.partnership, Stage.LOST, self.staff)
        pipeline.move(self.partnership, Stage.LOST, self.staff, note='No budget this year')
        with self.assertRaises(InvalidTransition):
            pipeline.move(self.partnership, Stage.REVIEW, self.staff)
        pipeline.move(self.partnership, Stage.ENQUIRY, self.staff)
        self.partnership.refresh_from_db()
        self.assertEqual(self.partnership.lost_reason, '')


class EnquiryIntakeTests(PartnersTestCase):
    DATA = {
        'organisation_name': 'Hope Chapel', 'kind': 'church', 'country': 'KE', 'city': 'Nairobi',
        'contact_name': 'Ruth Wanjiku', 'role_title': 'Pastor', 'email': 'Ruth@Example.org',
        'phone': '+254700000001', 'expected_participants': 20, 'message': 'We have 20 leaders.',
    }

    def test_new_enquiry_creates_partner(self):
        partnership, created = pipeline.intake_enquiry(dict(self.DATA))
        self.assertTrue(created)
        organisation = partnership.organisation
        self.assertEqual(organisation.contacts.get().email, 'ruth@example.org')
        self.assertEqual(partnership.source, partnership.Source.WEB_ENQUIRY)
        self.assertIn('We have 20 leaders.', partnership.events.get().body)

    def test_repeat_enquiry_attaches_to_existing(self):
        pipeline.intake_enquiry(dict(self.DATA))
        partnership, created = pipeline.intake_enquiry(dict(self.DATA, organisation_name='HOPE CHAPEL'))
        self.assertFalse(created)
        self.assertEqual(PartnerOrganisation.objects.count(), 1)
        self.assertEqual(partnership.events.count(), 2)


@override_settings(PARTNERS_DEFAULT_CURRENCY='CAD')
class LicenceTests(PartnersTestCase):
    def setUp(self):
        self.staff = make_staff()
        self.organisation = make_organisation(country='ZA')
        self.today = timezone.localdate()

    def draft(self, **extra):
        licence = Licence(
            organisation=self.organisation, starts_on=self.today, ends_on=self.today + timedelta(days=365), **extra,
        )
        return licences.create_licence(licence, self.staff)

    def test_number_is_sequential_per_country_and_year(self):
        first, second = self.draft(), self.draft()
        self.assertEqual(first.number, f'METP-ZA-{self.today.year}-0001')
        self.assertEqual(second.number, f'METP-ZA-{self.today.year}-0002')
        self.assertEqual(first.fee_currency, 'CAD')

    def test_activation_needs_signature_and_agreement(self):
        licence = self.draft()
        with self.assertRaises(LicenceStateError):
            licences.activate(licence, self.staff, today=self.today)
        licence.signed_on = self.today
        licence.agreement_file.save('a.pdf', ContentFile(b'%PDF-1.4'), save=True)
        licences.activate(licence, self.staff, today=self.today)
        licence.refresh_from_db()
        self.assertEqual(licence.status, Licence.Status.ACTIVE)

    def test_one_active_licence_per_partner(self):
        make_licence(self.organisation)
        second = make_licence(self.organisation, status=Licence.Status.DRAFT)
        with self.assertRaises(LicenceStateError):
            licences.activate(second, self.staff, today=self.today)

    def test_ended_licence_cannot_be_activated(self):
        licence = make_licence(
            self.organisation, status=Licence.Status.DRAFT,
            starts_on=self.today - timedelta(days=400), ends_on=self.today - timedelta(days=1),
        )
        with self.assertRaises(LicenceStateError):
            licences.activate(licence, self.staff, today=self.today)

    def test_suspend_and_terminate_need_reasons(self):
        licence = make_licence(self.organisation)
        with self.assertRaises(LicenceStateError):
            licences.suspend(licence, self.staff, '')
        licences.suspend(licence, self.staff, 'Fee unpaid')
        licences.activate(licence, self.staff, today=self.today)
        with self.assertRaises(LicenceStateError):
            licences.terminate(licence, self.staff, ' ')
        licences.terminate(licence, self.staff, 'Partnership ended')
        licence.refresh_from_db()
        self.assertEqual(licence.status, Licence.Status.TERMINATED)

    def test_expire_overdue(self):
        licence = make_licence(self.organisation, ends_on=self.today - timedelta(days=1))
        self.assertEqual(licences.expire_overdue(self.today), 1)
        licence.refresh_from_db()
        self.assertEqual(licence.status, Licence.Status.EXPIRED)

    def test_recording_rules_by_status(self):
        location = make_location(self.organisation)
        licence = location.licence
        running = make_cohort(location)
        planned = make_cohort(location, status=Cohort.Status.PLANNED)
        self.assertTrue(licences.allows_recording(licence, running))
        licence.status = Licence.Status.SUSPENDED
        self.assertTrue(licences.allows_recording(licence, running))
        self.assertFalse(licences.allows_recording(licence, planned))
        self.assertFalse(licences.allows_new_cohorts(licence))
        licence.status = Licence.Status.EXPIRED
        self.assertTrue(licences.allows_recording(licence, running))
        licence.status = Licence.Status.TERMINATED
        self.assertFalse(licences.allows_recording(licence, running))

    def test_reminders_on_exact_offsets(self):
        make_licence(self.organisation, ends_on=self.today + timedelta(days=30))
        other = make_organisation()
        make_licence(other, ends_on=self.today + timedelta(days=31))
        self.assertEqual(licences.reminders_due(self.today).count(), 1)


class ScopeRoleTests(PartnersTestCase):
    def test_coordinator_outranks_facilitator(self):
        from partners.services import scope
        location = make_location()
        user = add_member(location, LocationMembership.Role.FACILITATOR)
        add_member(location, LocationMembership.Role.COORDINATOR, user=user)
        self.assertEqual(scope.role_at(user, location), scope.COORDINATOR)
        LocationMembership.objects.filter(user=user, role='coordinator').update(is_active=False)
        self.assertEqual(scope.role_at(user, location), scope.FACILITATOR)
