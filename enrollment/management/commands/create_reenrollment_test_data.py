"""
Management command to create test data for re-enrollment portal.
Usage: python manage.py create_reenrollment_test_data
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from academics.models.school import AcademicYear
from academics.models.structure import Grade
from academics.models.learner import Learner, Guardian, LearnerGuardian, LearnerEnrollment
from enrollment.models import Reenrollment
from enrollment.services.reenrollment_service import ReenrollmentService


class Command(BaseCommand):
    help = 'Create test data for re-enrollment portal testing'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete existing test data before creating new',
        )

    def handle(self, *args, **options):
        if options['reset']:
            self.stdout.write('Cleaning up old test data...')
            # Delete in correct order to avoid foreign key constraints
            from enrollment.models import Reenrollment
            Reenrollment.objects.filter(learner__admission_number__startswith='TEST').delete()
            LearnerEnrollment.objects.filter(learner__admission_number__startswith='TEST').delete()
            LearnerGuardian.objects.filter(learner__admission_number__startswith='TEST').delete()
            Learner.objects.filter(admission_number__startswith='TEST').delete()
            Guardian.objects.filter(email='parent@test.com').delete()
            self.stdout.write(self.style.SUCCESS('✓ Cleaned up old test data'))

        self.stdout.write('Creating test data for re-enrollment portal...\n')

        # 1. Create academic years
        year_2025, created = AcademicYear.objects.get_or_create(
            year=2025,
            defaults={
                'start_date': timezone.now().date(),
                'end_date': timezone.now().date() + timedelta(days=365),
                'is_current': True,
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'✓ Created academic year 2025'))
        else:
            self.stdout.write(f'  Academic year 2025 already exists')

        year_2026, created = AcademicYear.objects.get_or_create(
            year=2026,
            defaults={
                'start_date': timezone.now().date() + timedelta(days=365),
                'end_date': timezone.now().date() + timedelta(days=730),
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'✓ Created academic year 2026'))
        else:
            self.stdout.write(f'  Academic year 2026 already exists')

        # 2. Create grades
        grade_9, created = Grade.objects.get_or_create(
            grade_number=9,
            defaults={'name': 'Grade 9', 'phase': 'senior'}
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'✓ Created Grade 9'))

        grade_10, created = Grade.objects.get_or_create(
            grade_number=10,
            defaults={'name': 'Grade 10', 'phase': 'fet'}
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'✓ Created Grade 10'))

        # 3. Create test guardian
        guardian, created = Guardian.objects.get_or_create(
            email='parent@test.com',
            defaults={
                'first_name': 'Mary',
                'last_name': 'TestParent',
                'phone_primary': '+27821234567',
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'✓ Created guardian: {guardian.get_full_name()}'))

        # 4. Create test learner
        learner, created = Learner.objects.get_or_create(
            admission_number='TEST2025001',
            defaults={
                'first_name': 'John',
                'last_name': 'TestLearner',
                'date_of_birth': '2010-03-15',
                'gender': 'M',
                'home_language': 'english',
                'sa_id_number': '1003155123084',
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'✓ Created learner: {learner.get_full_name()}'))

        # 5. Link guardian to learner
        LearnerGuardian.objects.get_or_create(
            learner=learner,
            guardian=guardian,
            defaults={
                'relationship': 'mother',
                'is_primary_contact': True,
            }
        )

        # 6. Create 2025 enrollment
        enrollment_2025, created = LearnerEnrollment.objects.get_or_create(
            learner=learner,
            academic_year=year_2025,
            defaults={
                'grade': grade_9,
                'status': 'active',
                'is_active': True,
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'✓ Created 2025 enrollment: Grade 9'))

        # 7. Create 2026 enrollment
        enrollment_2026, created = LearnerEnrollment.objects.get_or_create(
            learner=learner,
            academic_year=year_2026,
            defaults={
                'grade': grade_10,
                'status': 'admitted',
                'is_active': True,
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'✓ Created 2026 enrollment: Grade 10'))

        # 8. Create reenrollment record
        reenrollment, created = Reenrollment.objects.get_or_create(
            learner=learner,
            enrollment_year=2026,
            defaults={
                'to_academic_year': year_2026,
                'to_grade': grade_10,
                'subject_selection_required': True,
                'learner_enrollment': enrollment_2026,
                'status': 'pending',
                'expiry_date': timezone.now().date() + timedelta(days=30),
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'✓ Created reenrollment record'))
        else:
            # Reset status for testing
            reenrollment.status = 'pending'
            reenrollment.details_confirmed = False
            reenrollment.subjects_selected = False
            reenrollment.fee_agreement_signed = False
            reenrollment.payment_complete = False
            reenrollment.save()
            self.stdout.write(self.style.WARNING(f'! Reset existing reenrollment for testing'))

        # 9. Generate magic link token
        service = ReenrollmentService()
        token = service.generate_access_token(reenrollment)

        # 10. Output summary
        self.stdout.write('\n' + '='*70)
        self.stdout.write(self.style.SUCCESS('TEST DATA CREATED SUCCESSFULLY'))
        self.stdout.write('='*70)
        self.stdout.write(f'Learner:  {learner.get_full_name()} ({learner.admission_number})')
        self.stdout.write(f'Guardian: {guardian.get_full_name()} ({guardian.email})')
        self.stdout.write(f'Transition: Grade 9 (2025) → Grade 10 (2026)')
        self.stdout.write(f'Expiry: {reenrollment.expiry_date}')
        self.stdout.write('\n' + self.style.WARNING('RE-ENROLLMENT PORTAL URL:'))
        self.stdout.write(self.style.HTTP_INFO(f'http://localhost:8000/enroll/reenroll/?token={token}'))
        self.stdout.write('='*70 + '\n')
        
        self.stdout.write(self.style.SUCCESS('Copy the URL above and paste it in your browser to test!'))