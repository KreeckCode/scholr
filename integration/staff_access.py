"""
Gets admins, deans and programme staff into Moodle as teaching staff.

Two steps, each safe to repeat:
    ensure_account  finds, links or creates the person's Moodle account
    grant_access    enrols them as a teacher in every synced course

The dashboard runs them one after the other so the person can watch progress,
then opens the LMS through the normal auth_userkey single sign-on.
"""
import logging

from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from accounts.models import User
from partners.countries import COUNTRIES
from .exceptions import MoodleAPIError
from .models import Course, MoodleUserMapping
from .services import MoodleIntegrationService
from .utils import generate_moodle_password

logger = logging.getLogger(__name__)

ACCESS_CACHE_SECONDS = 6 * 60 * 60

COUNTRY_CODES = {name.lower(): code for code, name in COUNTRIES}
COUNTRY_CODE_SET = set(COUNTRY_CODES.values())

ROLE_NAMES = {
    1: 'Manager',
    3: 'Teacher',
    4: 'Non-editing teacher',
}


class StaffAccessError(Exception):
    """A problem the person can read and act on, shown as-is in the progress panel."""


def role_name(role_id):
    return ROLE_NAMES.get(role_id, f'Moodle role {role_id}')


def country_code(value):
    """ISO code for a stored country name or code, or None so Moodle keeps its default."""
    value = (value or '').strip()
    if value.upper() in COUNTRY_CODE_SET:
        return value.upper()
    return COUNTRY_CODES.get(value.lower())


def _access_cache_key(user):
    return f'staff_lms_access:{user.pk}'


class StaffLMSAccess:
    """Wraps one staff member's journey into Moodle."""

    def __init__(self, user, service=None):
        if not user.can_open_lms_as_staff():
            raise StaffAccessError('Your account type cannot open the LMS as staff.')
        self.user = user
        self.service = service or MoodleIntegrationService()
        self.client = self.service.client

    # ==================== ACCOUNT ====================

    def ensure_account(self):
        """
        Return {'status': 'existing' | 'linked' | 'created', 'moodle_user_id': int}.
        A synced mapping is trusted without calling Moodle, which keeps repeat visits fast.
        """
        mapping = MoodleUserMapping.objects.filter(cas_user=self.user).first()
        if mapping and mapping.moodle_user_id and mapping.sync_status == 'synced':
            return {'status': 'existing', 'moodle_user_id': mapping.moodle_user_id}

        moodle_username = mapping.moodle_username if mapping else self.user.username.lower()

        found = self.client.get_user_by_username(moodle_username)
        if found and not self._same_person(found):
            raise StaffAccessError(
                f'An LMS account called "{moodle_username}" already exists with a different '
                f'email address. Ask a Moodle administrator to check which account is yours.'
            )

        if not found and self.user.email:
            found = self.client.get_user_by_email(self.user.email.strip())

        if found:
            self._guard_unclaimed(found['id'])
            mapping = self._save_mapping(mapping, found['username'], found['id'])
            # A previous failed attempt may still be cached against the old account
            cache.delete(_access_cache_key(self.user))
            logger.info(f"Linked {self.user.username} to existing Moodle user {found['id']}")
            return {'status': 'linked', 'moodle_user_id': found['id']}

        moodle_user_id = self.client.create_user(self._new_account_payload(moodle_username))
        self._save_mapping(mapping, moodle_username, moodle_user_id)
        cache.delete(_access_cache_key(self.user))
        logger.info(f'Created Moodle user {moodle_user_id} for staff member {self.user.username}')
        return {'status': 'created', 'moodle_user_id': moodle_user_id}

    def _same_person(self, moodle_user):
        """Only link an existing account when both sides carry the same email."""
        ours = (self.user.email or '').strip().lower()
        theirs = (moodle_user.get('email') or '').strip().lower()
        return bool(ours) and ours == theirs

    def _guard_unclaimed(self, moodle_user_id):
        claimed = (
            MoodleUserMapping.objects
            .filter(moodle_user_id=moodle_user_id)
            .exclude(cas_user=self.user)
            .select_related('cas_user')
            .first()
        )
        if claimed:
            raise StaffAccessError(
                f'That LMS account is already linked to {claimed.cas_user.username}. '
                f'Ask a Moodle administrator to sort out the duplicate.'
            )

    @transaction.atomic
    def _save_mapping(self, mapping, moodle_username, moodle_user_id):
        if mapping is None:
            mapping = MoodleUserMapping(cas_user=self.user)
        mapping.moodle_username = moodle_username
        mapping.save()
        mapping.mark_synced(moodle_user_id)
        return mapping

    def _new_account_payload(self, moodle_username):
        user = self.user
        payload = {
            'username': moodle_username,
            'password': generate_moodle_password(),
            # Moodle rejects blank names, and accounts made with createsuperuser often have none
            'firstname': user.first_name.strip() or user.username,
            'lastname': user.last_name.strip() or user.get_role_display(),
            'email': (user.email or '').strip() or f'{moodle_username}@judea.local',
        }
        if user.phone_number:
            payload['phone1'] = user.phone_number

        profile = getattr(user, 'profile', None)
        if profile is not None:
            if profile.region:
                payload['city'] = profile.region
            code = country_code(profile.country)
            if code:
                payload['country'] = code
        return payload

    # ==================== ACCESS ====================

    def grant_access(self):
        """
        Enrol the person as teaching staff in every course that exists in Moodle.
        Returns {'status': 'granted' | 'current', 'courses': int, 'role': str, 'manager': bool|None, 'skipped': [...]}.
        """
        mapping = MoodleUserMapping.objects.filter(cas_user=self.user).first()
        if not mapping or not mapping.moodle_user_id:
            raise StaffAccessError('Your LMS account is not ready yet. Start again from the dashboard.')

        role_id = settings.MOODLE_STAFF_COURSE_ROLES.get(self.user.role)
        if role_id is None:
            raise StaffAccessError('No LMS role is set up for your account type.')

        course_ids = sorted(
            Course.objects
            .filter(moodle_course_id__isnull=False)
            .values_list('moodle_course_id', flat=True)
        )
        wants_manager = (
            settings.MOODLE_ASSIGN_MANAGER_TO_ADMINS and self.user.role == User.ADMIN
        )

        fingerprint = f"{mapping.moodle_user_id}|{role_id}|{wants_manager}|{','.join(map(str, course_ids))}"
        if cache.get(_access_cache_key(self.user)) == fingerprint:
            return {
                'status': 'current', 'courses': len(course_ids),
                'role': role_name(role_id), 'manager': wants_manager or None, 'skipped': [],
            }

        skipped = self._enrol(mapping.moodle_user_id, course_ids, role_id)

        manager = None
        if wants_manager:
            manager = self._assign_manager(mapping.moodle_user_id)

        # Partial results are not cached, so the next visit tries the gaps again
        if not skipped and manager is not False:
            cache.set(_access_cache_key(self.user), fingerprint, ACCESS_CACHE_SECONDS)

        return {
            'status': 'granted', 'courses': len(course_ids) - len(skipped),
            'role': role_name(role_id), 'manager': manager, 'skipped': skipped,
        }

    def _enrol(self, moodle_user_id, course_ids, role_id):
        """One request for every course; course by course only if that batch is refused."""
        if not course_ids:
            return []
        try:
            self.client.enrol_user_in_courses(moodle_user_id, course_ids, role_id)
            return []
        except MoodleAPIError as error:
            logger.warning(f'Batch staff enrolment failed for {self.user.username}, retrying singly: {error}')

        skipped = []
        for course_id in course_ids:
            try:
                self.client.enrol_user(moodle_user_id, course_id, role_id)
            except MoodleAPIError as error:
                logger.error(f'Could not enrol {self.user.username} in Moodle course {course_id}: {error}')
                skipped.append(course_id)
        return skipped

    def _assign_manager(self, moodle_user_id):
        """True when assigned, False when Moodle refused. A refusal never blocks sign-in."""
        try:
            self.client.assign_system_role(moodle_user_id, settings.MOODLE_MANAGER_ROLE_ID)
            return True
        except MoodleAPIError as error:
            logger.warning(f'Could not make {self.user.username} a Moodle manager: {error}')
            return False
