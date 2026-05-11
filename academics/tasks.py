"""
Celery tasks for the academics module.

Separated from shop/tasks.py because academics tasks are SMS-specific
and should not run on LMS-only deployments where academics is disabled.
"""

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@shared_task
def sync_online_learning_progress_task():
    """
    Pull Moodle progress for all active online enrollments and cache it
    in OnlineEnrollment.progress_percentage.

    Scheduled daily at 4 AM via Celery Beat.
    Groups by learner to avoid duplicate API calls when a learner has
    multiple active enrollments — one MoodleProgressService call per
    learner covers all their courses.
    """
    from academics.models.lms_link import OnlineEnrollment
    from academics.models.school import AcademicYear
    from academics.services.lms_bridge import LMSBridgeService
    from academics.models.learner import Learner

    current_year = AcademicYear.get_current()
    if not current_year:
        logger.warning('No current academic year — skipping online learning progress sync')
        return {'skipped': True, 'reason': 'No current year'}

    # Get distinct learner IDs with at least one active online enrollment
    learner_ids = (
        OnlineEnrollment.objects
        .filter(
            status=OnlineEnrollment.STATUS_ACTIVE,
            academic_year=current_year,
            course_enrollment__isnull=False,
        )
        .values_list('learner_id', flat=True)
        .distinct()
    )

    if not learner_ids:
        logger.info('No active online enrollments to sync')
        return {'learners_processed': 0, 'enrollments_updated': 0, 'errors': 0}

    service = LMSBridgeService()
    total_updated = 0
    total_errors = 0

    for learner in Learner.objects.filter(pk__in=learner_ids, is_active=True):
        try:
            result = service.sync_progress_for_learner(learner)
            if result.get('success'):
                total_updated += result.get('online_enrollments_updated', 0)
                logger.info(
                    f'Synced online progress for {learner.get_full_name()}: '
                    f'{result.get("online_enrollments_updated", 0)} courses updated'
                )
            else:
                total_errors += 1
                logger.warning(
                    f'Progress sync skipped for {learner.get_full_name()}: '
                    f'{result.get("error")}'
                )
        except Exception as exc:
            total_errors += 1
            logger.error(f'Progress sync error for {learner}: {exc}')

    logger.info(
        f'Online learning progress sync complete: '
        f'{len(learner_ids)} learners, {total_updated} enrollments updated, '
        f'{total_errors} errors'
    )
    return {
        'learners_processed': len(learner_ids),
        'enrollments_updated': total_updated,
        'errors': total_errors,
    }