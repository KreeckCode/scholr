from django.core.management.base import BaseCommand
from django.utils.text import slugify
from decimal import Decimal
from integration.models import Course
from accounts.models import Product


class Command(BaseCommand):
    help = 'Create Products for Courses that dont have them'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be created without actually creating',
        )
        parser.add_argument(
            '--activate',
            action='store_true',
            help='Activate products immediately (default: inactive)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        activate = options['activate']
        
        # Find courses without products
        courses_without_products = Course.objects.filter(
            products__isnull=True
        ).distinct()
        
        count = courses_without_products.count()
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS('✓ All courses have products'))
            return
        
        self.stdout.write(f'Found {count} course(s) without products\n')
        
        created_count = 0
        
        for course in courses_without_products:
            slug = slugify(course.short_name)
            
            # Ensure unique slug
            base_slug = slug
            counter = 1
            while Product.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            
            if dry_run:
                self.stdout.write(
                    f'[DRY RUN] Would create Product for: {course.title} (slug: {slug})'
                )
            else:
                product = Product.objects.create(
                    name=course.title,
                    slug=slug,
                    product_type=Product.COURSE,
                    moodle_course=course,
                    price=Decimal('0.00'),
                    is_free=True,
                    description=course.summary or 'Course description pending',
                    short_description=course.summary[:200] if course.summary else '',
                    is_active=activate,
                    duration_months=12,
                )
                
                self.stdout.write(
                    self.style.SUCCESS(
                        f'✓ Created Product "{product.name}" (ID: {product.id}) '
                        f'for Course "{course.title}" (ID: {course.id})'
                    )
                )
                created_count += 1
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f'\n[DRY RUN] Would create {count} product(s). '
                    f'Run without --dry-run to actually create.'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f'\n✓ Created {created_count} product(s)')
            )