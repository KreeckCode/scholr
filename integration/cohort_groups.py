"""
One Moodle group per partner cohort, in every METP course.

With the courses in forced separate-groups mode, participants only see their own cohort.
Coordinators and facilitators join the groups of the cohorts they look after, under the
Cohort viewer role, so they see those cohorts and nobody else.

Every run works out who should be in each group and changes only the difference, so it is
safe to repeat: after access is granted, after a facilitator is assigned, and every night.
Only groups this module created are touched.
"""
import logging

from django.conf import settings

from .exceptions import MoodleAPIError
from .models import Course, MoodleCohortGroup, MoodleUserMapping
from .services import MoodleIntegrationService

logger = logging.getLogger(__name__)

GROUP_IDNUMBER_PREFIX = 'scholr-cohort-'


def programme_courses():
    """The online courses behind METP 101 to 112 that already exist in Moodle."""
    from partners.models import ProgrammeCourse

    linked = ProgrammeCourse.objects.filter(online_course__isnull=False).values('online_course_id')
    return Course.objects.filter(id__in=linked, moodle_course_id__isnull=False).order_by('short_name')


def online_cohorts():
    """Blended cohorts are the only ones whose participants can be given online access."""
    from partners.models import Cohort

    return Cohort.objects.filter(delivery=Cohort.Delivery.BLENDED).select_related('location')


def staff_cohorts(user):
    """
    Online cohorts someone looks after: by the portal's rules for coordinators and facilitators,
    by network reach for hub administrators and ambassadors.
    """
    from accounts.models import User
    from partners.services import scope

    if scope.network_role(user) in User.NETWORK_SCOPED_ROLES:
        cohorts = scope.cohorts_in_reach(user)
    else:
        cohorts = scope.cohorts_for(user)
    return online_cohorts().filter(pk__in=cohorts.values('pk'))


def participant_cohorts(user):
    """Online cohorts where this person is a participant with live online access."""
    from partners.models import CohortMembership

    held = CohortMembership.objects.filter(
        participant__user=user, access_order__isnull=False, access_withdrawn_at__isnull=True,
    ).values('cohort_id')
    return online_cohorts().filter(pk__in=held)


def group_idnumber(cohort):
    return f'{GROUP_IDNUMBER_PREFIX}{cohort.public_id}'


def group_name(cohort):
    return f'{cohort.name} ({cohort.location.name})'[:254]


def desired_user_ids(cohort):
    """Scholr user IDs that belong in this cohort's group."""
    from partners.models import CohortMembership, LocationMembership

    wanted = set(
        CohortMembership.objects.filter(
            cohort=cohort, access_order__isnull=False, access_withdrawn_at__isnull=True,
            participant__user__isnull=False,
        ).values_list('participant__user_id', flat=True)
    )

    # Staff are only grouped once the Cohort viewer role exists, so nobody enters with wider access
    if settings.MOODLE_COHORT_VIEWER_ROLE_ID:
        active_at_location = LocationMembership.objects.filter(
            location_id=cohort.location_id, is_active=True, user__is_active=True,
        )
        wanted |= set(
            active_at_location.filter(role=LocationMembership.Role.COORDINATOR).values_list('user_id', flat=True)
        )
        wanted |= set(
            cohort.facilitators.filter(
                is_active=True, pk__in=active_at_location.values('user_id'),
            ).values_list('pk', flat=True)
        )
        wanted |= set(reach_staff_ids(cohort))
    return wanted


def reach_staff_ids(cohort):
    """Hub administrators and ambassadors whose reach includes this cohort's partner."""
    from django.db.models import Q
    from accounts.models import User
    from partners.models import TrainingLocation

    organisation_id, country = TrainingLocation.objects.filter(pk=cohort.location_id).values_list(
        'organisation_id', 'organisation__country',
    ).get()
    return User.objects.filter(
        Q(role__in=User.NETWORK_SCOPED_ROLES, network_assignments__organisation_id=organisation_id)
        | Q(role=User.HUB_ADMIN, network_assignments__country=country),
        is_active=True,
    ).distinct().values_list('pk', flat=True)


class CohortGroupSync:

    def __init__(self, service=None):
        self.service = service or MoodleIntegrationService()
        self.client = self.service.client

    # ==================== FULL RECONCILE ====================

    def sync(self, cohorts=None):
        """
        Bring the given cohorts' groups in line, in every METP course.
        Returns counts: groups created, members added and removed, and people still
        waiting for their Moodle enrolment to finish (picked up on a later run).
        """
        cohorts = list(online_cohorts() if cohorts is None else cohorts)
        report = {
            'cohorts': len(cohorts), 'courses': 0, 'created': 0, 'added': 0, 'removed': 0,
            'waiting': 0, 'no_account': 0,
        }
        if not cohorts:
            return report

        desired, without_account = {}, set()
        for cohort in cohorts:
            user_ids = desired_user_ids(cohort)
            mapped = dict(
                MoodleUserMapping.objects.filter(cas_user_id__in=user_ids, moodle_user_id__isnull=False)
                .values_list('cas_user_id', 'moodle_user_id')
            )
            desired[cohort.pk] = set(mapped.values())
            # Usually coordinators and facilitators who have not opened the LMS Portal yet
            without_account |= set(user_ids) - set(mapped)
        report['no_account'] = len(without_account)

        for course in programme_courses():
            report['courses'] += 1
            groups, created = self._ensure_groups(course, cohorts)
            report['created'] += created

            enrolled = self.client.get_enrolled_user_ids(course.moodle_course_id)
            current = self.client.get_group_member_ids(list(groups.values()))

            to_add, to_remove = [], []
            for cohort_pk, group_id in groups.items():
                wanted = desired[cohort_pk]
                have = current.get(group_id, set())
                # Moodle refuses to group someone who is not enrolled yet
                to_add += [{'groupid': group_id, 'userid': uid} for uid in sorted((wanted & enrolled) - have)]
                to_remove += [{'groupid': group_id, 'userid': uid} for uid in sorted(have - wanted)]
                report['waiting'] += len(wanted - enrolled)

            self.client.add_group_members(to_add)
            self.client.delete_group_members(to_remove)
            report['added'] += len(to_add)
            report['removed'] += len(to_remove)

        logger.info('Cohort groups synced: %s', report)
        return report

    def _ensure_groups(self, course, cohorts):
        """Return ({cohort.pk: moodle_group_id}, number_created), creating missing groups."""
        stored = {
            row.cohort_id: row.moodle_group_id
            for row in MoodleCohortGroup.objects.filter(course=course, cohort__in=cohorts)
        }
        in_moodle = self.client.get_course_groups(course.moodle_course_id)
        existing_ids = {group['id'] for group in in_moodle}
        by_idnumber = {group.get('idnumber'): group['id'] for group in in_moodle if group.get('idnumber')}

        groups, missing = {}, []
        for cohort in cohorts:
            group_id = stored.get(cohort.pk)
            if group_id not in existing_ids:
                # The stored group was deleted in Moodle, or never stored; fall back to the idnumber
                group_id = by_idnumber.get(group_idnumber(cohort))
            if group_id:
                groups[cohort.pk] = group_id
                if stored.get(cohort.pk) != group_id:
                    self._remember(cohort, course, group_id)
            else:
                missing.append(cohort)

        for cohort, group_id in self._create(course, missing):
            groups[cohort.pk] = group_id
            self._remember(cohort, course, group_id)
        return groups, len(missing)

    def _create(self, course, cohorts):
        if not cohorts:
            return []
        specs = [self._spec(course, cohort) for cohort in cohorts]
        try:
            created = self.client.create_groups(specs)
            by_idnumber = {group['idnumber']: group['id'] for group in created}
            return [(cohort, by_idnumber[group_idnumber(cohort)]) for cohort in cohorts]
        except (MoodleAPIError, KeyError) as error:
            logger.warning('Batch group creation failed in %s, creating one by one: %s', course.short_name, error)

        results = []
        for cohort in cohorts:
            spec = self._spec(course, cohort)
            try:
                created = self.client.create_groups([spec])
            except MoodleAPIError:
                # Group names must be unique in a course, so I add the cohort's short ID and retry
                spec['name'] = f"{spec['name'][:240]} [{str(cohort.public_id)[:8]}]"
                created = self.client.create_groups([spec])
            results.append((cohort, created[0]['id']))
        return results

    def _spec(self, course, cohort):
        return {
            'courseid': course.moodle_course_id,
            'name': group_name(cohort),
            'idnumber': group_idnumber(cohort),
            'description': f'Managed by Scholr. Cohort at {cohort.location.name}; do not edit members by hand.',
        }

    def _remember(self, cohort, course, group_id):
        MoodleCohortGroup.objects.update_or_create(
            cohort=cohort, course=course, defaults={'moodle_group_id': group_id},
        )

    # ==================== ONE PERSON ====================

    def add_member_to_groups(self, moodle_user_id, cohorts):
        """
        Put one already-enrolled person straight into their cohorts' groups, in one request.
        Returns the cohorts that have no group yet, so the caller can queue a full sync for them.
        """
        cohorts = list(cohorts)
        rows = MoodleCohortGroup.objects.filter(
            cohort__in=cohorts, course__in=programme_courses(),
        ).values_list('cohort_id', 'moodle_group_id')
        members, ready = [], set()
        for cohort_id, group_id in rows:
            members.append({'groupid': group_id, 'userid': moodle_user_id})
            ready.add(cohort_id)
        self.client.add_group_members(members)
        return [cohort for cohort in cohorts if cohort.pk not in ready]

    # ==================== COURSE SETTINGS ====================

    def apply_separate_groups(self):
        """Switch every METP course to forced separate groups. Run after groups are filled."""
        done = []
        for course in programme_courses():
            self.client.set_separate_groups(course.moodle_course_id)
            done.append(course.short_name)
        return done
