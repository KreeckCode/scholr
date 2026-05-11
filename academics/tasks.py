"""
Celery tasks for the academics module.

Beat schedule (configured in settings.CELERY_BEAT_SCHEDULE):
  04:00 — sync_online_learning_progress_task   pull Moodle progress
  06:00 — send_stall_notifications_task        email stall alerts

Bulk tasks fire on demand from the bulk operation views:
  bulk_create_lms_accounts_task
  bulk_enroll_in_course_task
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


# ==================== NIGHTLY SYNC ====================

@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def sync_online_learning_progress_task(self):
    """
    Pull Moodle progress for every learner with an active online enrollment.
    Runs at 04:00 via Beat, before the stall notification task.

    After all learners are synced, auto-marks any enrollment that reached
    100% as STATUS_COMPLETED — a single bulk UPDATE rather than per-row logic.
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
        'auto_completed': 0,
    }

    for learner_id in learner_ids:
        try:
            learner = Learner.objects.get(pk=learner_id)
            result  = service.sync_progress_for_learner(learner)
            if result.get('success'):
                results['synced'] += 1
            else:
                results['failed'] += 1
                logger.warning(
                    f'Progress sync returned non-success for learner '
                    f'{learner_id}: {result.get("error")}'
                )
        except Learner.DoesNotExist:
            logger.error(f'Progress sync: learner {learner_id} not found.')
            results['failed'] += 1
        except Exception as exc:
            logger.error(f'Progress sync failed for learner {learner_id}: {exc}')
            results['failed'] += 1
            try:
                raise self.retry(exc=exc)
            except self.MaxRetriesExceededError:
                pass

    # Auto-completion: any active enrollment sitting at 100% is complete.
    # Single bulk UPDATE — no per-row overhead.
    completed = OnlineEnrollment.objects.filter(
        status=OnlineEnrollment.STATUS_ACTIVE,
        progress_percentage__gte=100,
    ).update(status=OnlineEnrollment.STATUS_COMPLETED)
    results['auto_completed'] = completed

    if completed:
        logger.info(f'Auto-completed {completed} online enrollment(s).')

    logger.info(
        f'sync_online_learning_progress_task complete: '
        f'{results["synced"]} synced, {results["failed"]} failed, '
        f'{results["auto_completed"]} auto-completed '
        f'of {results["total_learners"]} learners.'
    )
    return results


# ==================== STALL NOTIFICATIONS ====================

@shared_task(bind=True, max_retries=2, default_retry_delay=600)
def send_stall_notifications_task(self):
    """
    Detect stalled online course progress and email relevant staff.
    Runs at 06:00 via Beat — 2 hours after the sync task so data is fresh.

    Thresholds (override via .env):
      PROGRESS_STALL_DAYS=7
      PROGRESS_NOT_STARTED_DAYS=3
      PROGRESS_ALERT_COOLDOWN_DAYS=7
    """
    from scholr.features import bridge_enabled

    if not bridge_enabled():
        logger.info('send_stall_notifications_task: bridge disabled, skipping.')
        return {'skipped': True}

    try:
        from academics.emails import ProgressStallEmailService
        result = ProgressStallEmailService().detect_and_send()
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


# ==================== BULK OPERATIONS ====================

@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def bulk_create_lms_accounts_task(self, learner_ids, created_by_id):
    """
    Create LMS accounts for a batch of learners.

    Fired by BulkCreateAccountsView. Skips learners who already have accounts.
    Credentials are not returned — learners access Moodle via SSO and are
    prompted by Moodle to set a permanent password on first login.
    """
    from scholr.features import bridge_enabled

    if not bridge_enabled():
        return {'skipped': True}

    from accounts.models import User
    from academics.models.learner import Learner
    from academics.services.lms_bridge import LMSBridgeService

    try:
        created_by = User.objects.get(pk=created_by_id)
    except User.DoesNotExist:
        logger.error(
            f'bulk_create_lms_accounts_task: '
            f'admin user {created_by_id} not found.'
        )
        return {'error': f'Admin user {created_by_id} not found.'}

    service = LMSBridgeService()
    results = {
        'total':           len(learner_ids),
        'created':         0,
        'already_existed': 0,
        'failed':          0,
    }

    for learner_id in learner_ids:
        try:
            learner = Learner.objects.get(pk=learner_id, is_active=True)
            result  = service.create_lms_account(
                learner=learner,
                created_by=created_by,
                notes='Bulk account creation',
            )
            if result.is_new_account:
                results['created'] += 1
            else:
                results['already_existed'] += 1
        except Learner.DoesNotExist:
            logger.warning(
                f'bulk_create_lms_accounts_task: learner {learner_id} not found.'
            )
            results['failed'] += 1
        except Exception as exc:
            logger.error(
                f'bulk_create_lms_accounts_task: '
                f'account creation failed for learner {learner_id}: {exc}'
            )
            results['failed'] += 1

    logger.info(
        f'bulk_create_lms_accounts_task complete: '
        f'{results["created"]} created, '
        f'{results["already_existed"]} already existed, '
        f'{results["failed"]} failed '
        f'of {results["total"]} learners.'
    )
    return results


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def bulk_enroll_in_course_task(
    self, learner_ids, course_id, enrolled_by_id, academic_year_id,
):
    """
    Enrol a batch of learners in a Moodle course.

    Fired by BulkEnrollView. Per-learner skips:
      - No LMS account or account not synced to Moodle
      - Already enrolled (enroll_in_course raises ValueError)

    The task never aborts on a single failure — it logs and continues
    so one bad record doesn't block the rest of the cohort.
    """
    from scholr.features import bridge_enabled

    if not bridge_enabled():
        return {'skipped': True}

    from accounts.models import User
    from academics.models.learner import Learner
    from academics.models.lms_link import LearnerLMSAccount
    from academics.models.school import AcademicYear
    from academics.services.lms_bridge import LMSBridgeService
    from integration.models import Course as LMSCourse

    # Resolve all FK objects up-front; fail fast if any are missing
    try:
        enrolled_by   = User.objects.get(pk=enrolled_by_id)
        course        = LMSCourse.objects.get(pk=course_id)
        academic_year = AcademicYear.objects.get(pk=academic_year_id)
    except Exception as exc:
        logger.error(f'bulk_enroll_in_course_task: setup failed: {exc}')
        return {'error': str(exc)}

    service = LMSBridgeService()
    results = {
        'total':             len(learner_ids),
        'enrolled':          0,
        'already_enrolled':  0,
        'skipped_no_account': 0,
        'failed':            0,
    }

    for learner_id in learner_ids:
        try:
            learner = Learner.objects.get(pk=learner_id, is_active=True)
        except Learner.DoesNotExist:
            logger.warning(
                f'bulk_enroll_in_course_task: learner {learner_id} not found.'
            )
            results['failed'] += 1
            continue

        # Gate: LMS account must exist and be Moodle-synced
        try:
            lms_account = learner.lms_account
            if not lms_account.is_active or not lms_account.is_synced_to_moodle():
                results['skipped_no_account'] += 1
                continue
        except LearnerLMSAccount.DoesNotExist:
            results['skipped_no_account'] += 1
            continue

        try:
            service.enroll_in_course(
                learner=learner,
                course=course,
                enrolled_by=enrolled_by,
                reason='Bulk enrollment',
                academic_year=academic_year,
            )
            results['enrolled'] += 1
        except ValueError:
            # enroll_in_course raises ValueError when already enrolled
            results['already_enrolled'] += 1
        except Exception as exc:
            logger.error(
                f'bulk_enroll_in_course_task: '
                f'enrolment failed for learner {learner_id}: {exc}'
            )
            results['failed'] += 1

    logger.info(
        f'bulk_enroll_in_course_task [{course.title}]: '
        f'{results["enrolled"]} enrolled, '
        f'{results["already_enrolled"]} already enrolled, '
        f'{results["skipped_no_account"]} no account, '
        f'{results["failed"]} failed '
        f'of {results["total"]} learners.'
    )
    return results