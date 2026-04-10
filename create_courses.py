#!/usr/bin/env python
import os
import django
from decimal import Decimal
from django.utils import timezone
from datetime import timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'judeaharvest.settings')
django.setup()

from accounts.models import User, UserProfile, DiscountCode

print("="*60)
print("STAGE 5: DISCOUNT CODE & TEST USER")
print("="*60)

# ==================== DISCOUNT CODE ====================
print("\n🎟️  Creating discount code...")

# Calculate validity dates (1 year from now)
valid_from = timezone.now()
valid_until = valid_from + timedelta(days=365)

discount, created = DiscountCode.objects.get_or_create(
    code='LAUNCH2025',
    defaults={
        'description': 'Launch promotion - 20% off all courses',
        'discount_type': 'percentage',
        'discount_value': Decimal('20.00'),  # 20%
        'minimum_purchase': Decimal('500.00'),
        'max_uses': None,  # Unlimited uses
        'max_uses_per_user': 1,
        'valid_from': valid_from,
        'valid_until': valid_until,
        'is_active': True,
    }
)

print(f"  {'CREATED' if created else 'EXISTS'}: {discount.code}")
print(f"    Discount: {discount.discount_value}%")
print(f"    Min purchase: R{discount.minimum_purchase}")
print(f"    Valid until: {valid_until.strftime('%Y-%m-%d')}")

# ==================== TEST USER ====================
print("\n👤 Creating test student user...")

# Create test user
test_user, user_created = User.objects.get_or_create(
    username='student1',
    defaults={
        'first_name': 'Test',
        'last_name': 'Student',
        'email': 'student1@test.com',
        'phone_number': '+27123456789',
        'role': User.STUDENT,
        'is_verified': True,
        'is_active': True,
    }
)

if user_created:
    test_user.set_password('password123')
    test_user.save()
    print(f"  CREATED: {test_user.username}")
else:
    print(f"  EXISTS: {test_user.username}")

print(f"    Username: student1")
print(f"    Password: password123")
print(f"    Email: student1@test.com")

# Create/update profile
profile, profile_created = UserProfile.objects.get_or_create(
    user=test_user,
    defaults={
        'country': 'South Africa',
        'region': 'Gauteng',
        'village_town': 'Johannesburg',
        'church_name': 'Test Church',
        'church_affiliation': 'Independent',
        'church_size': 'medium',
        'years_in_ministry': 5,
        'position_title': 'Pastor',
        'education_level': 'secondary',
        'languages_spoken': 'English, Afrikaans',
    }
)

print(f"  Profile: {'CREATED' if profile_created else 'EXISTS'}")

print("\n" + "="*60)
print("✅ STAGE 5 COMPLETE!")
print("="*60)

print("\n📋 SUMMARY OF ALL TEST DATA")
print("="*60)

from accounts.models import Product, GradeLevel, Subject
from integration.models import Course

print(f"\nCourses: {Course.objects.count()}")
for c in Course.objects.all().order_by('moodle_course_id'):
    print(f"  {c.moodle_course_id}: {c.short_name} - {c.title}")

print(f"\nProducts: {Product.objects.count()}")
for p in Product.objects.all().order_by('price'):
    product_type = p.get_product_type_display()
    print(f"  R{p.price:>7.2f} - {p.name} ({product_type})")

print(f"\nDiscount Codes: {DiscountCode.objects.count()}")
for d in DiscountCode.objects.all():
    print(f"  {d.code}: {d.discount_value}% off (min R{d.minimum_purchase})")

print(f"\nTest Users: 1")
print(f"  Username: student1 | Password: password123")

print("\n" + "="*60)
print("🎉 ALL TEST DATA SETUP COMPLETE!")
print("="*60)
print("\n✅ You can now:")
print("  1. Login as student1/password123")
print("  2. Browse the catalog")
print("  3. Add products to cart")
print("  4. Use discount code: LAUNCH2025")
print("  5. Test checkout flow")
print("="*60)