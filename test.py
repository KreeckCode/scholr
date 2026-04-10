from accounts.models import *
from integration.models import *
from decimal import Decimal
from django.utils import timezone
from datetime import timedelta

print("="*60)
print("STAGE 1: BASIC SETUP (CORRECTED)")
print("="*60)

# ==================== CREATE MOODLE INSTANCE ====================
# FIXED: Using correct field names from integration/models.py
moodle_instance, created = MoodleInstance.objects.get_or_create(
    name='Primary Moodle',
    defaults={
        'base_url': 'http://localhost:8888/moodle501/',      # ✓ CORRECTED
        'ws_token': '593f86a543044127bfafb5fc277bd8ff',        # ✓ CORRECTED
        'ws_service_name': 'judeacas',                         # ✓ CORRECTED
        'is_primary': True,
        'is_active': True
    }
)

if created:
    print(f"✓ Created NEW Moodle Instance: {moodle_instance.name}")
else:
    # Update existing with correct credentials
    moodle_instance.ws_token = '593f86a543044127bfafb5fc277bd8ff'
    moodle_instance.ws_service_name = 'judeacas'
    moodle_instance.save()
    print(f"✓ Updated EXISTING Moodle Instance: {moodle_instance.name}")

print(f"  URL: {moodle_instance.base_url}")
print(f"  Token: {moodle_instance.ws_token[:20]}...")
print(f"  Service: {moodle_instance.ws_service_name}")

# ==================== CREATE GRADE LEVELS ====================
print("\nCreating Grade Levels...")

grade10, created = GradeLevel.objects.get_or_create(
    slug='grade-10',
    defaults={
        'name': 'Grade 10',
        'description': 'South African Grade 10 CAPS Curriculum',
        'sort_order': 10,
        'is_active': True
    }
)
print(f"  {'Created' if created else 'Exists'}: {grade10.name}")

grade11, created = GradeLevel.objects.get_or_create(
    slug='grade-11',
    defaults={
        'name': 'Grade 11',
        'description': 'South African Grade 11 CAPS Curriculum',
        'sort_order': 11,
        'is_active': True
    }
)
print(f"  {'Created' if created else 'Exists'}: {grade11.name}")

certificate, created = GradeLevel.objects.get_or_create(
    slug='certificate',
    defaults={
        'name': 'Certificate Level',
        'description': 'Entry level theological studies',
        'sort_order': 1,
        'is_active': True
    }
)
print(f"  {'Created' if created else 'Exists'}: {certificate.name}")

print(f"\n✓ Grade Levels Total: {GradeLevel.objects.count()}")

# ==================== CREATE SUBJECTS ====================
print("\nCreating Subjects...")

subjects_data = [
    {'name': 'Mathematics', 'code': 'MATH', 'icon': 'fa-calculator', 'color': '#3B82F6'},
    {'name': 'Economics', 'code': 'ECON', 'icon': 'fa-chart-line', 'color': '#10B981'},
    {'name': 'Business Studies', 'code': 'BUS', 'icon': 'fa-briefcase', 'color': '#F59E0B'},
    {'name': 'Theology', 'code': 'THEO', 'icon': 'fa-bible', 'color': '#8B5CF6'},
    {'name': 'Biblical Studies', 'code': 'BIBL', 'icon': 'fa-book', 'color': '#EC4899'},
]

for subj_data in subjects_data:
    subject, created = Subject.objects.get_or_create(
        code=subj_data['code'],
        defaults=subj_data
    )
    print(f"  {'Created' if created else 'Exists'}: {subject.name} ({subject.code})")

print(f"\n✓ Subjects Total: {Subject.objects.count()}")

print("\n" + "="*60)
print("✅ STAGE 1 COMPLETE!")
print("="*60)

print("\n📋 SUMMARY:")
print(f"  ✓ 1 Moodle Instance (with real credentials)")
print(f"  ✓ {GradeLevel.objects.count()} Grade Levels")
print(f"  ✓ {Subject.objects.count()} Subjects")