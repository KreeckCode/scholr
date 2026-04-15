"""
Signals for automatic enrollment/unenrollment handling
Ensures Moodle stays in sync when enrollments change
"""
from accounts.models import Product
import logging
from django.db.models.signals import post_save, pre_delete, post_delete
from django.dispatch import receiver
from django.utils import timezone

from accounts.models import StudentEnrollment, Order
from integration.models import CourseEnrollment, MoodleInstance
from integration.services import MoodleIntegrationService

logger = logging.getLogger(__name__)


@receiver(post_save, sender=StudentEnrollment)
def handle_enrollment_deactivation(sender, instance, created, **kwargs):
    """
    When StudentEnrollment is deactivated, deactivate all related CourseEnrollments
    and trigger Moodle unenrollment
    """
    # Skip if being created or still active
    if created or instance.is_active:
        return
    
    # Get related CourseEnrollments (if it's a bundle)
    if instance.product.product_type == 'bundle':
        from accounts.models import ProductBundle
        
        # Get all courses in bundle
        bundle_courses = ProductBundle.objects.filter(
            product=instance.product
        ).select_related('course')
        
        # Deactivate enrollments
        for bundle_item in bundle_courses:
            try:
                enrollment = CourseEnrollment.objects.get(
                    user=instance.user,
                    course=bundle_item.course,
                    is_active=True
                )
                
                # Mark as inactive and pending sync
                enrollment.is_active = False
                enrollment.sync_status = 'pending'
                enrollment.save()
                
                logger.info(
                    f"Marked enrollment {instance.user.username} → "
                    f"{bundle_item.course.short_name} for unenrollment"
                )
                
            except CourseEnrollment.DoesNotExist:
                pass
    
    elif instance.product.moodle_course:
        # Single course product
        try:
            enrollment = CourseEnrollment.objects.get(
                user=instance.user,
                course=instance.product.moodle_course,
                is_active=True
            )
            
            enrollment.is_active = False
            enrollment.sync_status = 'pending'
            enrollment.save()
            
            logger.info(
                f"Marked enrollment {instance.user.username} → "
                f"{instance.product.moodle_course.short_name} for unenrollment"
            )
            
        except CourseEnrollment.DoesNotExist:
            pass
    
    # Trigger async Moodle sync
    _trigger_unenrollment_sync(instance.user)


@receiver(pre_delete, sender=StudentEnrollment)
def unenroll_before_deletion(sender, instance, **kwargs):
    """
    Before deleting StudentEnrollment, unenroll from all courses in Moodle
    """
    logger.info(f"Processing unenrollment for {instance.user.username} → {instance.product.name}")
    
    # Get related CourseEnrollments
    if instance.product.product_type == 'bundle':
        from accounts.models import ProductBundle
        
        bundle_courses = ProductBundle.objects.filter(
            product=instance.product
        ).select_related('course')
        
        for bundle_item in bundle_courses:
            try:
                enrollment = CourseEnrollment.objects.get(
                    user=instance.user,
                    course=bundle_item.course
                )
                
                # Mark for sync then delete will happen
                enrollment.is_active = False
                enrollment.sync_status = 'pending'
                enrollment.save()
                
                # Immediate Moodle unenrollment
                _immediate_unenroll(instance.user, bundle_item.course)
                
            except CourseEnrollment.DoesNotExist:
                pass
    
    elif instance.product.moodle_course:
        try:
            enrollment = CourseEnrollment.objects.get(
                user=instance.user,
                course=instance.product.moodle_course
            )
            
            enrollment.is_active = False
            enrollment.sync_status = 'pending'
            enrollment.save()
            
            # Immediate Moodle unenrollment
            _immediate_unenroll(instance.user, instance.product.moodle_course)
            
        except CourseEnrollment.DoesNotExist:
            pass


@receiver(pre_delete, sender=Order)
def deactivate_enrollments_on_order_delete(sender, instance, **kwargs):
    """
    When order is deleted, deactivate all related enrollments
    """
    # Only process if order had enrollments
    if not instance.enrollment_processed:
        return
    
    # Get all StudentEnrollments for this order
    enrollments = StudentEnrollment.objects.filter(order=instance)
    
    for enrollment in enrollments:
        logger.info(
            f"Deactivating enrollment {enrollment.user.username} → "
            f"{enrollment.product.name} (order deleted)"
        )
        
        # Deactivate (this will trigger the post_save signal above)
        enrollment.is_active = False
        enrollment.save()


def _trigger_unenrollment_sync(user):
    """
    Trigger Moodle sync for pending unenrollments
    Non-blocking - logs errors but doesn't raise
    """
    try:
        instance = MoodleInstance.objects.filter(
            is_active=True,
            is_primary=True
        ).first()
        
        if not instance:
            logger.warning("No Moodle instance configured for sync")
            return
        
        service = MoodleIntegrationService(instance)
        
        # Sync pending enrollments (including unenrollments)
        service.sync_enrollments()
        
    except Exception as e:
        logger.error(f"Failed to trigger unenrollment sync: {str(e)}")


def _immediate_unenroll(user, course):
    """
    Immediately unenroll user from course in Moodle
    Bypasses sync queue for instant removal
    """
    try:
        # Get Moodle instance
        instance = MoodleInstance.objects.filter(
            is_active=True,
            is_primary=True
        ).first()
        
        if not instance:
            logger.warning("No Moodle instance for immediate unenrollment")
            return
        
        # Get user mapping
        from integration.models import MoodleUserMapping
        
        try:
            mapping = MoodleUserMapping.objects.get(cas_user=user)
        except MoodleUserMapping.DoesNotExist:
            logger.warning(f"User {user.username} has no Moodle mapping")
            return
        
        if not mapping.moodle_user_id or not course.moodle_course_id:
            logger.warning(
                f"Missing Moodle IDs: user={mapping.moodle_user_id}, "
                f"course={course.moodle_course_id}"
            )
            return
        
        # Unenroll via Moodle client
        service = MoodleIntegrationService(instance)
        service.client.unenrol_user(
            mapping.moodle_user_id,
            course.moodle_course_id
        )
        
        logger.info(
            f"✓ Unenrolled {user.username} from {course.short_name} in Moodle "
            f"(user {mapping.moodle_user_id}, course {course.moodle_course_id})"
        )
        
    except Exception as e:
        logger.error(
            f"Failed to immediately unenroll {user.username} from "
            f"{course.short_name}: {str(e)}"
        )




@receiver(post_save, sender=CourseEnrollment)
def check_user_enrollment_status(sender, instance, **kwargs):
    """
    After any CourseEnrollment change, check if user has any active enrollments
    If none, deactivate their Moodle account
    """
    # Only process when enrollment is deactivated
    if instance.is_active:
        return
    
    user = instance.user
    
    # Check if user has ANY other active enrollments
    has_active_enrollments = CourseEnrollment.objects.filter(
        user=user,
        is_active=True
    ).exists()
    
    # If no active enrollments, deactivate Moodle account
    if not has_active_enrollments:
        _deactivate_moodle_account(user)


def _deactivate_moodle_account(user):
    """
    Deactivate Moodle account when user has no active enrollments
    """
    try:
        from integration.models import MoodleInstance
        
        instance = MoodleInstance.objects.filter(
            is_active=True,
            is_primary=True
        ).first()
        
        if not instance:
            return
        
        # Get mapping
        try:
            mapping = user.moodle_mapping
        except:
            return
        
        if not mapping.moodle_user_id:
            return
        
        # Deactivate account
        from integration.moodle_client import MoodleClient
        client = MoodleClient(instance.base_url, instance.ws_token)
        
        client.update_user(
            user_id=mapping.moodle_user_id,
            user_data={'suspended': 1}
        )
        
        logger.info(
            f"✓ Deactivated Moodle account for {user.username} "
            f"(no active enrollments remaining)"
        )
        
    except Exception as e:
        logger.error(f"Failed to deactivate Moodle account for {user.username}: {str(e)}")

        


# Add this import at the top with your other imports
from integration.models import Course

# Add this signal handler at the end of the file
@receiver(pre_delete, sender=Course)
def handle_course_deletion_from_bundles(sender, instance, **kwargs):
    """
    Clean up ProductBundle entries when a Course is deleted
    
    This ensures:
    - Bundles don't have broken FK references
    - Empty bundles get auto-deactivated
    - Audit trail via logging
    
    Runs BEFORE deletion so we can access course data for logging
    """
    from accounts.models import ProductBundle, Product
    
    course_title = instance.title
    course_id = instance.id
    
    # Find all bundles containing this course
    affected_bundles = ProductBundle.objects.filter(
        course=instance
    ).select_related('product')
    
    if not affected_bundles.exists():
        logger.info(f"Course '{course_title}' (ID: {course_id}) deleted - no bundles affected")
        return
    
    bundle_names = []
    bundles_to_deactivate = []
    
    for bundle_item in affected_bundles:
        bundle = bundle_item.product
        bundle_names.append(bundle.name)
        
        # Check if this was the last course in bundle
        remaining_courses = bundle.bundle_items.exclude(course=instance).count()
        
        if remaining_courses == 0:
            bundles_to_deactivate.append(bundle)
    
    # Delete ProductBundle entries (CASCADE will handle this, but explicit logging)
    deleted_count = affected_bundles.count()
    
    logger.warning(
        f"Course '{course_title}' (ID: {course_id}) deleted from {deleted_count} bundle(s): "
        f"{', '.join(bundle_names)}"
    )
    
    # Deactivate empty bundles
    for bundle in bundles_to_deactivate:
        bundle.is_active = False
        bundle.save(update_fields=['is_active'])
        
        logger.warning(
            f"Bundle '{bundle.name}' (ID: {bundle.id}) auto-deactivated - "
            f"no courses remaining after deletion of '{course_title}'"
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
        