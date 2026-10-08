import re
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounts.models import User, UserProfile
from integration.models import MoodleInstance, MoodleUserMapping
from integration.services import MoodleIntegrationService
from integration.utils import MOODLE_PASSWORD_SYMBOLS, generate_moodle_password


def make_user(username, role=User.STUDENT, verified=True, phone=None):
    user = User.objects.create(
        username=username, first_name='Test', last_name=username.title(), role=role,
        email=f'{username}@example.org', phone_number=phone, is_verified=verified,
    )
    UserProfile.objects.create(user=user, country='South Africa', region='Limpopo')
    return user


def map_user(user, moodle_user_id):
    mapping, _created = MoodleUserMapping.objects.get_or_create(
        cas_user=user, defaults={'moodle_username': user.username},
    )
    mapping.moodle_user_id = moodle_user_id
    mapping.sync_status = 'synced'
    mapping.save()
    return mapping


class PasswordGeneratorTests(TestCase):
    def test_meets_moodle_default_policy(self):
        for _ in range(200):
            password = generate_moodle_password()
            self.assertEqual(len(password), 24)
            self.assertRegex(password, r'[a-z]')
            self.assertRegex(password, r'[A-Z]')
            self.assertRegex(password, r'\d')
            self.assertTrue(any(character in MOODLE_PASSWORD_SYMBOLS for character in password))

    def test_never_repeats(self):
        self.assertEqual(len({generate_moodle_password() for _ in range(500)}), 500)

    def test_short_lengths_are_raised_to_twelve(self):
        self.assertEqual(len(generate_moodle_password(4)), 12)


class SyncUsersPasswordTests(TestCase):
    def setUp(self):
        self.instance = MoodleInstance.objects.create(name='Test', base_url='http://moodle.test', ws_token='x')

    def test_new_moodle_accounts_get_random_passwords(self):
        student = make_user('thabo.molefe', phone='+27710000001')
        with mock.patch('integration.services.MoodleClient') as client_class:
            client = client_class.return_value
            client.get_user_by_username.return_value = None
            client.create_user.return_value = 501
            MoodleIntegrationService(self.instance).sync_users(user_ids=[student.pk])
        sent = client.create_user.call_args.args[0]['password']
        self.assertNotEqual(sent, f'JH{student.username}123!')
        self.assertNotIn(student.username, sent)
        self.assertRegex(sent, r'^.{24}$')
        student.moodle_mapping.refresh_from_db()
        self.assertEqual(student.moodle_mapping.moodle_user_id, 501)


class ResetMoodlePasswordsCommandTests(TestCase):
    def setUp(self):
        self.instance = MoodleInstance.objects.create(name='Test', base_url='http://moodle.test', ws_token='x')
        self.students = [make_user(f'student{number}', phone=f'+2771000010{number}') for number in range(3)]
        for number, student in enumerate(self.students, start=1):
            map_user(student, 100 + number)
        self.dean = make_user('dean.one', role=User.DEAN, phone='+27710000200')
        map_user(self.dean, 900)
        self.unsynced = make_user('student.unsynced', phone='+27710000300')

    def run_command(self, *args):
        output = StringIO()
        with mock.patch('integration.management.commands.reset_moodle_passwords.MoodleClient') as client_class:
            client = client_class.return_value
            call_command('reset_moodle_passwords', *args, '--pause', '0', stdout=output)
        return client, output.getvalue()

    def sent_users(self, client):
        users = []
        for call in client._make_request.call_args_list:
            users.extend(call.args[1]['users'])
        return users

    def test_dry_run_sends_nothing(self):
        client, output = self.run_command()
        client._make_request.assert_not_called()
        client.update_user.assert_not_called()
        self.assertIn('3 account(s) selected', output)
        self.assertIn('Dry run', output)

    def test_apply_resets_mapped_students_only(self):
        client, output = self.run_command('--apply')
        users = self.sent_users(client)
        self.assertEqual(sorted(user['id'] for user in users), [101, 102, 103])
        passwords = [user['password'] for user in users]
        self.assertEqual(len(set(passwords)), 3)
        for password in passwords:
            self.assertFalse(password.startswith('JH'))
            self.assertNotIn(password, output)
        self.assertIn('3 password(s) replaced', output)

    def test_include_staff_and_exclude(self):
        client, _output = self.run_command('--apply', '--include-staff', '--exclude', 'STUDENT1')
        ids = sorted(user['id'] for user in self.sent_users(client))
        self.assertEqual(ids, [101, 103, 900])

    def test_failed_batch_falls_back_to_single_updates(self):
        output = StringIO()
        with mock.patch('integration.management.commands.reset_moodle_passwords.MoodleClient') as client_class:
            client = client_class.return_value
            client._make_request.side_effect = RuntimeError('invalid parameter')

            def update(user_id, data):
                if user_id == 102:
                    raise RuntimeError('user is a site admin')
                return True

            client.update_user.side_effect = update
            call_command('reset_moodle_passwords', '--apply', '--pause', '0', stdout=output)
        text = output.getvalue()
        self.assertEqual(client.update_user.call_count, 3)
        self.assertIn('2 password(s) replaced', text)
        self.assertIn('student1', text)
        self.assertIsNone(re.search(r'password\W+[A-Za-z0-9!#$%*+\-=?@^_]{24}', text))

    def test_needs_a_moodle_instance(self):
        MoodleInstance.objects.all().delete()
        with self.assertRaises(CommandError):
            call_command('reset_moodle_passwords', stdout=StringIO())
