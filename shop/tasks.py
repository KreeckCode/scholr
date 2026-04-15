from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone
from django.core.cache import cache
from datetime import timedelta

logger = get_task_logger(__name__)


@shared_task(bind=True, max_retries=3)
def process_order_enrollment_task(self, order_id):
    """
    Background task to process order enrollment
    Prevents blocking HTTP requests during enrollment
    """
    from accounts.models import Order
    from shop.services import EnrollmentService
    
    try:
        order = Order.objects.select_related('user').get(id=order_id)
        
        if not order.is_paid():
            logger.warning(f"Order {order.order_number} not paid, skipping enrollment")
            return
        
        if order.enrollment_processed:
            logger.warning(f"Order {order.order_number} already processed")
            return
        
        service = EnrollmentService()
        enrollments = service.process_order_enrollment(order)
        
        # Clear user's course cache
        cache.delete(f'user_courses:{order.user.id}')
        cache.delete(f'user_enrollments:{order.user.id}')
        
        logger.info(f"Successfully processed enrollment for order {order.order_number}: {len(enrollments)} products")
        
        return {
            'order_number': order.order_number,
            'enrollments_created': len(enrollments),
            'status': 'success'
        }
        
    except Order.DoesNotExist:
        logger.error(f"Order {order_id} not found")
        raise
    
    except Exception as exc:
        logger.error(f"Error processing enrollment for order {order_id}: {str(exc)}")
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@shared_task(bind=True, max_retries=2)
def sync_user_progress_task(self, user_id):
    """
    Background task to sync user progress from Moodle
    Rate limited to prevent overwhelming Moodle
    """
    from accounts.models import User
    from shop.services import MoodleProgressService
    
    try:
        user = User.objects.get(id=user_id)
        
        service = MoodleProgressService()
        result = service.sync_user_progress(user)
        
        # Clear user's progress cache
        cache.delete(f'user_progress:{user_id}')
        cache.delete(f'user_courses:{user_id}')
        
        logger.info(f"Progress sync for {user.username}: {result['courses_synced']} courses synced")
        
        return result
        
    except User.DoesNotExist:
        logger.error(f"User {user_id} not found")
        raise
    
    except Exception as exc:
        logger.error(f"Error syncing progress for user {user_id}: {str(exc)}")
        raise self.retry(exc=exc, countdown=120)


@shared_task(bind=True, max_retries=1)
def reconcile_enrollments_task(self, initiated_by_id=None):
    """
    Background task to reconcile all enrollments
    This is a heavy operation - runs in background
    """
    from shop.services import EnrollmentReconciliationService
    from accounts.models import User
    
    try:
        initiated_by = None
        if initiated_by_id:
            initiated_by = User.objects.get(id=initiated_by_id)
        
        service = EnrollmentReconciliationService()
        results = service.reconcile_all_enrollments(initiated_by=initiated_by)
        
        # Clear all user course caches
        cache.delete_pattern('user_courses:*')
        cache.delete_pattern('user_enrollments:*')
        
        logger.info(
            f"Reconciliation complete: {results['users_processed']} users, "
            f"{results['enrollments_removed']} removed, {results['enrollments_added']} added"
        )
        
        return results
        
    except Exception as exc:
        logger.error(f"Error during reconciliation: {str(exc)}")
        raise self.retry(exc=exc, countdown=300)


@shared_task
def send_enrollment_email_task(user_id, order_id):
    """
    Background task to send enrollment confirmation email
    """
    from accounts.models import User, Order, StudentEnrollment
    from django.core.mail import send_mail
    from django.template.loader import render_to_string
    from django.conf import settings
    
    try:
        user = User.objects.get(id=user_id)
        order = Order.objects.get(id=order_id)
        
        if not user.email:
            logger.warning(f"User {user.username} has no email address")
            return
        
        enrollments = StudentEnrollment.objects.filter(
            order=order
        ).select_related('product')
        
        context = {
            'user': user,
            'order': order,
            'enrollments': enrollments,
            'is_free': order.is_free_order(),
        }
        
        subject = "Welcome to African Pastoral Academy!" if order.is_free_order() else "Your Purchase Confirmation"
        message = render_to_string('shop/emails/enrollment_confirmation.txt', context)
        
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
        
        logger.info(f"Sent enrollment email to {user.email}")
        
    except Exception as exc:
        logger.error(f"Error sending email: {str(exc)}")


@shared_task
def cleanup_expired_enrollments():
    """
    Periodic task to clean up expired enrollments
    Runs daily at 2 AM
    """
    from accounts.models import StudentEnrollment
    
    expired = StudentEnrollment.objects.filter(
        is_active=True,
        expires_at__lt=timezone.now()
    )
    
    count = expired.count()
    expired.update(is_active=False)
    
    logger.info(f"Deactivated {count} expired enrollments")
    
    return {'deactivated': count}


@shared_task
def sync_all_user_progress():
    """
    Periodic task to sync all user progress
    Runs daily at 3 AM
    """
    from accounts.models import User
    
    users = User.objects.filter(
        role=User.STUDENT,
        is_active=True,
        moodle_mapping__isnull=False
    ).values_list('id', flat=True)
    
    # Queue individual sync tasks
    for user_id in users:
        sync_user_progress_task.delay(user_id)
    
    logger.info(f"Queued progress sync for {len(users)} users")
    
    return {'users_queued': len(users)}


@shared_task(bind=True, max_retries=3)
def sync_moodle_users(self, user_ids, initiated_by_id=None):
    """
    Background task to sync users to Moodle
    """
    from integration.services import MoodleIntegrationService
    from integration.models import MoodleInstance
    from accounts.models import User
    
    try:
        instance = MoodleInstance.objects.filter(
            is_active=True,
            is_primary=True
        ).first()
        
        if not instance:
            logger.error("No active Moodle instance")
            return {'error': 'No active instance'}
        
        initiated_by = None
        if initiated_by_id:
            initiated_by = User.objects.get(id=initiated_by_id)
        
        service = MoodleIntegrationService(instance)
        sync_log = service.sync_users(
            initiated_by=initiated_by,
            user_ids=user_ids
        )
        
        logger.info(f"User sync completed: {sync_log.successful_items}/{sync_log.total_items}")
        
        return {
            'total': sync_log.total_items,
            'successful': sync_log.successful_items,
            'status': sync_log.status
        }
        
    except Exception as exc:
        logger.error(f"Error syncing users: {str(exc)}")
        raise self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=3)
def sync_moodle_courses(self, course_ids=None, initiated_by_id=None):
    """
    Background task to sync courses to Moodle
    """
    from integration.services import MoodleIntegrationService
    from integration.models import MoodleInstance
    from accounts.models import User
    
    try:
        instance = MoodleInstance.objects.filter(
            is_active=True,
            is_primary=True
        ).first()
        
        if not instance:
            logger.error("No active Moodle instance")
            return {'error': 'No active instance'}
        
        initiated_by = None
        if initiated_by_id:
            initiated_by = User.objects.get(id=initiated_by_id)
        
        service = MoodleIntegrationService(instance)
        sync_log = service.sync_courses(
            initiated_by=initiated_by,
            course_ids=course_ids
        )
        
        logger.info(f"Course sync completed: {sync_log.successful_items}/{sync_log.total_items}")
        
        return {
            'total': sync_log.total_items,
            'successful': sync_log.successful_items,
            'status': sync_log.status
        }
        
    except Exception as exc:
        logger.error(f"Error syncing courses: {str(exc)}")
        raise self.retry(exc=exc, countdown=60)
    

@shared_task(bind=True, max_retries=3)
def process_pending_enrollments_task(self, user_id):
    """
    Process all pending enrollments for newly verified user
    Called after user verification
    """
    from accounts.models import User, Order
    from shop.services import EnrollmentService
    
    try:
        user = User.objects.get(id=user_id)
        
        if not user.is_verified:
            logger.warning(f"User {user.username} not verified, skipping enrollment processing")
            return
        
        # Get all paid orders that haven't been processed
        pending_orders = Order.objects.filter(
            user=user,
            status__in=[Order.COMPLETED, Order.FREE],
            enrollment_processed=False
        )
        
        if not pending_orders.exists():
            logger.info(f"No pending orders for {user.username}")
            return
        
        service = EnrollmentService()
        processed_count = 0
        
        for order in pending_orders:
            try:
                service.process_order_enrollment(order)
                processed_count += 1
                logger.info(f"Processed enrollment for order {order.order_number}")
            except Exception as e:
                logger.error(f"Failed to process order {order.order_number}: {str(e)}")
        
        logger.info(f"Processed {processed_count} pending enrollments for {user.username}")
        
        return {
            'user': user.username,
            'orders_processed': processed_count
        }
        
    except User.DoesNotExist:
        logger.error(f"User {user_id} not found")
        raise
    
    except Exception as exc:
        logger.error(f"Error processing pending enrollments for user {user_id}: {str(exc)}")
        raise self.retry(exc=exc, countdown=120)