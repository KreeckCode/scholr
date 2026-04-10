"""
Management command to reconcile Moodle sync status
Verifies that courses marked as synced actually exist in Moodle
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from integration.models import Course, MoodleInstance
from integration.moodle_client import MoodleClient


class Command(BaseCommand):
    help = 'Reconcile course sync status with actual Moodle state'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Actually fix mismatches (default is dry-run)',
        )
    
    def handle(self, *args, **options):
        dry_run = not options['fix']
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING('DRY RUN MODE - no changes will be made')
            )
            self.stdout.write(
                self.style.WARNING('Use --fix to actually fix issues\n')
            )
        
        # Get Moodle instance
        moodle_instance = MoodleInstance.objects.filter(
            is_active=True,
            is_primary=True
        ).first()
        
        if not moodle_instance:
            self.stdout.write(self.style.ERROR('No active Moodle instance found'))
            return
        
        # Initialize client
        client = MoodleClient(
            base_url=moodle_instance.base_url,
            token=moodle_instance.ws_token
        )
        
        self.stdout.write('Fetching all Moodle courses...')
        moodle_courses = {c['shortname']: c for c in client.get_courses()}
        self.stdout.write(f'Found {len(moodle_courses)} courses in Moodle\n')
        
        # Check all courses
        courses = Course.objects.all()
        
        stats = {
            'verified': 0,
            'reconciled': 0,
            'stale_ids': 0,
            'missing': 0,
            'fixed': 0
        }
        
        for course in courses:
            status = self._check_course(course, moodle_courses, client, dry_run)
            stats[status] += 1
        
        # Print summary
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS('RECONCILIATION SUMMARY'))
        self.stdout.write('='*60)
        self.stdout.write(f'✓ Verified (already correct): {stats["verified"]}')
        self.stdout.write(
            self.style.SUCCESS(f'✓ Reconciled (found & linked): {stats["reconciled"]}')
        )
        self.stdout.write(
            self.style.WARNING(f'⚠ Stale IDs (cleared): {stats["stale_ids"]}')
        )
        self.stdout.write(
            self.style.ERROR(f'✗ Missing (not in Moodle): {stats["missing"]}')
        )
        
        if dry_run:
            self.stdout.write('\n' + self.style.WARNING(
                f'Would have fixed {stats["reconciled"] + stats["stale_ids"]} issues'
            ))
            self.stdout.write(self.style.WARNING('Run with --fix to apply changes'))
        else:
            self.stdout.write('\n' + self.style.SUCCESS(
                f'Fixed {stats["reconciled"] + stats["stale_ids"]} issues'
            ))
    
    def _check_course(self, course, moodle_courses, client, dry_run):
        """Check a single course and return status"""
        
        # Check if course has moodle_course_id
        if course.moodle_course_id:
            # Verify it exists
            existing = client.get_course_by_id(course.moodle_course_id)
            
            if existing:
                # ID is correct
                self.stdout.write(
                    f'  ✓ {course.short_name}: ID {course.moodle_course_id} verified'
                )
                return 'verified'
            else:
                # ID is stale
                self.stdout.write(
                    self.style.WARNING(
                        f'  ⚠ {course.short_name}: ID {course.moodle_course_id} '
                        f'is stale (course deleted in Moodle?)'
                    )
                )
                
                # Try to find by shortname
                if course.short_name in moodle_courses:
                    new_id = moodle_courses[course.short_name]['id']
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'    → Found by shortname, new ID: {new_id}'
                        )
                    )
                    
                    if not dry_run:
                        course.moodle_course_id = new_id
                        course.sync_status = 'synced'
                        course.last_synced = timezone.now()
                        course.save()
                    
                    return 'reconciled'
                else:
                    # Clear stale ID
                    if not dry_run:
                        course.moodle_course_id = None
                        course.sync_status = 'pending'
                        course.save()
                    
                    return 'stale_ids'
        
        else:
            # No moodle_course_id, check if it exists by shortname
            if course.short_name in moodle_courses:
                moodle_id = moodle_courses[course.short_name]['id']
                self.stdout.write(
                    self.style.SUCCESS(
                        f'  ✓ {course.short_name}: Found in Moodle (ID: {moodle_id}), '
                        f'linking...'
                    )
                )
                
                if not dry_run:
                    course.moodle_course_id = moodle_id
                    course.sync_status = 'synced'
                    course.last_synced = timezone.now()
                    course.save()
                
                return 'reconciled'
            else:
                # Course doesn't exist in Moodle
                self.stdout.write(
                    self.style.ERROR(
                        f'  ✗ {course.short_name}: Not found in Moodle '
                        f'(needs sync)'
                    )
                )
                
                if not dry_run and course.sync_status == 'synced':
                    course.sync_status = 'pending'
                    course.save()
                
                return 'missing'