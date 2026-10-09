import itertools
from datetime import timedelta

from django.core.cache import caches
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import User, UserProfile

LOCAL_CACHES = {
    alias: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'accounts-pages-{alias}'}
    for alias in ('default', 'sessions', 'moodle')
}
PASSWORD = 'a-strong-test-password'
PHONES = itertools.count(1)


def make(username, role, verified=True, region='Limpopo', **profile):
    user = User.objects.create(
        username=username, role=role, first_name=username.title(), last_name='Tester',
        email=f'{username}@example.org', is_verified=verified,
        phone_number=f'+2779{next(PHONES):07d}',
    )
    user.set_password(PASSWORD)
    user.save()
    defaults = {'country': 'South Africa', 'region': region, 'church_name': 'Grace Church', 'education_level': ''}
    defaults.update(profile)
    UserProfile.objects.update_or_create(user=user, defaults=defaults)
    return user


@override_settings(CACHES=LOCAL_CACHES, SCHOLR_PARTNERS_ENABLED=True)
class AccountPagesTests(TestCase):
    def setUp(self):
        for alias in LOCAL_CACHES:
            caches[alias].clear()
        self.admin = make('admin.one', User.ADMIN)
        self.dean = make('dean.one', User.DEAN)
        self.student = make('student.one', User.STUDENT)
        self.pending = make('student.two', User.STUDENT, verified=False)

    def test_every_page_renders_for_its_role(self):
        pages = {
            self.admin: [
                'accounts:admin_dashboard', 'accounts:analytics', 'accounts:student_list',
                'accounts:profile', 'accounts:profile_edit', 'accounts:profile_complete',
            ],
            self.dean: ['accounts:dean_dashboard', 'accounts:student_list'],
            self.student: ['accounts:student_dashboard', 'accounts:profile'],
        }
        for user, names in pages.items():
            self.client.force_login(user)
            for name in names:
                with self.subTest(user=user.username, page=name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        self.client.force_login(self.admin)
        for name in ('accounts:student_detail', 'accounts:verify_student'):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name, args=[self.pending.id])).status_code, 200)

    def test_admin_dashboard_shows_the_partner_network_when_enabled(self):
        from partners.models import Licence, PartnerOrganisation, Partnership

        organisation = PartnerOrganisation.objects.create(name='Hope Chapel', country='KE')
        Partnership.objects.create(organisation=organisation, stage='intro_call',
                                   next_action_due=timezone.localdate() - timedelta(days=1))
        Licence.objects.create(
            organisation=organisation, number='METP-KE-2026-0001', status=Licence.Status.ACTIVE,
            starts_on=timezone.localdate() - timedelta(days=300), ends_on=timezone.localdate() + timedelta(days=20),
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse('accounts:admin_dashboard'))
        self.assertContains(response, 'Partner network')
        self.assertContains(response, 'METP-KE-2026-0001')
        self.assertContains(response, '20 days left')
        self.assertContains(response, '1 next step overdue')

    @override_settings(SCHOLR_PARTNERS_ENABLED=False)
    def test_admin_dashboard_leaves_the_network_out_when_disabled(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('accounts:admin_dashboard'))
        self.assertNotIn('network', response.context)
        self.assertNotContains(response, 'Licences ending soon')

    def test_login_keeps_next(self):
        target = reverse('accounts:profile')
        page = self.client.get(f"{reverse('accounts:login')}?next={target}")
        self.assertContains(page, f'name="next" value="{target}"')
        response = self.client.post(reverse('accounts:login'), {
            'username': self.student.username, 'password': PASSWORD, 'remember_me': 'on', 'next': target,
        })
        self.assertRedirects(response, target)

    def test_remove_verification_is_a_post_form(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse('accounts:student_detail', args=[self.student.id]))
        action = reverse('accounts:unverify_student', args=[self.student.id])
        self.assertContains(page, f'action="{action}"')
        self.client.post(action)
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_verified)

    def test_completing_a_profile_keeps_fields_the_page_does_not_show(self):
        UserProfile.objects.filter(user=self.student).update(
            bio='Pastor for ten years.', village_town='Mokopane', whatsapp_number='+27711111111',
        )
        self.client.force_login(self.student)
        response = self.client.post(reverse('accounts:profile_complete'), {
            'first_name': 'Student', 'last_name': 'Tester', 'email': self.student.email,
            'phone_number': self.student.phone_number, 'country': 'South Africa', 'region': 'Limpopo',
            'church_name': 'Grace Church', 'church_size': '', 'position_title': 'Elder',
            'education_level': UserProfile.EDUCATION_CHOICES[0][0],
            'languages_spoken': 'English',
        })
        self.assertRedirects(response, reverse('accounts:dashboard'), fetch_redirect_response=False)
        profile = UserProfile.objects.get(user=self.student)
        self.assertEqual(profile.bio, 'Pastor for ten years.')
        self.assertEqual(profile.village_town, 'Mokopane')
        self.assertEqual(profile.whatsapp_number, '+27711111111')
        self.assertEqual(profile.position_title, 'Elder')

    def test_completion_requires_the_fields_that_unlock_access(self):
        self.client.force_login(self.student)
        response = self.client.post(reverse('accounts:profile_complete'), {
            'first_name': 'Student', 'last_name': 'Tester', 'email': self.student.email,
            'phone_number': self.student.phone_number, 'country': '', 'region': '', 'church_name': '',
            'education_level': '',
        })
        self.assertEqual(response.status_code, 200)
        for field in ('country', 'region', 'church_name', 'education_level'):
            self.assertIn(field, response.context['profile_form'].errors)

    def test_analytics_fills_thirty_days(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('accounts:analytics'))
        self.assertEqual(len(response.context['recent_registrations']), 30)
        self.assertEqual(response.context['registrations_total'], 4)
