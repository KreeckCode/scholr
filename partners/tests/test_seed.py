from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import Product, ProductBundle, User
from integration.models import Course, CourseCategory, CourseEnrollment
from partners.models import ProgrammeCourse

from .base import PartnersTestCase
from .helpers import make_user


def seed(*args):
    output = StringIO()
    call_command('seed_metp_programme', *args, stdout=output)
    return output.getvalue()


class SeedOnlineProgrammeTests(PartnersTestCase):
    def test_create_online_builds_courses_and_bundle_without_enrolling_anyone(self):
        make_user(User.STUDENT, is_verified=True)
        output = seed('--create-online', '--create-bundle')

        courses = Course.objects.filter(short_name__startswith='METP').order_by('short_name')
        self.assertEqual(courses.count(), 12)
        self.assertFalse(courses.filter(auto_enroll_enabled=True).exists())
        self.assertEqual(courses.first().title, 'METP 101: Knowing and Loving God')
        self.assertEqual(courses.first().category.discipline, 'theology')
        self.assertEqual(ProgrammeCourse.objects.filter(online_course__isnull=False).count(), 12)
        self.assertFalse(CourseEnrollment.objects.exists())

        product = Product.objects.get(slug='metp-online-programme')
        self.assertEqual(product.product_type, Product.BUNDLE)
        self.assertFalse(product.is_active)
        self.assertEqual(ProductBundle.objects.filter(product=product).count(), 12)
        self.assertFalse(
            Product.objects.filter(moodle_course__in=courses, product_type=Product.COURSE, is_active=True).exists()
        )
        self.assertIn('12 online course(s) created', output)

    def test_running_twice_creates_nothing_new(self):
        seed('--create-online', '--create-bundle')
        seed('--create-online', '--create-bundle')
        self.assertEqual(Course.objects.filter(short_name__startswith='METP').count(), 12)
        self.assertEqual(CourseCategory.objects.filter(discipline='theology').count(), 1)
        self.assertEqual(ProductBundle.objects.filter(product__slug='metp-online-programme').count(), 12)

    def test_existing_theology_category_is_reused(self):
        existing = CourseCategory.objects.create(name='Theology', discipline='theology')
        seed('--create-online')
        self.assertEqual(set(Course.objects.values_list('category', flat=True)), {existing.pk})

    def test_named_category_must_exist(self):
        with self.assertRaises(CommandError):
            seed('--create-online', '--category', 'Nowhere')
        self.assertFalse(Course.objects.exists())

    def test_bundle_without_online_courses_points_to_create_online(self):
        with self.assertRaisesMessage(CommandError, '--create-online'):
            seed('--link-online', '--create-bundle')
        self.assertFalse(Product.objects.filter(slug='metp-online-programme').exists())
