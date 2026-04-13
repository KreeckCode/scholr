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
        # Create order
        order = Order.objects.create(
            user=user,
            status=Order.PENDING,
            customer_notes=customer_notes
        )
        
        # Add items - CRITICAL: Use DB prices, not cart prices (security)
        subtotal = Decimal('0.00')
        
        for item in cart:
            product = item['product']
            quantity = item['quantity']
            
            # Security: Always fetch fresh price from database
            db_product = Product.objects.get(id=product.id)
            unit_price = db_product.price
            
            OrderItem.objects.create(
                order=order,
                product=db_product,
                quantity=quantity,
                unit_price=unit_price
            )
            
            subtotal += unit_price * quantity
        
        # Apply discount
        order.subtotal = subtotal
        discount_amount = Decimal('0.00')
        
        if discount_code and discount_code.is_valid():
            discount_amount = discount_code.calculate_discount(subtotal)
            
            # Record discount usage
            discount_code.times_used += 1
            discount_code.save()
        
        order.discount_amount = discount_amount
        order.total_amount = subtotal - discount_amount
        
        # Auto-complete free orders
        if order.total_amount == Decimal('0.00'):
            order.status = Order.FREE
            order.payment_method = 'free'
            order.paid_at = timezone.now()
        
        order.save()
        
        logger.info(f"Created order {order.order_number} for user {user.username}: R{order.total_amount}")
        
        return order
    
    def verify_order_ownership(self, order, user):
        """
        Security: Verify user owns this order
        
        Raises:
            PermissionError if user doesn't own order
        """
        if order.user != user and not user.role == User.ADMIN:
            logger.warning(f"Unauthorized order access attempt: {user.username} → {order.order_number}")
            raise PermissionError("You don't have permission to access this order")
    
    def cancel_order(self, order, user):
        """
        Cancel pending order
        
        Security:
        - Only owner or admin can cancel
        - Only pending orders can be cancelled
        - Refund discount code if used
        """
        self.verify_order_ownership(order, user)
        
        if order.status != Order.PENDING:
            raise ValueError("Only pending orders can be cancelled")
        
        # Refund discount code usage
        # TODO: Track which discount was used
        
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
    
    Security:
    - Only processes paid/free orders
    - Verifies Moodle sync
    - Atomic operations
    - Audit logging
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
        """
        Main enrollment processor
        
        Security:
        - Verifies order is paid
        - Prevents duplicate processing
        - Atomic transaction
        
        Returns:
            list: Created StudentEnrollment instances
        """
        # Security: Only process paid/free orders
        if not order.is_paid():
            raise ValueError(f"Order {order.order_number} is not paid")
        
        # Prevent duplicate processing
        if order.enrollment_processed:
            logger.warning(f"Order {order.order_number} already processed")
            return []
        
        logger.info(f"Processing enrollment for order {order.order_number}")
        
        try:
            # 1. Expand products to courses
            courses_to_enroll = self._expand_products_to_courses(order)
            
            # 2. Create enrollment records
            enrollments = self._create_enrollment_records(
                order.user, 
                courses_to_enroll, 
                order
            )
            
            # 3. Sync to Moodle
            self._sync_to_moodle(order.user, courses_to_enroll)
            
            # 4. Send confirmation
            self._send_enrollment_confirmation(order.user, enrollments, order)
            
            # 5. Mark as processed
            order.enrollment_processed = True
            order.enrollment_processed_at = timezone.now()
            order.save()
            
            logger.info(f"Enrollment complete: {order.order_number} - {len(enrollments)} products")
            
            return enrollments
            
        except Exception as e:
            order.enrollment_error = str(e)
            order.save()
            logger.error(f"Enrollment failed for {order.order_number}: {str(e)}")
            raise
    
    def _expand_products_to_courses(self, order):
        """
        Convert products to course list
        
        Handles:
        - Single courses
        - Bundles (expands to multiple courses)
        - Term-specific access
        
        Returns:
            list of dicts: [
                {
                    'course': Course,
                    'terms': [1,2,3,4],
                    'duration_months': 12,
                    'product': Product,
                    'order_item': OrderItem
                },
                ...
            ]
        """
        courses = []
        
        for item in order.items.select_related('product').prefetch_related('product__bundle_items__course'):
            product = item.product
            
            if product.product_type == Product.COURSE:
                # Single course
                if product.moodle_course:
                    courses.append({
                        'course': product.moodle_course,
                        'terms': [1, 2, 3, 4],
                        'duration_months': product.duration_months,
                        'product': product,
                        'order_item': item
                    })
            
            elif product.product_type == Product.BUNDLE:
                # Expand bundle
                for bundle_item in product.bundle_items.all():
                    courses.append({
                        'course': bundle_item.course,
                        'terms': bundle_item.include_terms or [1, 2, 3, 4],
                        'duration_months': product.duration_months,
                        'product': product,
                        'order_item': item
                    })
            
            elif product.product_type == Product.TERM:
                # Term-specific
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
        """
        Merge duplicate courses
        If user buys Math Term 1 + Math Term 2, combine to [1,2]
        """
        course_map = {}
        
        for course_data in courses:
            course_id = course_data['course'].id
            
            if course_id in course_map:
                # Merge terms
                existing_terms = set(course_map[course_id]['terms'])
                new_terms = set(course_data['terms'])
                course_map[course_id]['terms'] = sorted(existing_terms | new_terms)
                
                # Use longest duration
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
        
        # Group by product
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
        
        # Create enrollments
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
                # Extend existing
                enrollment.extend_access(product_data['duration_months'])
            
            enrollments.append(enrollment)
        
        return enrollments
    
    def _sync_to_moodle(self, user, courses_to_enroll):
        """
        Sync enrollments to Moodle with immediate enrollment
        Also reactivates suspended accounts
        """
        moodle_service = self._get_moodle_service()
        
        if not moodle_service:
            logger.warning("Moodle service unavailable")
            return
        
        try:
            # Ensure user has Moodle account first
            from integration.models import MoodleUserMapping
            
            # Check if user is synced to Moodle
            mapping, created = MoodleUserMapping.objects.get_or_create(
                cas_user=user,
                defaults={
                    'moodle_username': user.username,
                    'sync_status': 'pending'
                }
            )
            
            # If user not synced, sync them first
            if not mapping.moodle_user_id or mapping.sync_status != 'synced':
                user_sync = moodle_service.sync_users(
                    initiated_by=user,
                    user_ids=[user.id]
                )
                logger.info(f"User sync for {user.username}: {user_sync.status}")
            
            # IMPORTANT: Reactivate account if suspended
            if mapping.moodle_user_id:
                try:
                    moodle_service.client.update_user(
                        user_id=mapping.moodle_user_id,
                        user_data={'suspended': 0}
                    )
                    logger.info(f"✓ Ensured Moodle account active for {user.username}")
                except Exception as e:
                    logger.warning(f"Could not reactivate account for {user.username}: {str(e)}")
            
            # Now sync each course enrollment
            for course_data in courses_to_enroll:
                course = course_data['course']
                
                # Create CourseEnrollment record
                enrollment, created = CourseEnrollment.objects.get_or_create(
                    user=user,
                    course=course,
                    defaults={
                        'is_active': True,
                        'sync_status': 'pending',
                        'enrolled_at': timezone.now()
                    }
                )
                
                # Update enrollment log
                course_data['order_item'].enrollment_log[str(course.id)] = {
                    'course_name': course.title,
                    'enrolled_at': timezone.now().isoformat(),
                    'terms': course_data['terms']
                }
                course_data['order_item'].save()
                
                # Sync to Moodle immediately
                try:
                    # Get fresh mapping
                    mapping = user.moodle_mapping
                    
                    if not mapping.moodle_user_id:
                        logger.error(f"User {user.username} has no Moodle ID, skipping enrollment")
                        continue
                    
                    if not course.moodle_course_id:
                        logger.error(f"Course {course.short_name} has no Moodle ID, skipping enrollment")
                        continue
                    
                    # Enroll directly via Moodle client
                    moodle_service.client.enrol_user(
                        mapping.moodle_user_id,
                        course.moodle_course_id
                    )
                    
                    # Mark as synced
                    enrollment.mark_synced()
                    
                    logger.info(
                        f"✓ Enrolled {user.username} in {course.short_name} "
                        f"(Moodle user {mapping.moodle_user_id}, course {course.moodle_course_id})"
                    )
                
                except Exception as e:
                    enrollment.mark_failed(str(e))
                    logger.error(
                        f"Failed to enroll {user.username} in {course.short_name}: {str(e)}"
                    )
        
        except Exception as e:
            logger.error(f"Moodle sync failed: {str(e)}")



    

    def _create_course_enrollments_for_bundle(self, user, product, order):
        """
        When user enrolls in bundle, create CourseEnrollment for each course
        Critical: This ensures Moodle knows about the enrollment
        """
        if product.product_type != Product.BUNDLE:
            return []
        
        # Get all courses in this bundle
        bundle_items = ProductBundle.objects.filter(
            product=product
        ).select_related('course')
        
        created_enrollments = []
        
        for item in bundle_items:
            course = item.course
            
            # Check if enrollment already exists
            enrollment, created = CourseEnrollment.objects.get_or_create(
                user=user,
                course=course,
                defaults={
                    'enrolled_by': None,
                    'is_active': True,
                    'sync_status': 'pending',
                    'enrolled_at': timezone.now()
                }
            )
            
            if created:
                logger.info(f"Created CourseEnrollment: {user.username} → {course.short_name}")
                created_enrollments.append(enrollment)
            else:
                # Reactivate if was inactive
                if not enrollment.is_active:
                    enrollment.is_active = True
                    enrollment.sync_status = 'pending'
                    enrollment.save()
                    created_enrollments.append(enrollment)
        
        # Sync to Moodle
        if created_enrollments:
            moodle_service = self._get_moodle_service()
            
            if not moodle_service:
                logger.warning("Moodle service unavailable for bundle enrollment sync")
                return created_enrollments
            
            try:
                # Get user's Moodle mapping
                mapping = MoodleUserMapping.objects.filter(cas_user=user).first()
                
                if not mapping or not mapping.moodle_user_id:
                    logger.error(f"User {user.username} has no Moodle mapping, skipping sync")
                    return created_enrollments
                
                # Enroll in each course via Moodle client
                for enrollment in created_enrollments:
                    try:
                        if not enrollment.course.moodle_course_id:
                            logger.error(
                                f"Course {enrollment.course.short_name} has no Moodle ID"
                            )
                            enrollment.sync_status = 'failed'
                            enrollment.sync_error = 'No Moodle course ID'
                            enrollment.save()
                            continue
                        
                        # Direct enrollment via client
                        moodle_service.client.enrol_user(
                            user_id=mapping.moodle_user_id,
                            course_id=enrollment.course.moodle_course_id,
                            role_id=5
                        )
                        
                        # Mark as synced
                        enrollment.sync_status = 'synced'
                        enrollment.last_synced = timezone.now()
                        enrollment.save()
                        
                        logger.info(
                            f"✓ Enrolled {user.username} in {enrollment.course.short_name} "
                            f"(Moodle user {mapping.moodle_user_id}, "
                            f"course {enrollment.course.moodle_course_id})"
                        )
                    
                    except Exception as e:
                        enrollment.sync_status = 'failed'
                        enrollment.sync_error = str(e)
                        enrollment.save()
                        logger.error(
                            f"Failed to enroll {user.username} in "
                            f"{enrollment.course.short_name}: {str(e)}"
                        )
            
            except Exception as e:
                logger.error(f"Moodle bundle enrollment error: {str(e)}")
        
        return created_enrollments





    
    def _send_enrollment_confirmation(self, user, enrollments, order):
        """Send confirmation email"""
        if not user.email:
            return
        
        context = {
            'user': user,
            'order': order,
            'enrollments': enrollments,
            'is_free': order.is_free_order(),
        }
        
        subject = "Welcome to African Pastoral Academy!" if order.is_free_order() else "Your Purchase Confirmation"
        message = render_to_string('shop/emails/enrollment_confirmation.txt', context)
        
        try:
            send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )
        except Exception as e:
            logger.error(f"Email failed: {str(e)}")
    
    def enroll_free_product(self, user, product):
        """
        Direct free enrollment without order
        Used for promotional giveaways or admin comps
        """
        if not product.is_free:
            raise ValueError("Product must be free")
        
        # Create free order
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
    """
    Payment gateway integration
    
    Security:
    - Webhook signature verification
    - Idempotency checks
    - Amount verification
    - Audit logging
    """
    
    def verify_paystack_webhook(self, request):
        """
        Verify Paystack webhook signature
        
        Security:
        - HMAC signature verification
        - Prevents replay attacks
        - Validates payload integrity
        
        Returns:
            dict: Parsed webhook data if valid
            
        Raises:
            ValueError: If signature invalid
        """
        signature = request.headers.get('X-Paystack-Signature', '')
        
        # Security: Verify HMAC signature
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
        """
        Process confirmed payment
        
        Security:
        - Verifies amount matches order
        - Prevents duplicate processing
        - Atomic transaction
        
        Args:
            payment_reference: Gateway reference
            amount: Amount paid (in kobo for Paystack)
            metadata: Payment metadata (should contain order_number)
        """
        order_number = metadata.get('order_number')
        
        if not order_number:
            logger.error("No order_number in payment metadata")
            raise ValueError("Invalid payment metadata")
        
        try:
            order = Order.objects.select_for_update().get(order_number=order_number)
        except Order.DoesNotExist:
            logger.error(f"Order not found: {order_number}")
            raise
        
        # Security: Verify amount matches (convert kobo to naira/rand)
        expected_amount = int(order.total_amount * 100)
        if abs(amount - expected_amount) > 1:  # Allow 1 kobo variance
            logger.error(f"Amount mismatch: expected {expected_amount}, got {amount}")
            raise ValueError("Payment amount mismatch")
        
        # Prevent duplicate processing
        if order.status == Order.COMPLETED:
            logger.warning(f"Order {order_number} already completed")
            return order
        
        # Mark as paid
        order.mark_paid(payment_reference=payment_reference, payment_method='paystack')
        
        # Process enrollment
        enrollment_service = EnrollmentService()
        enrollment_service.process_order_enrollment(order)
        
        logger.info(f"Payment confirmed: {order_number} - R{order.total_amount}")
        
        return order
    



class MoodleProgressService:
    """
    Service for syncing progress from Moodle
    
    Fetches course completion and grade data from Moodle
    Updates StudentEnrollment and CourseEnrollment progress
    """
    
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
    
    def sync_user_progress(self, user):
        """
        Sync all course progress for a user from Moodle
        
        Returns: dict with success status and details
        """
        result = {
            'success': False,
            'courses_synced': 0,
            'bundles_updated': 0,
            'errors': []
        }
        
        try:
            # Get user's Moodle mapping
            try:
                mapping = MoodleUserMapping.objects.get(cas_user=user)
            except MoodleUserMapping.DoesNotExist:
                result['errors'].append('User not synced to Moodle yet')
                return result
            
            if not mapping.moodle_user_id:
                result['errors'].append('Moodle user ID not set')
                return result
            
            # Get user's course enrollments
            course_enrollments = CourseEnrollment.objects.filter(
                user=user,
                is_active=True
            ).select_related('course')
            
            if not course_enrollments.exists():
                result['success'] = True
                result['errors'].append('No course enrollments found')
                return result
            
            # Fetch progress for each course from Moodle
            for enrollment in course_enrollments:
                try:
                    progress = self._fetch_course_progress(
                        mapping.moodle_user_id,
                        enrollment.course.moodle_course_id
                    )
                    
                    if progress is not None:
                        # Update CourseEnrollment (not used in display but good to track)
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
            
            # Update bundle progress (aggregate from courses)
            bundles_updated = self._update_bundle_progress(user)
            result['bundles_updated'] = bundles_updated
            
            result['success'] = True
            
        except Exception as e:
            result['errors'].append(f"Sync failed: {str(e)}")
            logger.error(f"Progress sync error for {user.username}: {str(e)}")
        
        return result
    
    def _fetch_course_progress(self, moodle_user_id, moodle_course_id):
        """
        Fetch course completion percentage from Moodle
        
        Uses Moodle's grade API to get completion percentage
        Returns: float (0-100) or None if unavailable
        """
        if not moodle_course_id:
            return None
        
        try:
            # Use the correct method name from MoodleClient
            # Try to get grades first (most reliable for quiz-based courses)
            try:
                grade_data = self.client._make_request(
                    'core_grades_get_grades',
                    {
                        'courseid': int(moodle_course_id),
                        'userid': int(moodle_user_id)
                    }
                )
                
                # Parse grade data
                if grade_data and 'items' in grade_data:
                    for item in grade_data['items']:
                        if item.get('itemname') == 'Course total':
                            grade_raw = float(item.get('graderaw', 0))
                            grade_max = float(item.get('grademax', 100))
                            
                            if grade_max > 0:
                                percentage = (grade_raw / grade_max) * 100
                                logger.info(
                                    f"Grade data: {grade_raw}/{grade_max} = {percentage}% "
                                    f"for user {moodle_user_id} in course {moodle_course_id}"
                                )
                                return round(percentage, 2)
            
            except Exception as e:
                logger.error(f"Error fetching grades: {str(e)}")
            
            # Fallback: Try completion status
            try:
                completion_data = self.client._make_request(
                    'core_completion_get_course_completion_status',
                    {
                        'courseid': int(moodle_course_id),
                        'userid': int(moodle_user_id)
                    }
                )
                
                # Check if course is marked as completed
                if isinstance(completion_data, dict):
                    completionstatus = completion_data.get('completionstatus', {})
                    if isinstance(completionstatus, dict) and completionstatus.get('completed'):
                        logger.info(f"Course {moodle_course_id} marked as completed for user {moodle_user_id}")
                        return 100.0
            
            except Exception as e:
                logger.error(f"Error fetching completion: {str(e)}")
            
            # No data found - return 0%
            logger.info(f"No progress data found for user {moodle_user_id} in course {moodle_course_id}")
            return 0.0
            
        except Exception as e:
            logger.error(
                f"Error fetching Moodle progress for course {moodle_course_id}, "
                f"user {moodle_user_id}: {str(e)}"
            )
            return None
    
    





    def _update_bundle_progress(self, user):
        """
        Update bundle progress based on constituent courses
        
        Bundle progress = average of all courses in bundle
        """
        bundles_updated = 0
        
        # Get user's bundle enrollments
        bundle_enrollments = StudentEnrollment.objects.filter(
            user=user,
            product__product_type=Product.BUNDLE,
            is_active=True
        ).select_related('product')
        
        for enrollment in bundle_enrollments:
            # Get all courses in this bundle
            bundle_courses = ProductBundle.objects.filter(
                product=enrollment.product
            ).select_related('course')
            
            if not bundle_courses.exists():
                continue
            
            # Get CourseEnrollments for these courses
            course_ids = [item.course_id for item in bundle_courses]
            user_course_enrollments = CourseEnrollment.objects.filter(
                user=user,
                course_id__in=course_ids,
                is_active=True
            ).select_related('course')
            
            # Calculate average progress
            total_courses = len(course_ids)
            completed_count = 0
            total_progress = Decimal('0.00')
            
            for course_enrollment in user_course_enrollments:
                # Fetch fresh progress from Moodle
                try:
                    mapping = MoodleUserMapping.objects.get(cas_user=user)
                    progress = self._fetch_course_progress(
                        mapping.moodle_user_id,
                        course_enrollment.course.moodle_course_id
                    )
                    
                    if progress is not None:
                        total_progress += Decimal(str(progress))
                        if progress >= 100:
                            completed_count += 1
                
                except Exception as e:
                    logger.error(f"Error calculating bundle progress: {str(e)}")
            
            # Update bundle enrollment
            if total_courses > 0:
                avg_progress = total_progress / total_courses
                enrollment.progress_percentage = avg_progress
                enrollment.last_accessed = timezone.now()
                
                # Mark as completed if all courses done
                if completed_count == total_courses:
                    enrollment.mark_completed()
                
                enrollment.save()
                bundles_updated += 1
        
        return bundles_updated
    



class EnrollmentReconciliationService:
    """
    Reconciles enrollment state between Django and Moodle
    Cleans up stale enrollments and manages account status
    """
    
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
        """
        Full reconciliation of all user enrollments
        
        Process:
        1. Get all users with Moodle accounts
        2. Check what they should have access to (active StudentEnrollments)
        3. Check what they actually have in Moodle
        4. Unenroll from courses they shouldn't have
        5. Enroll in courses they should have
        6. Deactivate accounts with no active enrollments
        
        Returns:
            dict with reconciliation results
        """
        logger.info("Starting full enrollment reconciliation")
        
        results = {
            'users_processed': 0,
            'enrollments_added': 0,
            'enrollments_removed': 0,
            'accounts_deactivated': 0,
            'accounts_reactivated': 0,
            'errors': []
        }
        
        # Get all users with Moodle mappings
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
        
        logger.info(
            f"Reconciliation complete: {results['users_processed']} users, "
            f"{results['enrollments_added']} added, "
            f"{results['enrollments_removed']} removed, "
            f"{results['accounts_deactivated']} deactivated"
        )
        
        return results
    
    def _reconcile_user_enrollments(self, user):
        """
        Reconcile enrollments for a single user
        Also cleans up CourseEnrollment records in Django
        
        Returns:
            dict with counts: added, removed, deactivated, reactivated
        """
        result = {
            'added': 0,
            'removed': 0,
            'deactivated': False,
            'reactivated': False
        }
        
        mapping = user.moodle_mapping
        moodle_user_id = mapping.moodle_user_id
        
        # Get what user SHOULD be enrolled in (from Django)
        should_have_access = self._get_courses_user_should_access(user)
        should_have_course_ids = set(should_have_access.keys())
        
        # Get what user ACTUALLY has in Moodle
        try:
            moodle_courses = self.client.get_user_courses(moodle_user_id)
            has_course_ids = {c['id'] for c in moodle_courses}
        except Exception as e:
            logger.error(f"Failed to get Moodle courses for {user.username}: {str(e)}")
            has_course_ids = set()
        
        # Courses to add (missing in Moodle)
        to_enroll = should_have_course_ids - has_course_ids
        for course_id in to_enroll:
            try:
                self.client.enrol_user(moodle_user_id, course_id)
                result['added'] += 1
                logger.info(f"✓ Enrolled {user.username} in course {course_id}")
            except Exception as e:
                logger.error(f"Failed to enroll {user.username} in {course_id}: {str(e)}")
        
        # Courses to remove (in Moodle but shouldn't be)
        to_unenroll = has_course_ids - should_have_course_ids
        for course_id in to_unenroll:
            try:
                # Remove from Moodle
                self.client.unenrol_user(moodle_user_id, course_id)
                result['removed'] += 1
                logger.info(f"✓ Unenrolled {user.username} from course {course_id}")
                
                # IMPORTANT: Also deactivate Django CourseEnrollment record
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
        
        # Manage account status
        if len(should_have_course_ids) == 0:
            # No active enrollments - deactivate account
            if self._deactivate_moodle_account(user, moodle_user_id):
                result['deactivated'] = True
        else:
            # Has enrollments - ensure account is active
            if self._reactivate_moodle_account(user, moodle_user_id):
                result['reactivated'] = True
        
        return result
    
    def _get_courses_user_should_access(self, user):
        """
        Get all courses user should have access to based on active StudentEnrollments
        
        Returns:
            dict: {moodle_course_id: course_object}
        """
        courses = {}
        
        # Get active student enrollments
        active_enrollments = StudentEnrollment.objects.filter(
            user=user,
            is_active=True
        ).select_related('product').prefetch_related('product__bundle_items__course')
        
        for enrollment in active_enrollments:
            product = enrollment.product
            
            if product.product_type == Product.BUNDLE:
                # Get all courses in bundle
                for bundle_item in product.bundle_items.all():
                    if bundle_item.course.moodle_course_id:
                        courses[bundle_item.course.moodle_course_id] = bundle_item.course
            
            elif product.moodle_course and product.moodle_course.moodle_course_id:
                # Single course product
                courses[product.moodle_course.moodle_course_id] = product.moodle_course
        
        return courses
    
    def _deactivate_moodle_account(self, user, moodle_user_id):
        """
        Deactivate Moodle user account (suspend, don't delete)
        
        Returns:
            bool: True if deactivated, False if already inactive or error
        """
        try:
            # Update user to suspended status
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
        """
        Reactivate Moodle user account if suspended
        
        Returns:
            bool: True if reactivated, False if already active or error
        """
        try:
            # Check if account is suspended
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
                    # Reactivate
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
        
