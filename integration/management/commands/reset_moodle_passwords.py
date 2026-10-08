"""
Replace the predictable Moodle passwords given to every synced Scholr user.

Until this fix, sync_users created Moodle accounts with the password JH{username}123!,
so anyone who knew a username could sign in at the Moodle login page. Scholr users reach
Moodle through auth_userkey single sign-on and never type their Moodle password, so
replacing it with a random one changes nothing for them.

    python manage.py reset_moodle_passwords                 # dry run: shows what would change
    python manage.py reset_moodle_passwords --apply
    python manage.py reset_moodle_passwords --apply --exclude teacher.jane --exclude admin

Only accounts mapped to a Scholr student are touched unless --include-staff is given.
Accounts that exist only in Moodle (site admins, teachers made by hand) are never touched.
Passwords are never printed or logged.
"""

import logging
import time

from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from integration.models import MoodleInstance, MoodleUserMapping
from integration.moodle_client import MoodleClient
from integration.utils import chunk_list, generate_moodle_password

logger = logging.getLogger('integration')

BATCH_SIZE = 50


class Command(BaseCommand):
    help = 'Give every synced Moodle account a random password (dry run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Change the passwords. Without it, nothing is sent.')
        parser.add_argument(
            '--exclude', action='append', default=[], metavar='MOODLE_USERNAME',
            help='Moodle username to leave alone, for someone who signs in to Moodle directly. Repeatable.',
        )
        parser.add_argument(
            '--user-id', action='append', type=int, default=[], metavar='SCHOLR_USER_ID',
            help='Only reset these Scholr users. Repeatable.',
        )
        parser.add_argument(
            '--include-staff', action='store_true',
            help='Also reset mapped accounts whose Scholr role is not student.',
        )
        parser.add_argument(
            '--pause', type=float, default=0.5,
            help='Seconds to wait between batches of 50, to go easy on Moodle. Default 0.5.',
        )

    def handle(self, *args, **options):
        instance = MoodleInstance.objects.filter(is_active=True, is_primary=True).first()
        if instance is None:
            raise CommandError('No active primary Moodle instance is configured.')

        mappings = (
            MoodleUserMapping.objects.filter(moodle_user_id__isnull=False)
            .select_related('cas_user')
            .order_by('moodle_user_id')
        )
        if not options['include_staff']:
            mappings = mappings.filter(cas_user__role=User.STUDENT)
        if options['user_id']:
            mappings = mappings.filter(cas_user_id__in=options['user_id'])
        excluded = {name.strip().lower() for name in options['exclude'] if name.strip()}
        targets = [mapping for mapping in mappings if mapping.moodle_username.lower() not in excluded]

        self.stdout.write(f'Moodle instance: {instance.name} ({instance.base_url})')
        self.stdout.write(f'{len(targets)} account(s) selected, {len(excluded)} username(s) excluded.')
        if not targets:
            return
        if not options['apply']:
            for mapping in targets[:20]:
                self.stdout.write(f'  would reset {mapping.moodle_username} (Moodle id {mapping.moodle_user_id})')
            if len(targets) > 20:
                self.stdout.write(f'  ...and {len(targets) - 20} more')
            self.stdout.write(self.style.WARNING('Dry run: nothing was changed. Add --apply to reset them.'))
            return

        client = MoodleClient(base_url=instance.base_url, token=instance.ws_token)
        updated, failed = 0, []
        batches = chunk_list(targets, BATCH_SIZE)
        for position, batch in enumerate(batches, start=1):
            ok, errors = self._reset_batch(client, batch)
            updated += ok
            failed.extend(errors)
            self.stdout.write(f'  batch {position}/{len(batches)}: {ok} reset, {len(errors)} failed')
            if options['pause'] and position < len(batches):
                time.sleep(options['pause'])

        logger.info('reset_moodle_passwords: %s reset, %s failed', updated, len(failed))
        self.stdout.write(self.style.SUCCESS(f'{updated} password(s) replaced.'))
        if failed:
            self.stdout.write(self.style.ERROR(f'{len(failed)} failed; run again with --user-id to retry:'))
            for mapping, message in failed:
                self.stdout.write(f'  {mapping.moodle_username} (Scholr user {mapping.cas_user_id}): {message}')

    def _reset_batch(self, client, batch):
        """One request for the whole batch; if Moodle refuses it, retry one by one to isolate the bad account."""
        payload = [{'id': mapping.moodle_user_id, 'password': generate_moodle_password()} for mapping in batch]
        try:
            client._make_request('core_user_update_users', {'users': payload})
            return len(batch), []
        except Exception as exc:
            logger.warning('Batch password reset failed, retrying individually: %s', exc)

        ok, errors = 0, []
        for mapping in batch:
            try:
                client.update_user(mapping.moodle_user_id, {'password': generate_moodle_password()})
                ok += 1
            except Exception as exc:
                errors.append((mapping, str(exc)[:200]))
        return ok, errors
