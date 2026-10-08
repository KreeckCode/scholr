"""
Load the twelve METP classroom courses, and optionally link each one to its online course.

    python manage.py seed_metp_programme
    python manage.py seed_metp_programme --link-online --online-prefix METP
    python manage.py seed_metp_programme --link-online --create-bundle
    python manage.py seed_metp_programme --create-online --create-bundle

Safe to run repeatedly: existing courses are updated, never duplicated.
--link-online only reads integration.Course rows. --create-online also creates the missing ones,
with auto-enrol off so nobody is enrolled except through a licence order; they reach Moodle on
the next course sync, like any other course created in Scholr.
"""

from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Product, ProductBundle
from integration.models import Course, CourseCategory
from partners.models import ProgrammeCourse

METP_COURSES = [
    ('METP 101', 'Knowing and Loving God', 'Theology and spirituality'),
    ('METP 102', 'Learning God’s Word', 'Biblical theology and interpretation'),
    ('METP 103', 'Caring for God’s People', 'Pastoral theology and counselling'),
    ('METP 104', 'Communicating God’s Word', 'Preaching and Christian education'),
    ('METP 105', 'Ministering in Power', 'Pentecostal and charismatic emphases'),
    ('METP 106', 'Spiritual Warfare', 'Theology of the kingdom'),
    ('METP 107', 'Reaching the Lost, Making Disciples', 'Evangelism and discipleship'),
    ('METP 108', 'Financial Stewardship', 'Life skills and administration'),
    ('METP 109', 'Marriage, Family and Ministry', 'Family discipleship'),
    ('METP 110', 'Leading Yourself and Others', 'Leadership studies'),
    ('METP 111', 'Building Communities of Faith', 'Church planting and organisational leadership'),
    ('METP 112', 'Developing Christian Leaders', 'Leadership development and mentorship'),
]


class Command(BaseCommand):
    help = 'Create or update the twelve METP programme courses.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--link-online', action='store_true',
            help='Link each course to the integration.Course whose short_name is the code without spaces.',
        )
        parser.add_argument(
            '--online-prefix', default='METP',
            help='Short name prefix of the Moodle courses, for example METP gives METP101.',
        )
        parser.add_argument(
            '--create-online', action='store_true',
            help='Create any missing online course (auto-enrol off), then link it. Implies --link-online.',
        )
        parser.add_argument(
            '--category', metavar='NAME',
            help='Existing course category for created online courses. Defaults to the theology category.',
        )
        parser.add_argument(
            '--create-bundle', action='store_true',
            help='Create or update the online programme bundle product from the linked courses.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options['create_online']:
            options['link_online'] = True
            category = self._category(options['category'])
        created = updated = linked = online_created = 0
        for sequence, (code, title, area) in enumerate(METP_COURSES, start=1):
            course, was_created = ProgrammeCourse.objects.update_or_create(
                code=code,
                defaults={'title': title, 'area': area, 'sequence': sequence, 'classroom_hours': Decimal('12.0')},
            )
            created += int(was_created)
            updated += int(not was_created)
            if options['link_online']:
                short_name = f"{options['online_prefix']}{code.split()[-1]}"
                online = Course.objects.filter(short_name=short_name).first()
                if online is None and options['create_online']:
                    online = Course.objects.create(
                        title=f'{code}: {title}',
                        short_name=short_name,
                        code=code,
                        category=category,
                        summary=area,
                        visible=True,
                        auto_enroll_enabled=False,
                    )
                    online_created += 1
                if online and course.online_course_id != online.pk:
                    course.online_course = online
                    course.save(update_fields=['online_course', 'updated_at'])
                if online:
                    linked += 1
                else:
                    self.stdout.write(self.style.WARNING(f'No online course with short name {short_name}.'))

        self.stdout.write(self.style.SUCCESS(
            f'{created} course(s) created, {updated} updated, {linked} linked online.'
        ))
        if online_created:
            self.stdout.write(self.style.SUCCESS(
                f'{online_created} online course(s) created in {category.name}. Sync categories and '
                f'courses to Moodle before giving anyone online access.'
            ))

        if options['create_bundle']:
            self._create_bundle()

    def _category(self, name):
        if name:
            category = CourseCategory.objects.filter(name__iexact=name).first()
            if category is None:
                raise CommandError(f'No course category called "{name}".')
            return category
        category = CourseCategory.objects.filter(discipline='theology').first()
        if category is None:
            # I'm creating it because the discipline is unique, so there can only ever be one
            category = CourseCategory.objects.create(
                name='Ministry Essentials Training Program',
                discipline='theology',
                description='The twelve METP courses, studied online by participants at licensed locations.',
            )
            self.stdout.write(f'Created course category {category.name}.')
        return category

    def _create_bundle(self):
        courses = list(
            Course.objects.filter(programme_courses__isnull=False).order_by('programme_courses__sequence')
        )
        if not courses:
            raise CommandError(
                'No programme course is linked to an online course. Run with --create-online to create '
                'them, or --link-online once they exist.'
            )
        for course in courses:
            if course.auto_enroll_enabled:
                raise CommandError(
                    f'{course.short_name} has auto_enroll_enabled on. Turn it off so access is only granted '
                    f'through orders, then run this again.'
                )
        product, was_created = Product.objects.get_or_create(
            slug=settings.PARTNERS_ONLINE_PRODUCT_SLUG,
            defaults={
                'name': 'METP online programme',
                'product_type': Product.BUNDLE,
                'price': Decimal('0.00'),
                'description': 'All twelve METP courses online, for participants at licensed training locations.',
                'short_description': 'All twelve METP courses online.',
                'is_active': False,
                'duration_months': 18,
                'auto_enroll_free': False,
            },
        )
        if product.product_type != Product.BUNDLE:
            raise CommandError(f'Product {product.slug} exists but is not a bundle.')
        for position, course in enumerate(courses, start=1):
            ProductBundle.objects.update_or_create(
                product=product, course=course, defaults={'sort_order': position},
            )
        state = 'created' if was_created else 'updated'
        self.stdout.write(self.style.SUCCESS(
            f'Bundle {product.slug} {state} with {len(courses)} course(s). It is hidden from the catalogue; '
            f'licence-funded access works whether or not it is listed.'
        ))
