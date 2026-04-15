from integration.models import CourseEnrollment, Course
import logging
import hashlib
import hmac
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
import requests
from accounts.models import (
    User, StudentEnrollment, Order, OrderItem, 
    Product, ProductBundle, DiscountCode
)
from integration.models import Course, CourseEnrollment, MoodleInstance
from integration.services import MoodleIntegrationService
from integration.moodle_client import MoodleClient
from integration.models import MoodleInstance, MoodleUserMapping
from django.core.cache import cache

logger = logging.getLogger(__name__)


class OrderService:
    """
    Handles order creation and management
    
    Security considerations:
    - Validates user ownership before operations
    - Prevents price manipulation (uses DB prices, not client prices)
    - Atomic transactions to prevent partial orders
    - Audit trail for all changes
    """
    
    @transaction.atomic
    def create_order_from_cart(self, user, cart, discount_code=None, customer_notes=''):
        """
        Create order from shopping cart
        
        Security:
        - Uses database product prices (not cart prices)
        - Validates discount code
        - Atomic transaction
        
        Args:
            user: User instance
            cart: Cart instance
            discount_code: Optional DiscountCode instance
            customer_notes: Optional customer notes
            
        Returns:
            Order instance
        """
        order = Order.objects.create(
            user=user,
            status=Order.PENDING,
            customer_notes=customer_notes
        )
        
        subtotal = Decimal('0.00')
        
        for item in cart:
            product = item['product']
            quantity = item['quantity']
            
            db_product = Product.objects.get(id=product.id)
            unit_price = db_product.price
            
            OrderItem.objects.create(
                order=order,
                product=db_product,
                quantity=quantity,
                unit_price=unit_price
            )
            
            subtotal += unit_price * quantity
        
        order.subtotal = subtotal
        discount_amount = Decimal('0.00')
        
        if discount_code and discount_code.is_valid():
            discount_amount = discount_code.calculate_discount(subtotal)
            discount_code.times_used += 1
            discount_code.save()
        
        order.discount_amount = discount_amount
        order.total_amount = subtotal - discount_amount
        
        if order.total_amount == Decimal('0.00'):
            order.status = Order.FREE
            order.payment_method = 'free'
            order.paid_at = timezone.now()
        
        order.save()
        
        logger.info(f"Created order {order.order_number} for user {user.username}: R{order.total_amount}")
        
        return order
    
    def verify_order_ownership(self, order, user):
        """Security: Verify user owns this order"""
        if order.user != user and not user.role == User.ADMIN:
            logger.warning(f"Unauthorized order access attempt: {user.username} → {order.order_number}")
            raise PermissionError("You don't have permission to access this order")
    
    def cancel_order(self, order, user):
        """Cancel pending order"""
        self.verify_order_ownership(order, user)
        
        if order.status != Order.PENDING:
            raise ValueError("Only pending orders can be cancelled")
        
        order.status = Order.FAILED
        order.save()
        
        logger.info(f"Order {order.order_number} cancelled by {user.username}")
    
    def get_user_orders(self, user):
        """Get all orders for user (with security check)"""
        if user.role == User.ADMIN:
            return Order.objects.all().select_related('user').prefetch_related('items__product')
        else:
            return Order.objects.filter(user=user).prefetch_related('items__product')


class EnrollmentService:
    """
    Handles product enrollment in courses
    Processes both FREE and PAID orders
    """
    
    def __init__(self):
        self.moodle_service = None
    
    def _get_moodle_service(self):
        """Lazy load Moodle service"""
        if not self.moodle_service:
            instance = MoodleInstance.objects.filter(
                is_active=True,
                is_primary=True
            ).first()
            
            if instance:
                self.moodle_service = MoodleIntegrationService(instance)
        
        return self.moodle_service
    
    @transaction.atomic
    def process_order_enrollment(self, order):
        """Main enrollment processor"""
        if not order.is_paid():
            raise ValueError(f"Order {order.order_number} is not paid")
        
        if order.enrollment_processed:
            logger.warning(f"Order {order.order_number} already processed")
            return []
        
        logger.info(f"Processing enrollment for order {order.order_number}")
        
        try:
            courses_to_enroll = self._expand_products_to_courses(order)
            enrollments = self._create_enrollment_records(order.user, courses_to_enroll, order)
            self._sync_to_moodle(order.user, courses_to_enroll)
            self._send_enrollment_confirmation(order.user, enrollments, order)
            
            order.enrollment_processed = True
            order.enrollment_processed_at = timezone.now()
            order.save()
            
            # Clear user cache
            cache.delete(f'user_enrollments:{order.user.id}')
            cache.delete(f'user_courses:{order.user.id}')
            
            logger.info(f"Enrollment complete: {order.order_number} - {len(enrollments)} products")
            
            return enrollments
            
        except Exception as e:
            order.enrollment_error = str(e)
            order.save()
            logger.error(f"Enrollment failed for {order.order_number}: {str(e)}")
            raise
    
    def _expand_products_to_courses(self, order):
        """Convert products to course list"""
        courses = []
        
        for item in order.items.select_related('product').prefetch_related('product__bundle_items__course'):
            product = item.product
            
            if product.product_type == Product.COURSE:
                if product.moodle_course:
                    courses.append({
                        'course': product.moodle_course,
                        'terms': [1, 2, 3, 4],
                        'duration_months': product.duration_months,
                        'product': product,
                        'order_item': item
                    })
            
            elif product.product_type == Product.BUNDLE:
                for bundle_item in product.bundle_items.all():
                    courses.append({
                        'course': bundle_item.course,
                        'terms': bundle_item.include_terms or [1, 2, 3, 4],
                        'duration_months': product.duration_months,
                        'product': product,
                        'order_item': item
                    })
            
            elif product.product_type == Product.TERM:
                if product.moodle_course:
                    courses.append({
                        'course': product.moodle_course,
                        'terms': [product.term_number],
                        'duration_months': product.duration_months,
                        'product': product,
                        'order_item': item
                    })
        
        return self._deduplicate_courses(courses)
    
    def _deduplicate_courses(self, courses):
        """Merge duplicate courses"""
        course_map = {}
        
        for course_data in courses:
            course_id = course_data['course'].id
            
            if course_id in course_map:
                existing_terms = set(course_map[course_id]['terms'])
                new_terms = set(course_data['terms'])
                course_map[course_id]['terms'] = sorted(existing_terms | new_terms)
                
                course_map[course_id]['duration_months'] = max(
                    course_map[course_id]['duration_months'],
                    course_data['duration_months']
                )
            else:
                course_map[course_id] = course_data
        
        return list(course_map.values())
    
    def _create_enrollment_records(self, user, courses_to_enroll, order):
        """Create StudentEnrollment records"""
        enrollments = []
        
        products = {}
        for course_data in courses_to_enroll:
            product = course_data['product']
            if product.id not in products:
                products[product.id] = {
                    'product': product,
                    'duration_months': course_data['duration_months'],
                    'courses': []
                }
            products[product.id]['courses'].append(course_data)
        
        for product_data in products.values():
            product = product_data['product']
            
            enrollment, created = StudentEnrollment.objects.get_or_create(
                user=user,
                product=product,
                defaults={
                    'order': order,
                    'is_free': order.is_free_order(),
                    'starts_at': timezone.now(),
                    'expires_at': timezone.now() + timedelta(
                        days=product_data['duration_months'] * 30
                    ),
                    'is_active': True
                }
            )
            
            if not created:
                enrollment.extend_access(product_data['duration_months'])
            
            enrollments.append(enrollment)
        
        return enrollments
    
    def _sync_to_moodle(self, user, courses_to_enroll):
        """Sync enrollments to Moodle with immediate enrollment and account reactivation"""
        moodle_service = self._get_moodle_service()
        
        if not moodle_service:
            logger.warning("Moodle service unavailable")
            return
        
        try:
            from integration.models import MoodleUserMapping
            
            mapping, created = MoodleUserMapping.objects.get_or_create(
                cas_user=user,
                defaults={
                    'moodle_username': user.username,
                    'sync_status': 'pending'
                }
            )
            
            # Ensure user is verified before Moodle sync
            if not user.is_verified:
                logger.error(
                    f"User {user.username} is not verified - cannot sync to Moodle. "
                    f"Enrollment will be pending until user is verified."
                )
                # Mark enrollments as pending verification
                for course_data in courses_to_enroll:
                    enrollment, _ = CourseEnrollment.objects.get_or_create(
                        user=user,
                        course=course_data['course'],
                        defaults={
                            'is_active': False,  # Not active until verified
                            'sync_status': 'pending',
                            'sync_error': 'User not verified',
                            'enrolled_at': timezone.now()
                        }
                    )
                return

            # Sync user to Moodle if not already synced
            if not mapping.moodle_user_id or mapping.sync_status != 'synced':
                user_sync = moodle_service.sync_users(
                    initiated_by=user,
                    user_ids=[user.id]
                )
                logger.info(f"User sync for {user.username}: {user_sync.status}")
                
                # Refresh mapping to get moodle_user_id
                mapping.refresh_from_db()
                
                # Verify sync succeeded
                if not mapping.moodle_user_id:
                    logger.error(
                        f"User sync completed but no Moodle ID assigned for {user.username}. "
                        f"Sync status: {mapping.sync_status}"
                    )
                    return
            
            # Reactivate account if suspended
            if mapping.moodle_user_id:
                try:
                    moodle_service.client.update_user(
                        user_id=mapping.moodle_user_id,
                        user_data={'suspended': 0}
                    )
                    logger.info(f"✓ Ensured Moodle account active for {user.username}")
                except Exception as e:
                    logger.warning(f"Could not reactivate account for {user.username}: {str(e)}")
            
            for course_data in courses_to_enroll:
                course = course_data['course']
                
                enrollment, created = CourseEnrollment.objects.get_or_create(
                    user=user,
                    course=course,
                    defaults={
                        'is_active': True,
                        'sync_status': 'pending',
                        'enrolled_at': timezone.now()
                    }
                )
                
                course_data['order_item'].enrollment_log[str(course.id)] = {
                    'course_name': course.title,
                    'enrolled_at': timezone.now().isoformat(),
                    'terms': course_data['terms']
                }
                course_data['order_item'].save()
                
                try:
                    mapping = user.moodle_mapping
                    
                    if not mapping.moodle_user_id:
                        logger.error(f"User {user.username} has no Moodle ID, skipping enrollment")
                        continue
                    
                    if not course.moodle_course_id:
                        logger.error(f"Course {course.short_name} has no Moodle ID, skipping enrollment")
                        continue
                    
                    moodle_service.client.enrol_user(
                        mapping.moodle_user_id,
                        course.moodle_course_id
                    )
                    
                    enrollment.mark_synced()
                    
                    logger.info(
                        f"✓ Enrolled {user.username} in {course.short_name} "
                        f"(Moodle user {mapping.moodle_user_id}, course {course.moodle_course_id})"
                    )
                
                except Exception as e:
                    enrollment.mark_failed(str(e))
                    logger.error(f"Failed to enroll {user.username} in {course.short_name}: {str(e)}")
        
        except Exception as e:
            logger.error(f"Moodle sync failed: {str(e)}")
    
    def _send_enrollment_confirmation(self, user, enrollments, order):
        """Queue confirmation email as background task"""
        if not user.email:
            return
        
        from shop.tasks import send_enrollment_email_task
        send_enrollment_email_task.delay(user.id, order.id)
        logger.info(f"Queued enrollment confirmation email for {user.email}")
    
    def enroll_free_product(self, user, product):
        """Direct free enrollment without order"""
        if not product.is_free:
            raise ValueError("Product must be free")
        
        order = Order.objects.create(
            user=user,
            status=Order.FREE,
            total_amount=Decimal('0.00'),
            payment_method='free',
            paid_at=timezone.now()
        )
        
        OrderItem.objects.create(
            order=order,
            product=product,
            unit_price=Decimal('0.00'),
            quantity=1
        )
        
        order.calculate_totals()
        order.save()
        
        return self.process_order_enrollment(order)


class PaymentService:
    """Payment gateway integration"""
    
    def verify_paystack_webhook(self, request):
        """Verify Paystack webhook signature"""
        signature = request.headers.get('X-Paystack-Signature', '')
        
        secret = settings.PAYSTACK_SECRET_KEY.encode('utf-8')
        computed_sig = hmac.new(
            secret,
            request.body,
            hashlib.sha512
        ).hexdigest()
        
        if not hmac.compare_digest(signature, computed_sig):
            logger.warning("Invalid Paystack webhook signature")
            raise ValueError("Invalid webhook signature")
        
        import json
        return json.loads(request.body)
    
    @transaction.atomic
    def process_payment_confirmation(self, payment_reference, amount, metadata):
        """Process confirmed payment"""
        order_number = metadata.get('order_number')
        
        if not order_number:
            logger.error("No order_number in payment metadata")
            raise ValueError("Invalid payment metadata")
        
        try:
            order = Order.objects.select_for_update().get(order_number=order_number)
        except Order.DoesNotExist:
            logger.error(f"Order not found: {order_number}")
            raise
        
        expected_amount = int(order.total_amount * 100)
        if abs(amount - expected_amount) > 1:
            logger.error(f"Amount mismatch: expected {expected_amount}, got {amount}")
            raise ValueError("Payment amount mismatch")
        
        if order.status == Order.COMPLETED:
            logger.warning(f"Order {order_number} already completed")
            return order
        
        order.mark_paid(payment_reference=payment_reference, payment_method='paystack')
        
        enrollment_service = EnrollmentService()
        enrollment_service.process_order_enrollment(order)
        
        logger.info(f"Payment confirmed: {order_number} - R{order.total_amount}")
        
        return order


class MoodleProgressService:
    """Service for syncing progress from Moodle"""
    
    def __init__(self):
        """Initialize with active Moodle instance"""
        self.instance = MoodleInstance.objects.filter(
            is_active=True,
            is_primary=True
        ).first()
        
        if not self.instance:
            raise ValueError("No active Moodle instance configured")
        
        self.client = MoodleClient(
            base_url=self.instance.base_url,
            token=self.instance.ws_token
        )
    from integration.models import CourseEnrollment

    def sync_user_progress(self, user):
        """Sync all course progress for a user from Moodle"""
        result = {
            'success': False,
            'courses_synced': 0,
            'bundles_updated': 0,
            'errors': []
        }
        
        try:
            try:
                mapping = MoodleUserMapping.objects.get(cas_user=user)
            except MoodleUserMapping.DoesNotExist:
                result['errors'].append('User not synced to Moodle yet')
                return result
            
            if not mapping.moodle_user_id:
                result['errors'].append('Moodle user ID not set')
                return result
            
            course_enrollments = CourseEnrollment.objects.filter(
                user=user,
                is_active=True
            ).select_related('course')
            
            if not course_enrollments.exists():
                result['success'] = True
                result['errors'].append('No course enrollments found')
                return result
            
            course_progress_map = {}
            
            for enrollment in course_enrollments:
                try:
                    progress = self._fetch_course_progress(
                        mapping.moodle_user_id,
                        enrollment.course.moodle_course_id
                    )
                    
                    if progress is not None:
                        course_progress_map[enrollment.course.id] = progress
                        
                        enrollment.progress_percentage = Decimal(str(progress))
                        enrollment.sync_status = 'synced'
                        enrollment.last_synced = timezone.now()
                        enrollment.save()
                        
                        result['courses_synced'] += 1
                        
                        logger.info(
                            f"Synced progress for {user.username} in "
                            f"{enrollment.course.short_name}: {progress}%"
                        )
                
                except Exception as e:
                    error_msg = f"Error syncing {enrollment.course.short_name}: {str(e)}"
                    result['errors'].append(error_msg)
                    logger.error(error_msg)
            
            student_enrollments = StudentEnrollment.objects.filter(
                user=user,
                is_active=True
            ).select_related('product').prefetch_related('product__bundle_items__course')
            
            for enrollment in student_enrollments:
                product = enrollment.product
                
                if product.product_type == Product.BUNDLE:
                    bundle_courses = product.bundle_items.all()
                    
                    if bundle_courses.exists():
                        total_progress = Decimal('0.00')
                        course_count = 0
                        
                        for bundle_item in bundle_courses:
                            course_id = bundle_item.course.id
                            if course_id in course_progress_map:
                                total_progress += Decimal(str(course_progress_map[course_id]))
                                course_count += 1
                        
                        if course_count > 0:
                            avg_progress = total_progress / course_count
                            enrollment.progress_percentage = avg_progress
                            enrollment.last_accessed = timezone.now()
                            
                            if avg_progress >= 100:
                                enrollment.mark_completed()
                            
                            enrollment.save()
                            result['bundles_updated'] += 1
                            
                            logger.info(f"Updated bundle progress: {product.name} = {avg_progress:.1f}%")
                
                elif product.moodle_course:
                    course_id = product.moodle_course.id
                    
                    if course_id in course_progress_map:
                        progress = Decimal(str(course_progress_map[course_id]))
                        enrollment.progress_percentage = progress
                        enrollment.last_accessed = timezone.now()
                        
                        if progress >= 100:
                            enrollment.mark_completed()
                        
                        enrollment.save()
                        
                        logger.info(f"Updated course progress: {product.name} = {progress}%")
            
            # Clear cache
            cache.delete(f'user_progress:{user.id}')
            cache.delete(f'user_enrollments:{user.id}')
            
            result['success'] = True
            
        except Exception as e:
            result['errors'].append(f"Sync failed: {str(e)}")
            logger.error(f"Progress sync error for {user.username}: {str(e)}")
        
        return result
    
    def _fetch_course_progress(self, moodle_user_id, moodle_course_id):
        """Fetch course completion percentage from Moodle with caching"""
        if not moodle_course_id:
            return None
        
        # Try cache first
        cache_key = f'moodle_progress:{moodle_user_id}:{moodle_course_id}'
        cached_progress = cache.get(cache_key)
        
        if cached_progress is not None:
            logger.info(f"Cache hit for progress: user {moodle_user_id}, course {moodle_course_id}")
            return cached_progress
        
        progress = self._fetch_from_moodle(moodle_user_id, moodle_course_id)
        
        # Cache for 10 minutes
        cache.set(cache_key, progress, 600)
        
        return progress
    
    def _fetch_from_moodle(self, moodle_user_id, moodle_course_id):
        """
        Actual Moodle API calls with fallback hierarchy:
        1. Activity completion (if activities exist)
        2. Course progress field (from enrollment)
        3. Default to 0%
        """
        if not moodle_course_id:
            return None
        
        try:
            activity_progress = None
            course_progress = None
            
            # Method 1: Try activity completion
            try:
                activities_completion = self.client._make_request(
                    'core_completion_get_activities_completion_status',
                    {
                        'courseid': int(moodle_course_id),
                        'userid': int(moodle_user_id)
                    }
                )
                
                if activities_completion and 'statuses' in activities_completion:
                    statuses = activities_completion['statuses']
                    
                    # Only use activity completion if there are actual activities
                    if len(statuses) > 0:
                        total_activities = len(statuses)
                        completed_activities = sum(
                            1 for activity in statuses 
                            if activity.get('state', 0) == 1
                        )
                        
                        activity_progress = (completed_activities / total_activities) * 100
                        
                        logger.info(
                            f"Activity completion: {completed_activities}/{total_activities} = "
                            f"{activity_progress:.1f}% for user {moodle_user_id} in course {moodle_course_id}"
                        )
            
            except Exception as e:
                error_msg = str(e)
                if 'nocriteriaset' in error_msg.lower():
                    logger.info(f"Course {moodle_course_id} has no completion criteria")
                elif 'cannotviewreport' in error_msg.lower():
                    logger.warning(f"Permission denied for course {moodle_course_id}")
                else:
                    logger.error(f"Error fetching activity completion: {error_msg}")
            
            # Method 2: Get course progress from enrollment
            try:
                user_courses = self.client._make_request(
                    'core_enrol_get_users_courses',
                    {'userid': int(moodle_user_id)}
                )
                
                if user_courses:
                    for course in user_courses:
                        if course.get('id') == int(moodle_course_id):
                            # Check for progress field - handle None values
                            if 'progress' in course:
                                try:
                                    if course['progress'] is not None:
                                        course_progress = float(course['progress'])
                                        logger.info(
                                            f"Course progress from enrollment: {course_progress}% "
                                            f"for user {moodle_user_id} in course {moodle_course_id}"
                                        )
                                except (ValueError, TypeError) as e:
                                    logger.warning(f"Invalid progress value: {course['progress']}, error: {e}")
                            
                            # Check if marked as completed
                            if course.get('completed', False):
                                logger.info(f"Course {moodle_course_id} marked as completed")
                                return 100.0
            
            except Exception as e:
                logger.error(f"Error fetching user courses: {str(e)}")
            
            # Decision logic: Use most reliable data
            # Priority: activity_progress (if exists) > course_progress > 0%
            if activity_progress is not None:
                logger.info(f"Using activity completion: {activity_progress}%")
                return round(activity_progress, 2)
            elif course_progress is not None:
                logger.info(f"Using course progress field: {course_progress}%")
                return round(course_progress, 2)
            else:
                logger.info(
                    f"No progress data available for user {moodle_user_id} "
                    f"in course {moodle_course_id} - defaulting to 0%"
                )
                return 0.0
            
        except Exception as e:
            logger.error(
                f"Error fetching Moodle progress for course {moodle_course_id}, "
                f"user {moodle_user_id}: {str(e)}"
            )
        return 0.0
    


class EnrollmentReconciliationService:
    """Reconciles enrollment state between Django and Moodle"""
    
    def __init__(self):
        """Initialize with Moodle service"""
        instance = MoodleInstance.objects.filter(
            is_active=True,
            is_primary=True
        ).first()
        
        if not instance:
            raise ValueError("No active Moodle instance configured")
        
        self.moodle_service = MoodleIntegrationService(instance)
        self.client = self.moodle_service.client
    
    def reconcile_all_enrollments(self, initiated_by=None):
        """Full reconciliation of all user enrollments"""
        logger.info("Starting full enrollment reconciliation")
        
        results = {
            'users_processed': 0,
            'enrollments_added': 0,
            'enrollments_removed': 0,
            'accounts_deactivated': 0,
            'accounts_reactivated': 0,
            'errors': []
        }
        
        users_with_moodle = User.objects.filter(
            role=User.STUDENT,
            moodle_mapping__isnull=False,
            moodle_mapping__moodle_user_id__isnull=False
        ).select_related('moodle_mapping')
        
        for user in users_with_moodle:
            try:
                user_result = self._reconcile_user_enrollments(user)
                
                results['users_processed'] += 1
                results['enrollments_added'] += user_result['added']
                results['enrollments_removed'] += user_result['removed']
                
                if user_result['deactivated']:
                    results['accounts_deactivated'] += 1
                elif user_result['reactivated']:
                    results['accounts_reactivated'] += 1
                
            except Exception as e:
                error_msg = f"Error reconciling {user.username}: {str(e)}"
                results['errors'].append(error_msg)
                logger.error(error_msg)
        
        # Clear all user caches
        cache.delete_pattern('user_courses:*')
        cache.delete_pattern('user_enrollments:*')
        
        logger.info(
            f"Reconciliation complete: {results['users_processed']} users, "
            f"{results['enrollments_added']} added, "
            f"{results['enrollments_removed']} removed, "
            f"{results['accounts_deactivated']} deactivated"
        )
        
        return results
    
    def _reconcile_user_enrollments(self, user):
        """Reconcile enrollments for a single user"""
        result = {
            'added': 0,
            'removed': 0,
            'deactivated': False,
            'reactivated': False
        }
        
        mapping = user.moodle_mapping
        moodle_user_id = mapping.moodle_user_id
        
        should_have_access = self._get_courses_user_should_access(user)
        should_have_course_ids = set(should_have_access.keys())
        
        try:
            moodle_courses = self.client.get_user_courses(moodle_user_id)
            has_course_ids = {c['id'] for c in moodle_courses}
        except Exception as e:
            logger.error(f"Failed to get Moodle courses for {user.username}: {str(e)}")
            has_course_ids = set()
        
        to_enroll = should_have_course_ids - has_course_ids
        for course_id in to_enroll:
            try:
                self.client.enrol_user(moodle_user_id, course_id)
                result['added'] += 1
                logger.info(f"✓ Enrolled {user.username} in course {course_id}")
            except Exception as e:
                logger.error(f"Failed to enroll {user.username} in {course_id}: {str(e)}")
        
        to_unenroll = has_course_ids - should_have_course_ids
        for course_id in to_unenroll:
            try:
                self.client.unenrol_user(moodle_user_id, course_id)
                result['removed'] += 1
                logger.info(f"✓ Unenrolled {user.username} from course {course_id}")
                
                try:
                    course = Course.objects.get(moodle_course_id=course_id)
                    course_enrollment = CourseEnrollment.objects.filter(
                        user=user,
                        course=course
                    ).first()
                    
                    if course_enrollment and course_enrollment.is_active:
                        course_enrollment.is_active = False
                        course_enrollment.sync_status = 'synced'
                        course_enrollment.save()
                        logger.info(f"✓ Deactivated CourseEnrollment in Django for {user.username} → {course.short_name}")
                
                except Course.DoesNotExist:
                    logger.warning(f"Course with Moodle ID {course_id} not found in Django")
                except Exception as ce_error:
                    logger.error(f"Error deactivating CourseEnrollment: {str(ce_error)}")
            
            except Exception as e:
                logger.error(f"Failed to unenroll {user.username} from {course_id}: {str(e)}")
        
        if len(should_have_course_ids) == 0:
            if self._deactivate_moodle_account(user, moodle_user_id):
                result['deactivated'] = True
        else:
            if self._reactivate_moodle_account(user, moodle_user_id):
                result['reactivated'] = True
        
        return result
    
    def _get_courses_user_should_access(self, user):
        """Get all courses user should have access to based on active StudentEnrollments"""
        courses = {}
        
        active_enrollments = StudentEnrollment.objects.filter(
            user=user,
            is_active=True
        ).select_related('product').prefetch_related('product__bundle_items__course')
        
        for enrollment in active_enrollments:
            product = enrollment.product
            
            if product.product_type == Product.BUNDLE:
                for bundle_item in product.bundle_items.all():
                    if bundle_item.course.moodle_course_id:
                        courses[bundle_item.course.moodle_course_id] = bundle_item.course
            
            elif product.moodle_course and product.moodle_course.moodle_course_id:
                courses[product.moodle_course.moodle_course_id] = product.moodle_course
        
        return courses
    
    def _deactivate_moodle_account(self, user, moodle_user_id):
        """Deactivate Moodle user account (suspend, don't delete)"""
        try:
            self.client.update_user(
                user_id=moodle_user_id,
                user_data={'suspended': 1}
            )
            
            logger.info(f"✓ Deactivated Moodle account for {user.username} (no active enrollments)")
            return True
            
        except Exception as e:
            logger.error(f"Failed to deactivate Moodle account for {user.username}: {str(e)}")
            return False
    
    def _reactivate_moodle_account(self, user, moodle_user_id):
        """Reactivate Moodle user account if suspended"""
        try:
            moodle_user = self.client._make_request(
                'core_user_get_users_by_field',
                {
                    'field': 'id',
                    'values': [moodle_user_id]
                }
            )
            
            if moodle_user and len(moodle_user) > 0:
                user_data = moodle_user[0]
                
                if user_data.get('suspended', 0) == 1:
                    self.client.update_user(
                        user_id=moodle_user_id,
                        user_data={'suspended': 0}
                    )
                    
                    logger.info(f"✓ Reactivated Moodle account for {user.username}")
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to check/reactivate Moodle account for {user.username}: {str(e)}")
            return False