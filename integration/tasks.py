import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from scholr.features import cohort_groups_enabled
from .exceptions import MoodleConnectionError

logger = logging.getLogger(__name__)

# Saves inside this window share one queued run; the run starts once the window closes
COHORT_SYNC_DELAY_SECONDS = 30


@shared_task(bind=True, max_retries=3)
def sync_cohort_groups_task(self, cohort_ids=None):
    """Reconcile Moodle cohort groups. No IDs means every online cohort, as the nightly run does."""
    if not cohort_groups_enabled():
        return {'skipped': 'cohort groups are off'}

    from .cohort_groups import CohortGroupSync, online_cohorts

    cohorts = online_cohorts()
    if cohort_ids is not None:
        cohorts = cohorts.filter(pk__in=cohort_ids)
    try:
        return CohortGroupSync().sync(cohorts)
    except MoodleConnectionError as error:
        raise self.retry(exc=error, countdown=300)


def queue_cohort_sync(cohort_ids):
    """Queue a sync for these cohorts once the current transaction commits, at most once per window."""
    if not cohort_groups_enabled():
        return
    cohort_ids = sorted({pk for pk in cohort_ids if pk})
    if not cohort_ids:
        return

    def send():
        # I mark cohorts only after commit, so a rolled-back change never blocks a real one
        fresh = [pk for pk in cohort_ids if cache.add(f'cohort-group-sync:{pk}', 1, COHORT_SYNC_DELAY_SECONDS)]
        if not fresh:
            return
        try:
            sync_cohort_groups_task.apply_async(args=[fresh], countdown=COHORT_SYNC_DELAY_SECONDS)
        except Exception:
            # A lost run is not fatal; the nightly sync repairs it
            logger.exception('Could not queue cohort group sync for %s', fresh)

    transaction.on_commit(send)


# ==================== ONLINE QUIZ RESULTS ====================

ONLINE_RESULTS_REFRESH_SECONDS = 5 * 60


@shared_task(bind=True, max_retries=3)
def sync_online_results_task(self):
    """Pull quiz results for every METP course. Runs nightly and when someone asks for a refresh."""
    from scholr.features import online_results_enabled
    if not online_results_enabled():
        return {'skipped': 'online results are off'}

    from .online_results import OnlineResultSync
    try:
        return OnlineResultSync().sync()
    except MoodleConnectionError as error:
        raise self.retry(exc=error, countdown=300)


def queue_online_results_refresh():
    """
    Ask for fresh results now. Returns False when a refresh was already asked for in the
    last five minutes, so repeated clicks cost Moodle nothing.
    """
    if not cache.add('online-results-refresh', 1, ONLINE_RESULTS_REFRESH_SECONDS):
        return False
    try:
        sync_online_results_task.delay()
    except Exception:
        cache.delete('online-results-refresh')
        logger.exception('Could not queue an online results refresh')
        return False
    return True


def queue_moodle_language_update(user_id):
    """Send someone's new language to Moodle once the change is saved."""
    from django.db import transaction
    from scholr.features import multilingual_enabled

    if multilingual_enabled() and getattr(settings, 'SCHOLR_LMS_ENABLED', True):
        transaction.on_commit(lambda: update_moodle_language.delay(user_id))


@shared_task(bind=True, max_retries=3, default_retry_delay=120)
def update_moodle_language(self, user_id):
    """Set a person's Moodle language to the one they chose here. Nothing to do before they have an account."""
    from .languages import moodle_language
    from .models import MoodleUserMapping

    mapping = MoodleUserMapping.objects.filter(cas_user_id=user_id).select_related('cas_user').first()
    if not mapping or not mapping.moodle_user_id:
        return {'skipped': 'no LMS account yet'}
    language = moodle_language(mapping.cas_user)
    try:
        from .moodle_client import MoodleClient
        MoodleClient().update_user(mapping.moodle_user_id, {'lang': language})
    except Exception as exc:
        raise self.retry(exc=exc)
    return {'user': user_id, 'lang': language}


def staff_course_role(role):
    """The Moodle course role a Scholr role is given through the LMS Portal, or None."""
    from accounts.models import User

    if role in User.COHORT_VIEWER_ROLES:
        return settings.MOODLE_COHORT_VIEWER_ROLE_ID or None
    return settings.MOODLE_STAFF_COURSE_ROLES.get(role)


def queue_staff_lms_revocation(user_id, old_role, new_role):
    """After a role change that changes someone's Moodle course role, remove the old enrolments."""
    from scholr.features import staff_lms_portal_enabled

    old_course_role = staff_course_role(old_role)
    if not staff_lms_portal_enabled() or not old_course_role or old_course_role == staff_course_role(new_role):
        return
    transaction.on_commit(lambda: revoke_staff_lms_access.delay(user_id, old_role))


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def revoke_staff_lms_access(self, user_id, old_role):
    """
    Take away the Moodle teaching enrolments a previous role was given. The new role's access,
    if it has any, is granted the next time the person uses the LMS Portal.
    """
    from .models import Course, MoodleUserMapping
    from .moodle_client import MoodleClient
    from .staff_access import _access_cache_key

    mapping = MoodleUserMapping.objects.filter(cas_user_id=user_id).select_related('cas_user').first()
    old_course_role = staff_course_role(old_role)
    if not mapping or not mapping.moodle_user_id or not old_course_role:
        return {'skipped': 'nothing to remove'}

    client = MoodleClient()
    removed, failed = 0, []
    for course_id in Course.objects.filter(moodle_course_id__isnull=False).values_list('moodle_course_id', flat=True):
        try:
            client.unenrol_user(mapping.moodle_user_id, course_id, old_course_role)
            removed += 1
        except Exception:
            # Usually a course they were never enrolled in
            failed.append(course_id)
    cache.delete(_access_cache_key(mapping.cas_user))
    logger.info('Removed old staff LMS access for user %s from %s course(s)', user_id, removed)
    return {'user': user_id, 'removed': removed, 'not_enrolled': failed}
