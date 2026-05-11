"""
Celery tasks for the academics module.

Task schedule (defined in settings.CELERY_BEAT_SCHEDULE):
  04:00 — sync_online_learning_progress_task   (pull Moodle progress)
  06:00 — send_stall_notifications_task        (email stall alerts)

The 2-hour gap ensures progress data is fresh before alerts fire.
Both tasks are no-ops when the LMS Bridge feature flag is off.
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def sync_online_learning_progress_task(self):
    """
    Pull Moodle progress for every learner with an active online enrollment.
    Groups enrollments by learner to minimise Moodle API round-trips.
    Runs nightly at 04:00 via Celery Beat.
    """
    from scholr.features import bridge_enabled

    if not bridge_enabled():
        logger.info('sync_online_learning_progress_task: bridge disabled, skipping.')
        return {'skipped': True}

    from academics.models.lms_link import OnlineEnrollment
    from academics.models.learner import Learner
    from academics.services.lms_bridge import LMSBridgeService

    learner_ids = list(
        OnlineEnrollment.objects.filter(
            status=OnlineEnrollment.STATUS_ACTIVE,
        ).values_list('learner_id', flat=True).distinct()
    )

    service = LMSBridgeService()
    results = {
        'total_learners': len(learner_ids),
        'synced':         0,
        'failed':         0,
    }

    for learner_id in learner_ids:
        try:
            learner = Learner.objects.get(pk=learner_id)
            result = service.sync_progress_for_learner(learner)
            if result.get('success'):
                results['synced'] += 1
            else:
                results['failed'] += 1
                logger.warning(
                    f'Progress sync returned non-success for learner '
                    f'{learner_id}: {result.get("error")}'
                )
        except Learner.DoesNotExist:
            logger.error(f'Learner {learner_id} not found during progress sync.')
            results['failed'] += 1
        except Exception as exc:
            logger.error(f'Progress sync failed for learner {learner_id}: {exc}')
            results['failed'] += 1
            # Retry the whole task on unexpected errors (up to max_retries)
            try:
                raise self.retry(exc=exc)
            except self.MaxRetriesExceededError:
                pass

    logger.info(
        f'sync_online_learning_progress_task complete: '
        f'{results["synced"]} synced, {results["failed"]} failed '
        f'of {results["total_learners"]} learners.'
    )
    return results


@shared_task(bind=True, max_retries=2, default_retry_delay=600)
def send_stall_notifications_task(self):
    """
    Detect online course progress that has stalled or never started,
    and email the relevant homeroom teachers.

    Runs nightly at 06:00 via Celery Beat — after the sync task at 04:00
    so the progress data it reads is as fresh as possible.

    Thresholds (overridable via .env):
      PROGRESS_STALL_DAYS=7         — days without movement to count as stalled
      PROGRESS_NOT_STARTED_DAYS=3   — days after enrollment before alerting
      PROGRESS_ALERT_COOLDOWN_DAYS=7 — minimum gap between alerts per learner+course
    """
    from scholr.features import bridge_enabled

    if not bridge_enabled():
        logger.info('send_stall_notifications_task: bridge disabled, skipping.')
        return {'skipped': True}

    try:
        from academics.emails import ProgressStallEmailService
        service = ProgressStallEmailService()
        result = service.detect_and_send()
    except Exception as exc:
        logger.error(f'send_stall_notifications_task failed: {exc}')
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {'error': str(exc)}

    logger.info(
        f'send_stall_notifications_task complete: '
        f'{result["stalled"]} stalled, '
        f'{result["not_started"]} not started, '
        f'{result["emails_sent"]} emails sent, '
        f'{result["skipped_no_recipients"]} skipped (no recipients).'
    )
    return result