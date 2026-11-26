"""
Django signals for automatic sync triggers
Handles post-save actions to mark items for sync
"""

import logging
from decimal import Decimal

from django.db.models.signals import post_save, pre_save, pre_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import Product, StudentEnrollment, User
from .models import Course, CourseEnrollment, MoodleUserMapping

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def create_moodle_mapping_on_verification(sender, instance, created, **kwargs):
    """
    Automatically create MoodleUserMapping when student is verified
    Only applies to students who are verified
    """
    # Only for students
    if instance.role != User.STUDENT:
        return
    
    # Only if verified
    if not instance.is_verified:
        return
    
    # Check if mapping already exists
    if hasattr(instance, 'moodle_mapping'):
        # User already has mapping, mark as modified if needed
        mapping = instance.moodle_mapping
        if mapping.sync_status == 'synced':
            mapping.sync_status = 'modified'
            mapping.save(update_fields=['sync_status'])
        return
    
    # Create new mapping
    MoodleUserMapping.objects.create(
        cas_user=instance,
        moodle_username=instance.username,
        sync_status='pending'
    )


@receiver(pre_save, sender=Course)
def mark_course_for_sync_on_change(sender, instance, **kwargs):
    """
    Mark course for sync when it's modified
    Only if already synced to Moodle
    """
    if not instance.pk:
        # New course, will be pending by default
        return
    
    try:
        old_instance = Course.objects.get(pk=instance.pk)
    except Course.DoesNotExist:
        return
    
    # Only mark as modified if currently synced
    if old_instance.sync_status != 'synced':
        return
    
    # Check if any synced fields changed
    synced_fields = [
        'title', 'summary', 'visible', 'start_date', 'end_date'
    ]
    
    for field in synced_fields:
        if getattr(old_instance, field) != getattr(instance, field):
            instance.sync_status = 'modified'
            break


@receiver(post_save, sender=CourseEnrollment)
def mark_enrollment_for_sync(sender, instance, created, update_fields=None, **kwargs):
    """
    Mark enrollment for sync when created or modified
    """
    # If just created, already pending
    if created:
        return

    # mark_synced and mark_failed write sync_status themselves; flipping that back
    # to pending left every enrolment looking unsynced the moment it was synced
    if update_fields and 'sync_status' in update_fields:
        return
    
    # If status changed, mark for sync
    if instance.sync_status == 'synced':
        instance.sync_status = 'pending'
        instance.save(update_fields=['sync_status'])


@receiver(post_save, sender=Course)
def auto_enroll_verified_students(sender, instance, created, **kwargs):
    """
    Automatically enroll verified students when course is created
    Only if auto_enroll_enabled is True
    """
    # Only for newly created courses
    if not created:
        return
    
    # Only if auto-enroll enabled
    if not instance.auto_enroll_enabled:
        return
    
    # Only if course is visible
    if not instance.visible:
        return
    
    # Get all verified students
    verified_students = User.objects.filter(
        role=User.STUDENT,
        is_verified=True,
        is_active=True
    )
    
    # Create enrollments
    enrollments = []
    for student in verified_students:
        enrollment, created_enrollment = CourseEnrollment.objects.get_or_create(
            user=student,
            course=instance,
            defaults={
                'is_active': True,
                'sync_status': 'pending'
            }
        )
        
        if created_enrollment:
            enrollments.append(enrollment)
    
    if enrollments:
        try:
            from .utils import log_sync_event
            log_sync_event(
                'AUTO_ENROLL',
                f'Auto-enrolled {len(enrollments)} students in {instance.short_name}',
                'INFO'
            )
        except ImportError:
            # If utils doesn't exist, just log normally
            logger.info(
                f'Auto-enrolled {len(enrollments)} students in {instance.short_name}'
            )


@receiver(post_save, sender=Course)
def auto_create_product_for_course(sender, instance, created, **kwargs):
    """
    Automatically create a Product when a Course is created
    
    This ensures new courses appear in the catalog immediately
    Product is created as inactive by default - admin must review and activate
    
    Why: Prevents orphaned courses that never appear in catalog
    """
    if created:
        # Check if Product already exists (edge case: manual creation)
        if Product.objects.filter(moodle_course=instance).exists():
            logger.info(
                f"Product already exists for course '{instance.title}' - skipping auto-creation"
            )
            return
        
        # Generate slug from short_name
        slug = slugify(instance.short_name)
        
        # Ensure slug is unique
        base_slug = slug
        counter = 1
        while Product.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        # Create product
        product = Product.objects.create(
            name=instance.title,
            slug=slug,
            product_type=Product.COURSE,
            moodle_course=instance,
            price=Decimal('0.00'),  # Default to free
            is_free=True,
            description=instance.summary or 'Course description pending',
            short_description=instance.summary[:200] if instance.summary else '',
            is_active=False,  # Admin must activate after setting price
            duration_months=12,  # Default duration
            # Note: grade_level and subject require mapping - leave null for now
        )
        
        logger.info(
            f"Auto-created Product '{product.name}' (ID: {product.id}) for "
            f"Course '{instance.title}' (ID: {instance.id})"
        )


@receiver(pre_delete, sender=Course)
def handle_course_product_cleanup(sender, instance, **kwargs):
    """
    When a Course is deleted, deactivate or delete associated single-course Products
    
    Two scenarios:
    1. Product type='course' → Deactivate (preserves order history)
    2. Product type='bundle' → Already handled by handle_course_deletion_from_bundles
    
    Why deactivate instead of delete:
    - Preserves order history and audit trail
    - Students who purchased still see what they bought
    - Can be manually deleted later if needed
    """
    course_title = instance.title
    course_id = instance.id
    
    # Find single-course products linked to this course
    affected_products = Product.objects.filter(
        moodle_course=instance,
        product_type=Product.COURSE  # Only single-course products
    )
    
    if not affected_products.exists():
        return
    
    product_names = []
    
    for product in affected_products:
        product_names.append(product.name)
        
        # Check if product has active enrollments
        active_enrollments = StudentEnrollment.objects.filter(
            product=product,
            is_active=True
        ).count()
        
        if active_enrollments > 0:
            # Deactivate product but keep it for enrolled students
            product.is_active = False
            product.save(update_fields=['is_active'])
            
            logger.warning(
                f"Product '{product.name}' (ID: {product.id}) deactivated - "
                f"course '{course_title}' deleted, {active_enrollments} active enrollment(s) remain"
            )
        else:
            # No enrollments - safe to delete product
            product.delete()
            
            logger.info(
                f"Product '{product.name}' (ID: {product.id}) deleted - "
                f"course '{course_title}' deleted, no active enrollments"
            )
    
    if product_names:
        logger.warning(
            f"Course '{course_title}' (ID: {course_id}) deletion affected "
            f"{len(product_names)} product(s): {', '.join(product_names)}"
        )

# ==================== PARTNER COHORT GROUPS ====================
# Every receiver below only queues a sync. The task itself checks the METP-only flag.

def _queue(cohort_ids):
    from .tasks import queue_cohort_sync
    queue_cohort_sync(cohort_ids)


def _connect_cohort_group_signals():
    from django.db.models.signals import m2m_changed
    from partners.models import Cohort, CohortMembership, LocationMembership

    @receiver(post_save, sender=CohortMembership, dispatch_uid='cohort_groups_membership')
    def cohort_membership_changed(sender, instance, **kwargs):
        _queue([instance.cohort_id])

    @receiver(post_save, sender=LocationMembership, dispatch_uid='cohort_groups_location_team')
    def location_team_changed(sender, instance, **kwargs):
        _queue(Cohort.objects.filter(
            location_id=instance.location_id, delivery=Cohort.Delivery.BLENDED,
        ).values_list('pk', flat=True))

    @receiver(m2m_changed, sender=Cohort.facilitators.through, dispatch_uid='cohort_groups_facilitators')
    def cohort_facilitators_changed(sender, instance, action, pk_set, reverse, **kwargs):
        if action not in ('post_add', 'post_remove', 'post_clear'):
            return
        if reverse:
            # A user's cohorts were changed from the user side
            _queue(pk_set or instance.facilitated_cohorts.values_list('pk', flat=True))
        else:
            _queue([instance.pk])


_connect_cohort_group_signals()


@receiver(post_save, sender=CourseEnrollment, dispatch_uid='cohort_groups_enrolment_done')
def course_enrolment_completed(sender, instance, update_fields=None, **kwargs):
    """
    mark_synced() is how the order flow reports a finished Moodle enrolment, and it is the
    only save that writes moodle_enrollment_id. Progress syncs save without update_fields,
    so they never trigger this.
    """
    if not update_fields or 'moodle_enrollment_id' not in update_fields:
        return
    if not instance.is_active or instance.sync_status != 'synced':
        return
    from partners.models import CohortMembership
    _queue(
        CohortMembership.objects.filter(
            participant__user_id=instance.user_id, access_order__isnull=False,
        ).values_list('cohort_id', flat=True)
    )


# ==================== ROLE CHANGES ====================

def _connect_reach_signals():
    """A changed country or partner assignment changes which cohort groups that person belongs in."""
    from django.db.models.signals import post_delete

    def affected_cohorts(assignment):
        from partners.models import Cohort
        if assignment.organisation_id:
            cohorts = Cohort.objects.filter(location__organisation_id=assignment.organisation_id)
        else:
            cohorts = Cohort.objects.filter(location__organisation__country=assignment.country)
        return list(cohorts.values_list('pk', flat=True))

    @receiver(post_save, sender='partners.StaffAssignment', dispatch_uid='cohort_groups_reach_added')
    def reach_added(sender, instance, **kwargs):
        _queue(affected_cohorts(instance))

    @receiver(post_delete, sender='partners.StaffAssignment', dispatch_uid='cohort_groups_reach_removed')
    def reach_removed(sender, instance, **kwargs):
        _queue(affected_cohorts(instance))


_connect_reach_signals()


def _connect_role_change_signals():
    from accounts.roles import role_changed

    @receiver(role_changed, dispatch_uid='integration_revoke_old_lms_access')
    def remove_old_lms_access(sender, user, old_role, new_role, **kwargs):
        """A role with a different Moodle course role loses the old enrolments."""
        from .tasks import queue_staff_lms_revocation
        queue_staff_lms_revocation(user.pk, old_role, new_role)

    @receiver(role_changed, dispatch_uid='integration_forget_staff_lms_access')
    def forget_cached_lms_access(sender, user, **kwargs):
        """A new role means a new Moodle role, so the next LMS Portal visit must not be served from cache."""
        from django.core.cache import cache
        from .staff_access import _access_cache_key
        cache.delete(_access_cache_key(user))


_connect_role_change_signals()
