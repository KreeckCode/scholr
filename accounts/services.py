"""
Enrollment and product services for Judea Harvest
Handles order processing and automatic enrollment into Moodle courses
"""

import logging
from typing import List, Dict
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from django.core.mail import send_mail
from django.conf import settings

from .models import (
    User, Order, OrderItem, Product, ProductBundle, 
    StudentEnrollment
)
from integration.models import CourseEnrollment
from integration.services import MoodleIntegrationService

logger = logging.getLogger(__name__)


class EnrollmentService:
    """
    Handles product purchases and automatic enrollment
    Works for both FREE and PAID courses
    """
    
    def process_order_enrollment(self, order: Order) -> List[StudentEnrollment]:
        """
        Main entry point after payment confirmation (or immediate for free orders)
        
        Args:
            order: Order instance (can be free or paid)
            
        Returns:
            List of StudentEnrollment instances created
        """
        enrollments = []
        
        try:
            with transaction.atomic():
                # Expand products into courses
                courses_to_enroll = self._expand_products_to_courses(order)
                
                logger.info(f"Processing enrollment for order {order.order_number}: {len(courses_to_enroll)} courses")
                
                # Create enrollment records
                for course_data in courses_to_enroll:
                    enrollment = self._create_enrollment_record(
                        user=order.user,
                        product=course_data['product'],
                        course=course_data['course'],
                        terms=course_data['terms'],
                        duration_months=course_data['duration_months'],
                        order=order
                    )
                    enrollments.append(enrollment)
                
                # Sync all enrollments to Moodle
                self._sync_to_moodle(order.user, courses_to_enroll)
                
                # Mark order as enrollment processed
                order.enrollment_processed = True
                order.enrollment_processed_at = timezone.now()
                order.save()
                
                # Send confirmation email
                self._send_enrollment_confirmation(order.user, enrollments, order)
                
                logger.info(f"Successfully enrolled {order.user.username} in {len(enrollments)} courses")
                
        except Exception as e:
            logger.error(f"Enrollment failed for order {order.order_number}: {str(e)}", exc_info=True)
            order.enrollment_error = str(e)
            order.save()
            raise
        
        return enrollments
    
    def _expand_products_to_courses(self, order: Order) -> List[Dict]:
        """
        Convert purchased products into list of courses to enroll
        
        Handles:
        - Single courses
        - Bundles (multiple courses)
        - Term-specific products
        
        Returns:
            List of dicts with course enrollment details
        """
        courses = []
        
        for item in order.items.all():
            product = item.product
            
            if product.product_type == Product.COURSE:
                # Single course - all terms
                if product.moodle_course:
                    courses.append({
                        'product': product,
                        'course': product.moodle_course,
                        'terms': [1, 2, 3, 4],  # All terms by default
                        'duration_months': product.duration_months
                    })
                else:
                    logger.warning(f"Product {product.name} has no linked Moodle course")
            
            elif product.product_type == Product.BUNDLE:
                # Bundle - expand to all included courses
                bundle_items = product.bundle_items.all()
                
                for bundle_item in bundle_items:
                    courses.append({
                        'product': product,
                        'course': bundle_item.course,
                        'terms': bundle_item.include_terms or [1, 2, 3, 4],
                        'duration_months': product.duration_months
                    })
            
            elif product.product_type == Product.TERM:
                # Term-specific access
                if product.moodle_course and product.term_number:
                    courses.append({
                        'product': product,
                        'course': product.moodle_course,
                        'terms': [product.term_number],
                        'duration_months': product.duration_months
                    })
        
        # Remove duplicates (if student bought overlapping products)
        return self._deduplicate_courses(courses)
    
    def _deduplicate_courses(self, courses: List[Dict]) -> List[Dict]:
        """
        Remove duplicate course enrollments
        If same course appears multiple times, merge term access
        """
        course_map = {}
        
        for course_data in courses:
            course_id = course_data['course'].id
            
            if course_id in course_map:
                # Merge terms (union of both)
                existing_terms = set(course_map[course_id]['terms'])
                new_terms = set(course_data['terms'])
                course_map[course_id]['terms'] = sorted(existing_terms | new_terms)
                
                # Use longer duration
                course_map[course_id]['duration_months'] = max(
                    course_map[course_id]['duration_months'],
                    course_data['duration_months']
                )
            else:
                course_map[course_id] = course_data
        
        return list(course_map.values())
    
    def _create_enrollment_record(
        self, 
        user: User, 
        product: Product,
        course,
        terms: List[int],
        duration_months: int,
        order: Order
    ) -> StudentEnrollment:
        """
        Create StudentEnrollment record
        Also creates integration.CourseEnrollment for Moodle sync
        """
        # Calculate access period
        starts_at = timezone.now()
        expires_at = starts_at + timedelta(days=duration_months * 30)
        
        # Create or update StudentEnrollment
        enrollment, created = StudentEnrollment.objects.update_or_create(
            user=user,
            product=product,
            defaults={
                'order': order,
                'starts_at': starts_at,
                'expires_at': expires_at,
                'is_active': True,
                'is_free': product.is_free,
            }
        )
        
        # If updating existing enrollment, extend access
        if not created:
            enrollment.extend_access(months=duration_months)
            logger.info(f"Extended access for {user.username} to {product.name}")
        
        # Create integration.CourseEnrollment for Moodle sync
        course_enrollment, _ = CourseEnrollment.objects.get_or_create(
            user=user,
            course=course,
            defaults={
                'is_active': True,
                'enrolled_by': None,  # System enrollment
                'sync_status': 'pending'
            }
        )
        
        return enrollment
    
    def _sync_to_moodle(self, user: User, courses_to_enroll: List[Dict]):
        """
        Sync enrollments to Moodle
        Creates Moodle user if needed, then enrolls in all courses
        """
        try:
            # Get primary Moodle instance
            from integration.models import MoodleInstance
            
            moodle_instance = MoodleInstance.objects.filter(
                is_active=True,
                is_primary=True
            ).first()
            
            if not moodle_instance:
                logger.error("No active Moodle instance configured")
                return
            
            service = MoodleIntegrationService(moodle_instance)
            
            # Ensure user exists in Moodle
            if not hasattr(user, 'moodle_mapping') or not user.moodle_mapping.moodle_user_id:
                logger.info(f"Creating Moodle user for {user.username}")
                service.create_or_update_user(user)
            
            # Enroll in each course
            for course_data in courses_to_enroll:
                course = course_data['course']
                
                try:
                    # Get or create enrollment record
                    enrollment = CourseEnrollment.objects.get(
                        user=user,
                        course=course
                    )
                    
                    # Sync to Moodle if not already synced
                    if enrollment.sync_status != 'synced':
                        service.sync_single_enrollment(enrollment)
                        logger.info(f"Synced enrollment: {user.username} → {course.title}")
                    
                except Exception as e:
                    logger.error(f"Failed to sync enrollment for {course.title}: {str(e)}")
                    # Continue with other enrollments even if one fails
                    continue
        
        except Exception as e:
            logger.error(f"Moodle sync failed: {str(e)}", exc_info=True)
            # Don't raise - enrollment records are created, sync can retry later
    
    def _send_enrollment_confirmation(
        self, 
        user: User, 
        enrollments: List[StudentEnrollment],
        order: Order
    ):
        """
        Send email confirmation with enrollment details and login instructions
        """
        try:
            course_names = [e.product.name for e in enrollments]
            is_free = order.is_free_order()
            
            subject = f"{'Free Course Enrollment' if is_free else 'Purchase Confirmation'} - Judea Harvest"
            
            message = f"""
Dear {user.get_full_name()},

{'Thank you for enrolling in our free course(s)!' if is_free else 'Thank you for your purchase!'}

Order Number: {order.order_number}
{'Amount Paid: R' + str(order.total_amount) if not is_free else ''}

You now have access to the following course(s):

{chr(10).join('- ' + name for name in course_names)}

ACCESS YOUR COURSES:
1. Log in to your account at {settings.SITE_URL}/accounts/login/
2. Click "Access Learning Platform" from your dashboard
3. You will be automatically logged into Moodle with all your courses

Your access expires on: {enrollments[0].expires_at.strftime('%B %d, %Y')}

NEED HELP?
Contact us at support@judeaharvest.com or WhatsApp: +27 XX XXX XXXX

Blessings,
The Judea Harvest Team
            """
            
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email] if user.email else [],
                fail_silently=True  # Don't break enrollment if email fails
            )
            
            logger.info(f"Sent enrollment confirmation to {user.email}")
            
        except Exception as e:
            logger.error(f"Failed to send enrollment email: {str(e)}")
            # Don't raise - email failure shouldn't break enrollment
    
    def auto_enroll_free_courses(self, user: User):
        """
        Automatically enroll verified students in all free courses
        with auto_enroll_free = True
        
        Called when:
        - Student is verified
        - New free course is published
        """
        if user.role != User.STUDENT or not user.is_verified:
            logger.warning(f"Cannot auto-enroll {user.username}: not verified student")
            return
        
        # Get all free courses with auto-enrollment enabled
        free_products = Product.objects.filter(
            is_free=True,
            is_active=True,
            auto_enroll_free=True
        )
        
        enrolled_count = 0
        
        for product in free_products:
            # Check if already enrolled
            if StudentEnrollment.objects.filter(user=user, product=product).exists():
                continue
            
            # Create free order
            order = Order.objects.create(
                user=user,
                status=Order.FREE,
                total_amount=Decimal('0.00'),
                payment_method='free'
            )
            
            OrderItem.objects.create(
                order=order,
                product=product,
                unit_price=Decimal('0.00'),
                quantity=1
            )
            
            # Process enrollment immediately
            try:
                order.process_enrollment()
                enrolled_count += 1
                logger.info(f"Auto-enrolled {user.username} in {product.name}")
            except Exception as e:
                logger.error(f"Auto-enrollment failed for {product.name}: {str(e)}")
                continue
        
        if enrolled_count > 0:
            logger.info(f"Auto-enrolled {user.username} in {enrolled_count} free courses")
        
        return enrolled_count


class CartService:
    """
    Shopping cart management
    Uses session-based cart for anonymous/logged-in users
    """
    
    def __init__(self, request):
        """Initialize cart from session"""
        self.session = request.session
        cart = self.session.get('cart')
        
        if not cart:
            cart = self.session['cart'] = {}
        
        self.cart = cart
    
    def add(self, product: Product, quantity: int = 1):
        """Add product to cart"""
        product_id = str(product.id)
        
        if product_id in self.cart:
            self.cart[product_id]['quantity'] += quantity
        else:
            self.cart[product_id] = {
                'quantity': quantity,
                'price': str(product.price)
            }
        
        self.save()
    
    def remove(self, product_id: str):
        """Remove product from cart"""
        if product_id in self.cart:
            del self.cart[product_id]
            self.save()
    
    def update_quantity(self, product_id: str, quantity: int):
        """Update product quantity"""
        if product_id in self.cart:
            if quantity > 0:
                self.cart[product_id]['quantity'] = quantity
            else:
                del self.cart[product_id]
            self.save()
    
    def clear(self):
        """Empty cart"""
        self.session['cart'] = {}
        self.save()
    
    def save(self):
        """Save cart to session"""
        self.session.modified = True
    
    def get_items(self):
        """Get cart items with product objects"""
        product_ids = self.cart.keys()
        products = Product.objects.filter(id__in=product_ids, is_active=True)
        
        items = []
        for product in products:
            product_id = str(product.id)
            items.append({
                'product': product,
                'quantity': self.cart[product_id]['quantity'],
                'price': product.price,
                'subtotal': product.price * self.cart[product_id]['quantity']
            })
        
        return items
    
    def get_total(self):
        """Calculate cart total"""
        items = self.get_items()
        return sum(item['subtotal'] for item in items)
    
    def get_item_count(self):
        """Get total number of items"""
        return sum(item['quantity'] for item in self.cart.values())
    
    def is_free_only(self):
        """Check if cart contains only free products"""
        items = self.get_items()
        return all(item['product'].is_free for item in items)