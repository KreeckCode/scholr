#!/usr/bin/env python
"""Create test subjects and offerings for Grade 10."""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'scholr.settings')
django.setup()

from academics.models.structure import Grade
from academics.models.curriculum import Curriculum, SchoolSubject, SubjectOffering

# Get or create curriculum
curriculum, _ = Curriculum.objects.get_or_create(
    code='caps',
    defaults={'name': 'CAPS (DBE)', 'is_default': True}
)

# Get Grade 10
grade_10 = Grade.objects.get(grade_number=10)

# Define all subjects (compulsory + electives)
subject_data = [
    # Compulsory subjects (first 4)
    ('English Home Language', 'EHL', True),
    ('Mathematics', 'MAT', True),
    ('Life Orientation', 'LO', True),
    ('Life Sciences', 'BIO', True),
    # Elective subjects (need at least 3)
    ('Physical Sciences', 'PHY', False),
    ('Accounting', 'ACC', False),
    ('Business Studies', 'BUS', False),
    ('Geography', 'GEO', False),
    ('History', 'HIS', False),
]

subjects_created = 0
offerings_created = 0

for name, code, is_compulsory in subject_data:
    # Create or get subject
    subject, created = SchoolSubject.objects.get_or_create(
        code=code,
        defaults={
            'curriculum': curriculum,
            'name': name,
            'subject_type': 'other',
            'default_sba_weighting': 25,
            'default_exam_weighting': 75,
        }
    )
    
    if created:
        subjects_created += 1
        print(f"✓ Created subject: {name}")
    else:
        print(f"  Found existing: {name}")
    
    # Create or get offering for Grade 10
    offering, created = SubjectOffering.objects.get_or_create(
        subject=subject,
        grade=grade_10,
        defaults={
            'is_compulsory': is_compulsory,
            'sba_weighting': 25,
            'exam_weighting': 75,
            'pass_mark': 40,
        }
    )
    
    if created:
        offerings_created += 1
        print(f"  → Created offering for Grade 10 ({'compulsory' if is_compulsory else 'elective'})")

print(f"\n✓ Summary:")
print(f"  Subjects created: {subjects_created}")
print(f"  Offerings created: {offerings_created}")
print(f"  Total subjects for Grade 10: {SubjectOffering.objects.filter(grade=grade_10).count()}")
print(f"  Compulsory: {SubjectOffering.objects.filter(grade=grade_10, is_compulsory=True).count()}")
print(f"  Elective: {SubjectOffering.objects.filter(grade=grade_10, is_compulsory=False).count()}")