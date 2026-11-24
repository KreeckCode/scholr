"""
Programme results and online quiz results, side by side.

Programme results are the classroom outcomes coordinators record and sign off. Online quiz
results come from Moodle; this module only reads what integration has already stored, so
the partners app still never talks to Moodle itself.
"""
from django.db.models import Max

from integration.models import OnlineQuizResult
from partners.models import CohortMembership, CourseResult, ProgrammeCourse

from . import scope

PROGRAMME = 'programme'
ONLINE = 'online'
VIEWS = (PROGRAMME, ONLINE)


def active_courses():
    return list(ProgrammeCourse.objects.filter(is_active=True).order_by('sequence'))


def online_results(memberships, courses):
    """{(membership.pk, course.pk): OnlineQuizResult} for participants who study online."""
    users = {m.pk: m.participant.user_id for m in memberships if m.participant.user_id}
    linked = {c.pk: c.online_course_id for c in courses if c.online_course_id}
    if not users or not linked:
        return {}
    stored = {
        (row.user_id, row.course_id): row
        for row in OnlineQuizResult.objects.filter(
            user_id__in=set(users.values()), course_id__in=set(linked.values()),
        )
    }
    found = {}
    for membership_pk, user_id in users.items():
        for course_pk, online_id in linked.items():
            row = stored.get((user_id, online_id))
            if row is not None:
                found[(membership_pk, course_pk)] = row
    return found


def programme_results(memberships, courses):
    """{(membership.pk, course.pk): CourseResult}."""
    return {
        (row.membership_id, row.course_id): row
        for row in CourseResult.objects.filter(membership__in=memberships, course__in=courses)
    }


def last_synced():
    return OnlineQuizResult.objects.aggregate(latest=Max('synced_at'))['latest']


def visible_memberships(user, country='', organisation='', location='', cohort=''):
    """Participants whose results this user may see, narrowed by the filters given."""
    memberships = (
        CohortMembership.objects
        .filter(cohort__in=scope.cohorts_for(user))
        .exclude(status=CohortMembership.Status.WITHDRAWN)
        .select_related('participant', 'cohort__location__organisation')
    )
    if country:
        memberships = memberships.filter(cohort__location__organisation__country=country)
    if organisation:
        memberships = memberships.filter(cohort__location__organisation__public_id=organisation)
    if location:
        memberships = memberships.filter(cohort__location__public_id=location)
    if cohort:
        memberships = memberships.filter(cohort__public_id=cohort)
    return memberships.order_by(
        'cohort__location__organisation__name', 'cohort__name',
        'participant__last_name', 'participant__first_name',
    )


def grid(memberships, courses):
    """Rows of cells, one per course, ready for the results grid template."""
    memberships = list(memberships)
    programme = programme_results(memberships, courses)
    online = online_results(memberships, courses)
    rows = []
    for membership in memberships:
        cells = [
            {
                'course': course,
                'programme': programme.get((membership.pk, course.pk)),
                'online': online.get((membership.pk, course.pk)),
            }
            for course in courses
        ]
        rows.append({'membership': membership, 'cells': cells})
    return rows


def course_summaries(rows, courses, view):
    """Passed counts per course column, for the grid footer."""
    summaries = []
    for index, course in enumerate(courses):
        if view == ONLINE:
            passed = sum(1 for row in rows if row['cells'][index]['online'] and row['cells'][index]['online'].passed)
        else:
            passed = sum(
                1 for row in rows
                if row['cells'][index]['programme']
                and row['cells'][index]['programme'].status == CourseResult.Status.PASSED
            )
        summaries.append({'course': course, 'passed': passed})
    return summaries
