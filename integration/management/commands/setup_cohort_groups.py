"""
Set up Moodle cohort groups for the METP courses.

    python manage.py setup_cohort_groups              # shows what would happen, changes nothing
    python manage.py setup_cohort_groups --apply      # creates and fills groups, then forces separate groups

Groups are filled first and the courses switched to separate groups last, so no participant
is ever left in a course with no group. Safe to run again at any time.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integration.cohort_groups import CohortGroupSync, desired_user_ids, online_cohorts, programme_courses
from integration.models import MoodleInstance
from scholr.features import cohort_groups_enabled


class Command(BaseCommand):
    help = 'Create one Moodle group per partner cohort in every METP course, then force separate groups.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Make the changes in Moodle.')
        parser.add_argument(
            '--skip-group-mode', action='store_true',
            help='Create and fill groups but leave the course group mode alone.',
        )

    def handle(self, *args, **options):
        if not MoodleInstance.objects.filter(is_active=True, is_primary=True).exists():
            raise CommandError('There is no active primary Moodle connection.')

        courses = list(programme_courses())
        cohorts = list(online_cohorts())
        if not courses:
            raise CommandError(
                'No METP course is linked to a Moodle course. Run seed_metp_programme --link-online first.'
            )

        self.stdout.write(f'{len(courses)} METP course(s): {", ".join(course.short_name for course in courses)}')
        self.stdout.write(f'{len(cohorts)} blended cohort(s):')
        for cohort in cohorts:
            self.stdout.write(f'  {cohort.name} at {cohort.location.name}: {len(desired_user_ids(cohort))} member(s)')

        if settings.MOODLE_COHORT_VIEWER_ROLE_ID:
            self.stdout.write(f'Cohort viewer role: {settings.MOODLE_COHORT_VIEWER_ROLE_ID}')
        else:
            self.stdout.write(self.style.WARNING(
                'MOODLE_COHORT_VIEWER_ROLE_ID is not set. Participants will be grouped, but coordinators '
                'and facilitators are left out and cannot use the LMS Portal until it is.'
            ))

        if not options['apply']:
            self.stdout.write(self.style.WARNING('Dry run: nothing was changed. Add --apply to set up the groups.'))
            return

        sync = CohortGroupSync()
        report = sync.sync(cohorts)
        self.stdout.write(self.style.SUCCESS(
            f"Groups: {report['created']} created; {report['added']} member(s) added, "
            f"{report['removed']} removed across {report['courses']} course(s)."
        ))
        if report['waiting']:
            self.stdout.write(
                f"{report['waiting']} place(s) are waiting for a Moodle enrolment to finish; "
                f'the next sync adds them.'
            )

        if options['skip_group_mode']:
            self.stdout.write('Group mode left unchanged.')
        else:
            switched = sync.apply_separate_groups()
            self.stdout.write(self.style.SUCCESS(
                f'Forced separate groups on {len(switched)} course(s): {", ".join(switched)}'
            ))

        if not cohort_groups_enabled():
            self.stdout.write(self.style.WARNING(
                'Set SCHOLR_COHORT_GROUPS_ENABLED=True in .env and restart, so groups keep themselves '
                'up to date and coordinators can use the LMS Portal.'
            ))
