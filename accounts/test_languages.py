import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.checks import run_checks
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User

ON = settings.MIDDLEWARE[:]
ON.insert(ON.index('django.contrib.sessions.middleware.SessionMiddleware') + 1, 'django.middleware.locale.LocaleMiddleware')
ON.insert(ON.index('django.contrib.auth.middleware.AuthenticationMiddleware') + 1, 'scholr.middleware.UserLanguageMiddleware')
MULTILINGUAL = override_settings(SCHOLR_MULTILINGUAL_ENABLED=True, MIDDLEWARE=ON)


def make(role=User.STUDENT, **extra):
    user = User.objects.create(username=f'{role}{User.objects.count()}', role=role, is_verified=True, **extra)
    user.set_password('Correct-horse-9')
    user.save()
    return user


class SwitchOffTests(TestCase):
    """SNAP keeps English everywhere, with no picker and no way to change it."""

    def test_english_with_no_picker(self):
        page = self.client.get(reverse('accounts:login'), HTTP_ACCEPT_LANGUAGE='fr')
        self.assertContains(page, 'Sign in')
        self.assertNotContains(page, 'Se connecter')
        self.assertNotContains(page, reverse('accounts:set_language'))

    def test_changing_language_does_nothing(self):
        response = self.client.post(reverse('accounts:set_language'), {'language': 'fr', 'next': '/'})
        self.assertNotIn(settings.LANGUAGE_COOKIE_NAME, response.cookies)


@MULTILINGUAL
class PageLanguageTests(TestCase):
    def test_browser_language_is_used_before_anyone_chooses(self):
        page = self.client.get(reverse('accounts:login'), HTTP_ACCEPT_LANGUAGE='sw')
        self.assertContains(page, 'Ingia')
        self.assertContains(page, '<html lang="sw"')

    def test_choosing_a_language_sets_a_cookie_and_returns_to_the_page(self):
        response = self.client.post(reverse('accounts:set_language'), {'language': 'pt', 'next': '/dashboard/login/'})
        self.assertRedirects(response, '/dashboard/login/', fetch_redirect_response=False)
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, 'pt')
        self.assertContains(self.client.get(reverse('accounts:login')), 'Iniciar sessão')

    def test_unknown_languages_and_off_site_returns_are_refused(self):
        response = self.client.post(reverse('accounts:set_language'),
                                    {'language': 'xx', 'next': 'https://evil.example/'})
        self.assertNotIn(settings.LANGUAGE_COOKIE_NAME, response.cookies)
        self.assertFalse(response['Location'].startswith('https://evil.example'))

    def test_picker_lists_each_language_in_itself(self):
        page = self.client.get(reverse('accounts:login'))
        for name in ('English', 'Français', 'Kiswahili', 'Português', 'Español'):
            self.assertContains(page, name)

    def test_signed_in_choice_is_saved_and_wins_over_the_browser(self):
        user = make()
        self.client.force_login(user)
        with mock.patch('integration.tasks.queue_moodle_language_update') as queued:
            self.client.post(reverse('accounts:set_language'), {'language': 'es', 'next': '/'})
        user.refresh_from_db()
        self.assertEqual(user.preferred_language, 'es')
        queued.assert_called_once_with(user.pk)

        another_device = self.client_class()
        another_device.force_login(user)
        page = another_device.get(reverse('accounts:profile'), HTTP_ACCEPT_LANGUAGE='fr')
        self.assertContains(page, 'lang="es"')


class MoodleLanguageTests(TestCase):
    def test_follows_the_chosen_language_only_when_switched_on(self):
        from integration.languages import moodle_language
        user = make(preferred_language='fr')
        self.assertEqual(moodle_language(user), 'en')
        with MULTILINGUAL:
            self.assertEqual(moodle_language(user), 'fr')
            self.assertEqual(moodle_language(make()), 'en')

    @MULTILINGUAL
    @override_settings(SCHOLR_STAFF_LMS_PORTAL_ENABLED=True)
    def test_new_staff_accounts_are_created_in_their_language(self):
        from integration.models import MoodleInstance
        from integration.staff_access import StaffLMSAccess
        MoodleInstance.objects.create(name='Test', base_url='http://moodle.test', ws_token='x',
                                      is_active=True, is_primary=True)
        payload = StaffLMSAccess(make(User.ADMIN, preferred_language='sw'))._new_account_payload('someone')
        self.assertEqual(payload['lang'], 'sw')

    @MULTILINGUAL
    def test_a_changed_language_is_sent_to_moodle(self):
        from integration.models import MoodleUserMapping
        from integration.tasks import update_moodle_language
        user = make(preferred_language='pt')
        MoodleUserMapping.objects.update_or_create(
            cas_user=user, defaults={'moodle_user_id': 77, 'moodle_username': user.username})
        with mock.patch('integration.moodle_client.MoodleClient') as client:
            self.assertEqual(update_moodle_language.apply(args=[user.pk]).get(), {'user': user.pk, 'lang': 'pt'})
        client.return_value.update_user.assert_called_once_with(77, {'lang': 'pt'})


@override_settings(SCHOLR_STAFF_LMS_PORTAL_ENABLED=True, MOODLE_COHORT_VIEWER_ROLE_ID=9,
                   MOODLE_STAFF_COURSE_ROLES={'admin': 3, 'programme_staff': 3, 'dean': 4})
class RoleChangeMoodleTests(TestCase):
    def queued(self, old_role, new_role):
        from integration.tasks import queue_staff_lms_revocation
        with mock.patch('integration.tasks.transaction.on_commit') as on_commit:
            queue_staff_lms_revocation(1, old_role, new_role)
        return on_commit.called

    def test_only_a_different_moodle_role_removes_access(self):
        self.assertTrue(self.queued(User.ADMIN, User.LEADER))
        self.assertTrue(self.queued(User.DEAN, User.HUB_ADMIN))
        self.assertFalse(self.queued(User.ADMIN, User.PROGRAMME_STAFF))
        self.assertFalse(self.queued(User.HUB_ADMIN, User.AMBASSADOR))
        self.assertFalse(self.queued(User.LEADER, User.ADMIN))

    @override_settings(SCHOLR_STAFF_LMS_PORTAL_ENABLED=False)
    def test_nothing_without_the_lms_portal(self):
        self.assertFalse(self.queued(User.ADMIN, User.LEADER))

    def test_old_enrolments_are_removed_from_every_course(self):
        from integration.models import Course, CourseCategory, MoodleUserMapping
        from integration.tasks import revoke_staff_lms_access
        user = make(User.LEADER)
        MoodleUserMapping.objects.update_or_create(
            cas_user=user, defaults={'moodle_user_id': 41, 'moodle_username': user.username})
        category = CourseCategory.objects.create(name='Ministry', discipline='theology')
        for number in (101, 102):
            Course.objects.create(title=f'METP{number}', short_name=f'METP{number}', category=category,
                                  moodle_course_id=number)
        with mock.patch('integration.moodle_client.MoodleClient') as client:
            result = revoke_staff_lms_access.apply(args=[user.pk, User.ADMIN]).get()
        self.assertEqual(result['removed'], 2)
        client.return_value.unenrol_user.assert_any_call(41, 101, 3)
        client.return_value.unenrol_user.assert_any_call(41, 102, 3)

    def test_a_role_change_triggers_it(self):
        from accounts.roles import change_role
        admin, other = make(User.ADMIN), make(User.ADMIN)
        with mock.patch('integration.tasks.queue_staff_lms_revocation') as queued:
            change_role(other, User.LEADER, admin)
        queued.assert_called_once_with(other.pk, User.ADMIN, User.LEADER)


class CatalogueTests(TestCase):
    """Guards for the rule that every feature is translatable: catalogues complete and compiled."""

    def test_every_language_has_a_complete_compiled_catalogue(self):
        for code, _name in settings.LANGUAGES:
            if code == 'en':
                continue
            folder = Path(settings.LOCALE_PATHS[0]) / code / 'LC_MESSAGES'
            source = (folder / 'django.po').read_text(encoding='utf-8')
            untranslated = re.findall(r'\nmsgid "(.+)"\nmsgstr ""\n\n', source + '\n')
            self.assertEqual(untranslated, [], code)
            self.assertTrue((folder / 'django.mo').exists(), code)
            # A compiled catalogue that lags its source shows up as a string Django cannot translate
            from django.utils import translation
            pairs = re.findall(r'\nmsgid "(.+)"\nmsgstr "(.+)"\n', source)
            with translation.override(code):
                stale = [msgid for msgid, msgstr in pairs if translation.gettext(msgid) != msgstr.replace('\\"', '"')]
            self.assertEqual(stale, [], f'{code}: run compilemessages')

    def test_no_duplicate_template_tag_libraries(self):
        self.assertNotIn('templates.W003', [message.id for message in run_checks()])
