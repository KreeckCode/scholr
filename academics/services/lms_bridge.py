"""
LMS Bridge Service — Stage 12A: Account Management.

Manages the lifecycle of school learner LMS accounts. Sits between the
academics module and the integration/accounts stack. Never calls MoodleClient
directly — always goes through MoodleIntegrationService.

Stage 12B will add enrollment methods to this service.
"""

import logging
import re
import secrets
import string
import unicodedata
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from academics.models.lms_link import LearnerLMSAccount
from academics.models.learner import Learner
from academics.models.school import School

User = get_user_model()
logger = logging.getLogger(__name__)


@dataclass
class AccountCreationResult:
    """
    Returned by create_lms_account.
    temporary_password is in memory only — never persisted as plaintext.
    """
    lms_account: LearnerLMSAccount
    temporary_password: str
    is_new_account: bool


class LMSBridgeService:
    """
    Orchestrates LMS account lifecycle for school learners.

    The rule: academics side creates the bridge, integration side owns Moodle.
    This service creates accounts.User and hands off to MoodleIntegrationService.
    """

    # ==================== ACCOUNT MANAGEMENT ====================

    @transaction.atomic
    def create_lms_account(
        self,
        learner: Learner,
        created_by,
        notes: str = '',
    ) -> AccountCreationResult:
        """
        Create a User account and Moodle mapping for a learner.

        Idempotent — if an active LMS account already exists, returns it with
        a blank password (the caller should detect is_new_account=False and
        prompt to use reset_password instead).

        Steps:
          1. Check for existing active account
          2. Generate unique username and placeholder email
          3. Generate temporary password
          4. Create accounts.User (role=STUDENT, is_verified=True)
          5. Create MoodleUserMapping (pending)
          6. Sync user to Moodle via MoodleIntegrationService
          7. Create LearnerLMSAccount
        """
        # Idempotency: return existing active account without a new password
        try:
            existing = LearnerLMSAccount.objects.get(learner=learner)
            logger.info(
                f"LMS account already exists for {learner.get_full_name()}: "
                f"{existing.user.username}"
            )
            return AccountCreationResult(
                lms_account=existing,
                temporary_password='',
                is_new_account=False,
            )
        except LearnerLMSAccount.DoesNotExist:
            pass

        username = self._generate_username(learner)
        email = self._generate_email(learner)
        temp_password = self._generate_password()

        # Create the Django User — role=STUDENT so existing LMS dashboards
        # work, but the learner_lms_account reverse accessor identifies them
        # as a school learner for routing purposes.
        user = User(
            username=username,
            first_name=learner.first_name,
            last_name=learner.last_name,
            email=email,
            role='student',
            is_active=True,
            is_verified=True,  # School verification — not LMS dean verification
        )
        if learner.phone_number:
            user.phone_number = learner.phone_number
        user.set_password(temp_password)
        user.save()

        # Create UserProfile to avoid attribute errors in other views
        from accounts.models import UserProfile
        UserProfile.objects.get_or_create(
            user=user,
            defaults={'country': 'South Africa', 'region': ''},
        )

        # Create MoodleUserMapping (pending — sync happens next)
        from integration.models import MoodleUserMapping
        MoodleUserMapping.objects.create(
            cas_user=user,
            moodle_username=username,
            sync_status='pending',
        )

        # Sync to Moodle — if Moodle is unavailable the account still exists
        # in Django with pending status. A "Retry Sync" action handles this later.
        moodle_sync_ok = self._sync_user_to_moodle(user)
        if not moodle_sync_ok:
            logger.warning(
                f"Moodle sync failed for {username} — account created in Django only. "
                f"Use 'Retry Moodle Sync' to complete the setup."
            )

        lms_account = LearnerLMSAccount.objects.create(
            learner=learner,
            user=user,
            is_active=True,
            created_by=created_by,
            notes=notes,
        )

        logger.info(
            f"LMS account created: {learner.get_full_name()} → {username} "
            f"(Moodle sync: {'ok' if moodle_sync_ok else 'pending'})"
        )

        return AccountCreationResult(
            lms_account=lms_account,
            temporary_password=temp_password,
            is_new_account=True,
        )

    @transaction.atomic
    def reset_password(self, lms_account: LearnerLMSAccount, reset_by) -> str:
        """
        Generate and set a new temporary password. Returns the password
        in plaintext for one-time display. Never stored.
        """
        temp_password = self._generate_password()
        lms_account.user.set_password(temp_password)
        lms_account.user.save(update_fields=['password'])

        logger.info(
            f"Password reset for {lms_account.learner.get_full_name()} "
            f"by {reset_by.username}"
        )
        return temp_password

    @transaction.atomic
    def deactivate_lms_account(
        self, lms_account: LearnerLMSAccount, deactivated_by
    ) -> LearnerLMSAccount:
        """
        Suspend the Moodle account and deactivate the Django User.
        Does not delete — the learner record and academic data are untouched.
        """
        if not lms_account.is_active:
            return lms_account

        # Suspend in Moodle
        self._suspend_moodle_account(lms_account.user)

        # Deactivate Django User
        lms_account.user.is_active = False
        lms_account.user.save(update_fields=['is_active'])

        lms_account.is_active = False
        lms_account.deactivated_at = timezone.now()
        lms_account.deactivated_by = deactivated_by
        lms_account.save(update_fields=['is_active', 'deactivated_at', 'deactivated_by'])

        logger.info(
            f"LMS account deactivated: {lms_account.learner.get_full_name()} "
            f"by {deactivated_by.username}"
        )
        return lms_account

    @transaction.atomic
    def reactivate_lms_account(
        self, lms_account: LearnerLMSAccount, reactivated_by
    ) -> LearnerLMSAccount:
        """Reactivate a previously deactivated account."""
        if lms_account.is_active:
            return lms_account

        lms_account.user.is_active = True
        lms_account.user.save(update_fields=['is_active'])

        # Unsuspend in Moodle
        self._unsuspend_moodle_account(lms_account.user)

        lms_account.is_active = True
        lms_account.deactivated_at = None
        lms_account.deactivated_by = None
        lms_account.save(update_fields=['is_active', 'deactivated_at', 'deactivated_by'])

        logger.info(
            f"LMS account reactivated: {lms_account.learner.get_full_name()} "
            f"by {reactivated_by.username}"
        )
        return lms_account

    def retry_moodle_sync(self, lms_account: LearnerLMSAccount) -> bool:
        """
        Retry Moodle sync for accounts that failed on creation.
        Returns True if sync succeeded.
        """
        return self._sync_user_to_moodle(lms_account.user)

    def generate_sso_url(self, lms_account: LearnerLMSAccount) -> str | None:
        """
        Generate a one-time Moodle SSO login URL for the learner.
        Returns None if Moodle is unavailable or user not synced.
        """
        if not lms_account.is_synced_to_moodle():
            return None

        try:
            from integration.models import MoodleInstance
            from integration.services import MoodleIntegrationService

            instance = MoodleInstance.objects.filter(
                is_active=True, is_primary=True
            ).first()
            if not instance:
                return None

            service = MoodleIntegrationService(instance)
            return service.generate_sso_url(lms_account.user)
        except Exception as exc:
            logger.error(f"SSO URL generation failed: {exc}")
            return None

    # ==================== PRIVATE HELPERS ====================

    def _generate_username(self, learner: Learner) -> str:
        """
        Generate a unique, Moodle-compatible username.
        Format: {first}.{last}.{admission_number}
        All lowercased, non-ASCII stripped, max 30 chars.
        """
        def clean(s: str) -> str:
            # Strip accents (e.g. é → e)
            s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()
            # Keep only alphanumeric
            return re.sub(r'[^a-z0-9]', '', s.lower())

        first = clean(learner.first_name)[:10]
        last = clean(learner.last_name)[:10]
        # Use admission number as unique suffix
        admission = clean(learner.admission_number or str(learner.pk))[:8]

        base = f"{first}.{last}.{admission}"[:30]

        # Resolve collisions by appending a counter
        username = base
        counter = 1
        while User.objects.filter(username=username).exists():
            suffix = str(counter)
            username = f"{base[:(30 - len(suffix) - 1)]}{suffix}"
            counter += 1

        return username

    def _generate_email(self, learner: Learner) -> str:
        """
        Generate a Moodle-valid placeholder email.
        Uses school email domain if available, otherwise scholr.local.
        """
        school = School.get_current()
        domain = 'noreply.scholr.local'

        if school and school.email:
            try:
                domain = school.email.split('@')[1]
            except IndexError:
                pass

        admission = re.sub(r'[^a-z0-9]', '', (learner.admission_number or '').lower())
        if not admission:
            admission = f"learner{learner.pk}"

        return f"{admission}@{domain}"

    def _generate_password(self) -> str:
        """
        Generate a readable temporary password.
        Format: Word#digits (e.g., Cedar#7291)
        Easy to read aloud to a learner without confusion.
        """
        words = [
            'Cedar', 'River', 'Stone', 'Eagle', 'Flame',
            'Tiger', 'Maple', 'Delta', 'Solar', 'Cloud',
            'Storm', 'Ridge', 'Frost', 'Amber', 'Brook',
        ]
        import random
        word = random.choice(words)
        digits = ''.join(random.choices(string.digits, k=4))
        return f"{word}#{digits}"

    def _sync_user_to_moodle(self, user) -> bool:
        """
        Sync a single user to Moodle. Returns True on success.
        Catches all exceptions — Moodle unavailability must not break account creation.
        """
        try:
            from integration.models import MoodleInstance
            from integration.services import MoodleIntegrationService

            instance = MoodleInstance.objects.filter(
                is_active=True, is_primary=True
            ).first()

            if not instance:
                logger.warning("No active Moodle instance — skipping Moodle sync")
                return False

            service = MoodleIntegrationService(instance)
            sync_log = service.sync_users(user_ids=[user.pk])

            return sync_log.successful_items > 0

        except Exception as exc:
            logger.error(f"Moodle user sync error for {user.username}: {exc}")
            return False

    def _suspend_moodle_account(self, user) -> None:
        """Suspend the Moodle account (does not delete)."""
        try:
            mapping = user.moodle_mapping
            if not mapping.moodle_user_id:
                return

            from integration.models import MoodleInstance
            from integration.moodle_client import MoodleClient

            instance = MoodleInstance.objects.filter(
                is_active=True, is_primary=True
            ).first()
            if not instance:
                return

            client = MoodleClient(instance.base_url, instance.ws_token)
            client.update_user(mapping.moodle_user_id, {'suspended': 1})

        except Exception as exc:
            logger.error(f"Failed to suspend Moodle account for {user.username}: {exc}")

    def _unsuspend_moodle_account(self, user) -> None:
        """Unsuspend a previously suspended Moodle account."""
        try:
            mapping = user.moodle_mapping
            if not mapping.moodle_user_id:
                return

            from integration.models import MoodleInstance
            from integration.moodle_client import MoodleClient

            instance = MoodleInstance.objects.filter(
                is_active=True, is_primary=True
            ).first()
            if not instance:
                return

            client = MoodleClient(instance.base_url, instance.ws_token)
            client.update_user(mapping.moodle_user_id, {'suspended': 0})

        except Exception as exc:
            logger.error(f"Failed to unsuspend Moodle account for {user.username}: {exc}")