from django.contrib.auth import authenticate
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User

PASSWORD = 'Correct-horse-9'


def make(username, **extra):
    user = User.objects.create(username=username, role=extra.pop('role', User.LEADER), is_verified=True, **extra)
    user.set_password(PASSWORD)
    user.save()
    return user


class SignInTestCase(TestCase):
    def setUp(self):
        self.leader = make('testleader.leader.771e', email='Test@Leader.com', phone_number='0722309577')
        self.international = make('ngozi', email='ngozi@example.org', phone_number='+2348012345678')

    def signs_in(self, identifier, password=PASSWORD):
        user = authenticate(username=identifier, password=password)
        return user.username if user else None


class SwitchOffTests(SignInTestCase):
    """Exactly as sign-in has always worked, which is what SNAP keeps."""

    def test_username_and_international_phone(self):
        self.assertEqual(self.signs_in('testleader.leader.771e'), 'testleader.leader.771e')
        self.assertEqual(self.signs_in('+2348012345678'), 'ngozi')

    def test_email_and_local_phone_do_not(self):
        self.assertIsNone(self.signs_in('test@leader.com'))
        self.assertIsNone(self.signs_in('0722309577'))

    def test_login_page_wording_is_unchanged(self):
        page = self.client.get(reverse('accounts:login'))
        self.assertContains(page, 'Use your username or phone number, with the country code.')


@override_settings(SCHOLR_EMAIL_SIGN_IN_ENABLED=True)
class SwitchOnTests(SignInTestCase):
    def test_email_in_any_case(self):
        self.assertEqual(self.signs_in('test@leader.com'), 'testleader.leader.771e')
        self.assertEqual(self.signs_in('  TEST@LEADER.COM '), 'testleader.leader.771e')

    def test_phone_as_it_was_saved_or_with_spaces(self):
        self.assertEqual(self.signs_in('0722309577'), 'testleader.leader.771e')
        self.assertEqual(self.signs_in('072 230 9577'), 'testleader.leader.771e')
        self.assertEqual(self.signs_in('+234 801 234 5678'), 'ngozi')

    def test_username_still_works_and_ignores_case(self):
        self.assertEqual(self.signs_in('testleader.leader.771e'), 'testleader.leader.771e')
        self.assertEqual(self.signs_in('NGOZI'), 'ngozi')

    def test_wrong_password_is_refused(self):
        self.assertIsNone(self.signs_in('test@leader.com', 'wrong'))

    def test_shared_email_is_never_used(self):
        make('second.account', email='TEST@leader.com')
        self.assertIsNone(self.signs_in('test@leader.com'))
        self.assertEqual(self.signs_in('testleader.leader.771e'), 'testleader.leader.771e')

    def test_an_exact_username_beats_an_email(self):
        make('lee@example.org', email='other@example.org')
        make('someone', email='lee@example.org')
        self.assertEqual(self.signs_in('lee@example.org'), 'lee@example.org')

    def test_inactive_accounts_are_refused(self):
        User.objects.filter(pk=self.leader.pk).update(is_active=False)
        self.assertIsNone(self.signs_in('test@leader.com'))

    def test_signing_in_through_the_page(self):
        response = self.client.post(reverse('accounts:login'), {'username': 'test@leader.com', 'password': PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.leader.pk)

    def test_login_page_wording(self):
        page = self.client.get(reverse('accounts:login'))
        self.assertContains(page, 'Use your username, email address or phone number.')
        self.assertContains(page, 'Username, email or phone number')


class ExistingSessionsTests(TestCase):
    def test_people_signed_in_before_the_deploy_stay_signed_in(self):
        user = make('already.in', role=User.ADMIN)
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('accounts:admin_dashboard')).status_code, 200)
