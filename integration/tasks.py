import logging

from celery import shared_task
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
    from scholr.features import partners_enabled
    if not partners_enabled():
        return {'skipped': 'partners are off'}

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
