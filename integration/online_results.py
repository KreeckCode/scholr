"""
Pull quiz results from the Moodle gradebook for every learner in the METP courses.

One request per course returns everyone, so a full refresh of all twelve courses is twelve
requests however many participants there are. Only quizzes count: METP courses have no
hand-marked work, and the course total is not used (see OnlineQuizResult).
"""
import logging
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from .cohort_groups import programme_courses
from .models import MoodleUserMapping, OnlineQuizResult
from .services import MoodleIntegrationService

logger = logging.getLogger(__name__)


def _decimal(value):
    if value is None or value == '':
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def summarise(grade_items):
    """Turn one learner's gradebook rows into quiz totals. Unattempted quizzes have no raw grade."""
    quizzes = [item for item in grade_items if item.get('itemtype') == 'mod' and item.get('itemmodule') == 'quiz']
    summary = {
        'quizzes_total': 0, 'quizzes_attempted': 0, 'points': Decimal('0'),
        'max_points_attempted': Decimal('0'), 'max_points_total': Decimal('0'), 'items': [],
    }
    for quiz in quizzes:
        maximum = _decimal(quiz.get('grademax'))
        if not maximum:
            continue
        score = _decimal(quiz.get('graderaw'))
        summary['quizzes_total'] += 1
        summary['max_points_total'] += maximum
        if score is not None:
            summary['quizzes_attempted'] += 1
            summary['points'] += score
            summary['max_points_attempted'] += maximum
        summary['items'].append({
            'name': quiz.get('itemname') or 'Quiz',
            'score': str(score) if score is not None else None,
            'max': str(maximum),
        })
    return summary


class OnlineResultSync:

    def __init__(self, service=None):
        self.client = (service or MoodleIntegrationService()).client

    def sync(self, courses=None):
        """Refresh every learner's quiz results. Returns counts per run."""
        courses = list(programme_courses() if courses is None else courses)
        users_by_moodle_id = dict(
            MoodleUserMapping.objects.filter(moodle_user_id__isnull=False)
            .values_list('moodle_user_id', 'cas_user_id')
        )
        report = {'courses': 0, 'saved': 0, 'unknown': 0}
        now = timezone.now()

        for course in courses:
            report['courses'] += 1
            for row in self.client.get_course_grade_items(course.moodle_course_id):
                user_id = users_by_moodle_id.get(row.get('userid'))
                if user_id is None:
                    # Someone in Moodle that Scholr does not know, such as a Moodle-only admin
                    report['unknown'] += 1
                    continue
                summary = summarise(row.get('gradeitems') or [])
                OnlineQuizResult.objects.update_or_create(
                    user_id=user_id, course=course, defaults={**summary, 'synced_at': now},
                )
                report['saved'] += 1

        logger.info('Online quiz results synced: %s', report)
        return report
