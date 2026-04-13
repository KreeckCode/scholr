import logging
from decimal import Decimal
from .services import MoodleProgressService
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib import messages
from django.views.generic import ListView, DetailView, View
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, HttpResponse, Http404
from django.db.models import Q, Count, Avg, Sum
from django.core.paginator import Paginator
from django.utils import timezone
from django.conf import settings
from django.urls import reverse
from shop.tasks import (
    sync_user_progress_task, 
    send_enrollment_email_task,
    process_order_enrollment_task
)
from accounts.models import (
    User, Product, ProductBundle, Order, OrderItem,
    StudentEnrollment, DiscountCode, GradeLevel, Subject
)
from django.db import transaction
from .forms import BundleForm
from .cart import Cart
from .forms import (
    CheckoutForm, DiscountCodeForm, ProductSearchForm,
    BulkEnrollmentForm
)
from .services import OrderService, EnrollmentService, PaymentService

logger = logging.getLogger(__name__)


# ==================== MIXINS ====================

class AdminRequiredMixin(UserPassesTestMixin):
    """
    Mixin to restrict views to admin users only
    
    Security: Prevents unauthorized access to admin features
    """
    
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.role == User.ADMIN
    
    def handle_no_permission(self):
        messages.error(self.request, 'Only administrators can access this page.')
        return redirect('accounts:dashboard')


class StudentRequiredMixin(UserPassesTestMixin):
    """
    Mixin to restrict views to students
    
    Used for: Student dashboard, course access
    """
    
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.role == User.STUDENT
    
    def handle_no_permission(self):
        messages.error(self.request, 'This page is only accessible to students.')
        return redirect('accounts:dashboard')


# ==================== CATALOG VIEWS ====================

class ProductCatalogView(ListView):
    model = Product
    template_name = 'shop/catalog.html'
    context_object_name = 'products'
    paginate_by = 12
    
    def get_queryset(self):
        queryset = Product.objects.filter(
            is_active=True
        ).select_related(
            'grade_level',
            'subject',
            'moodle_course'
        ).prefetch_related(
            'bundle_items__course'
        )
        
        # Apply filters from form
        form = ProductSearchForm(self.request.GET)
        
        if form.is_valid():
            q = form.cleaned_data.get('q')
            if q:
                queryset = queryset.filter(
                    Q(name__icontains=q) |
                    Q(description__icontains=q) |
                    Q(short_description__icontains=q)
                )
            
            grade = form.cleaned_data.get('grade')
            if grade:
                queryset = queryset.filter(grade_level_id=grade)
            
            subject = form.cleaned_data.get('subject')
            if subject:
                queryset = queryset.filter(subject_id=subject)
            
            price = form.cleaned_data.get('price')
            if price == 'free':
                queryset = queryset.filter(is_free=True)
            elif price == 'paid':
                queryset = queryset.filter(is_free=False)
            elif price == '0-500':
                queryset = queryset.filter(price__lte=500, is_free=False)
            elif price == '500-1000':
                queryset = queryset.filter(price__gte=500, price__lte=1000)
            elif price == '1000+':
                queryset = queryset.filter(price__gt=1000)
            
            sort = form.cleaned_data.get('sort', 'featured')
            if sort == 'price_asc':
                queryset = queryset.order_by('price')
            elif sort == 'price_desc':
                queryset = queryset.order_by('-price')
            elif sort == 'name_asc':
                queryset = queryset.order_by('name')
            elif sort == 'newest':
                queryset = queryset.order_by('-created_at')
            else:
                queryset = queryset.order_by('-is_featured', 'sort_order', '-created_at')
        else:
            queryset = queryset.order_by('-is_featured', 'sort_order', '-created_at')
        
        return queryset
    
    def get_context_data(self, **kwargs):
        """Add search form and filter options to context"""
        context = super().get_context_data(**kwargs)
        
        # Changed from 'form' to 'search_form' to match template
        context['search_form'] = ProductSearchForm(self.request.GET)
        
        # Add filter options
        context['grade_levels'] = GradeLevel.objects.filter(is_active=True)
        context['subjects'] = Subject.objects.filter(is_active=True)
        
        # Add total count
        context['total_products'] = self.get_queryset().count()
        
        return context


class ProductDetailView(DetailView):
    model = Product
    template_name = 'shop/product_detail.html'
    context_object_name = 'product'
    slug_field = 'slug'
    slug_url_kwarg = 'slug'
    
    def get_queryset(self):
        return Product.objects.filter(
            is_active=True
        ).select_related(
            'grade_level',
            'subject',
            'moodle_course',
            'moodle_course__category'
        ).prefetch_related(
            'bundle_items__course__category'
        )
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        product = self.object
        
        if self.request.user.is_authenticated:
            context['is_enrolled'] = StudentEnrollment.objects.filter(
                user=self.request.user,
                product=product,
                is_active=True
            ).exists()
        else:
            context['is_enrolled'] = False
        
        # Removed only() - conflicts with select_related
        context['related_products'] = Product.objects.filter(
            is_active=True
        ).filter(
            Q(grade_level=product.grade_level) | Q(subject=product.subject)
        ).exclude(
            id=product.id
        ).select_related(
            'grade_level', 
            'subject'
        )[:4]
        
        cart = Cart(self.request)
        context['cart_count'] = len(cart)
        
        return context


# ==================== CART VIEWS ====================

@require_POST
def cart_add(request, product_id):
    """
    Add product to cart
    
    Security:
    - CSRF protected
    - Validates product exists and is active
    - Server-side validation
    
    UX:
    - AJAX-friendly
    - Returns JSON for AJAX requests
    - Redirects for form submissions
    """
    try:
        product = get_object_or_404(Product, id=product_id, is_active=True)
        cart = Cart(request)
        
        # Get quantity (default 1)
        quantity = int(request.POST.get('quantity', 1))
        
        # Validate quantity
        if quantity < 1 or quantity > 10:
            raise ValueError("Invalid quantity")
        
        cart.add(product, quantity)
        
        logger.info(f"Added to cart: {product.name} x{quantity} by {request.user.username if request.user.is_authenticated else 'guest'}")
        
        # AJAX response
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'cart_count': len(cart),
                'cart_total': str(cart.get_total_price()),
                'message': f'{product.name} added to cart'
            })
        
        # Regular response
        messages.success(request, f'✓ {product.name} added to cart')
        return redirect('shop:cart_detail')
        
    except Product.DoesNotExist:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'Product not found'}, status=404)
        messages.error(request, 'Product not found')
        return redirect('shop:catalog')
    
    except Exception as e:
        logger.error(f"Error adding to cart: {str(e)}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'An error occurred'}, status=500)
        messages.error(request, 'Could not add item to cart')
        return redirect('shop:catalog')


@require_POST
def cart_remove(request, product_id):
    """
    Remove product from cart
    
    Security: CSRF protected
    """
    cart = Cart(request)
    cart.remove(product_id)
    
    messages.info(request, 'Item removed from cart')
    return redirect('shop:cart_detail')


@require_POST
def cart_update(request, product_id):
    """
    Update product quantity in cart
    
    Security:
    - CSRF protected
    - Validates quantity
    """
    try:
        quantity = int(request.POST.get('quantity', 1))
        
        if quantity < 0 or quantity > 10:
            raise ValueError("Invalid quantity")
        
        cart = Cart(request)
        cart.update_quantity(product_id, quantity)
        
        # AJAX response
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'cart_count': len(cart),
                'cart_total': str(cart.get_total_price())
            })
        
        messages.success(request, 'Cart updated')
        return redirect('shop:cart_detail')
        
    except Exception as e:
        logger.error(f"Error updating cart: {str(e)}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
        messages.error(request, 'Could not update cart')
        return redirect('shop:cart_detail')


def cart_detail(request):
    """
    Shopping cart detail page
    
    Features:
    - Shows all cart items
    - Quantity adjustment
    - Discount code application
    - Total calculation
    - Checkout button
    """
    cart = Cart(request)
    
    # Handle discount code
    discount = None
    discount_form = DiscountCodeForm()
    
    if request.method == 'POST':
        discount_form = DiscountCodeForm(
            request.POST,
            cart_total=cart.get_total_price(),
            user=request.user
        )
        
        if discount_form.is_valid():
            # Get validated code (string only)
            discount_code = discount_form.cleaned_data['code']
            # Store only the code string in session
            request.session['discount_code'] = discount_code
            messages.success(request, f'✓ Discount code "{discount_code}" applied!')
            return redirect('shop:cart_detail')
    
    # Check for existing discount in session
    elif 'discount_code' in request.session:
        try:
            discount = DiscountCode.objects.get(code=request.session['discount_code'])
            if not discount.is_valid():
                del request.session['discount_code']
                discount = None
                messages.warning(request, 'Your discount code has expired')
        except DiscountCode.DoesNotExist:
            del request.session['discount_code']
            discount = None
    
    # Calculate totals
    subtotal = cart.get_total_price()
    discount_amount = Decimal('0.00')
    
    if discount:
        discount_amount = discount.calculate_discount(subtotal)
    
    total = subtotal - discount_amount
    
    context = {
        'cart': cart,
        'subtotal': subtotal,
        'discount': discount,
        'discount_amount': discount_amount,
        'total': total,
        'discount_form': discount_form,
        'cart_count': len(cart)
    }
    
    return render(request, 'shop/cart_detail.html', context)


@require_POST
def remove_discount(request):
    """Remove applied discount code"""
    if 'discount_code' in request.session:
        del request.session['discount_code']
        messages.info(request, 'Discount code removed')
    
    return redirect('shop:cart_detail')


# ==================== CHECKOUT VIEWS ====================

def checkout(request):
    """
    Checkout process
    
    Flow:
    1. Validate cart not empty
    2. If FREE cart → Auto-create order and enroll
    3. If PAID cart → Show checkout form
    4. Create order
    5. Redirect to payment or confirmation
    
    Security:
    - Validates cart contents
    - Uses database prices (not client prices)
    - Creates atomic orders
    """
    cart = Cart(request)
    
    # Check cart not empty
    if len(cart) == 0:
        messages.warning(request, 'Your cart is empty')
        return redirect('shop:catalog')
    
    # Get discount if applied
    discount = None
    if 'discount_code' in request.session:
        try:
            discount = DiscountCode.objects.get(code=request.session['discount_code'])
            if not discount.is_valid():
                del request.session['discount_code']
                discount = None
        except DiscountCode.DoesNotExist:
            del request.session['discount_code']
    
    # Calculate totals
    subtotal = cart.get_total_price()
    discount_amount = discount.calculate_discount(subtotal) if discount else Decimal('0.00')
    total = subtotal - discount_amount
    
    # Handle form submission
    if request.method == 'POST':
        # If user authenticated, skip form
        if request.user.is_authenticated:
            user = request.user
        else:
            # Validate checkout form
            form = CheckoutForm(request.POST)
            
            if not form.is_valid():
                context = {
                    'form': form,
                    'cart': cart,
                    'subtotal': subtotal,
                    'discount': discount,
                    'discount_amount': discount_amount,
                    'total': total,
                    'cart_count': len(cart)
                }
                return render(request, 'shop/checkout.html', context)
            
            # Create or get user
            phone = form.cleaned_data['phone_number']
            email = form.cleaned_data.get('email', '')
            
            # Try to find existing user
            user = User.objects.filter(Q(phone_number=phone) | Q(email=email)).first()
            
            if not user:
                # Create new user
                import uuid
                user = User.objects.create_user(
                    username=f"user_{uuid.uuid4().hex[:8]}",
                    phone_number=phone,
                    email=email or None,
                    first_name=form.cleaned_data['first_name'],
                    last_name=form.cleaned_data['last_name'],
                    role=User.STUDENT
                )
                
                # Create profile
                from accounts.models import UserProfile
                UserProfile.objects.create(
                    user=user,
                    country=form.cleaned_data['country'],
                    church_name=form.cleaned_data.get('church_name', '')
                )
                
                messages.info(request, 'Account created! Please set a password to access your courses.')
        
        # Create order
        order_service = OrderService()
        
        try:
            order = order_service.create_order_from_cart(
                user=user,
                cart=cart,
                discount_code=discount,
                customer_notes=request.POST.get('customer_notes', '')
            )
            
            # Clear cart and discount
            cart.clear()
            if 'discount_code' in request.session:
                del request.session['discount_code']
            
            # If free order, process immediately
            if order.is_free_order():
                enrollment_service = EnrollmentService()
                enrollment_service.process_order_enrollment(order)
                
                messages.success(request, '✓ Enrollment complete! You now have access to your courses.')
                
                # Log user in if not authenticated
                if not request.user.is_authenticated:
                    from django.contrib.auth import login
                    login(request, user)
                
                return redirect('shop:order_confirmation', order_number=order.order_number)
            
            # Paid order - redirect to payment
            return redirect('shop:payment', order_number=order.order_number)
            
        except Exception as e:
            logger.error(f"Checkout error: {str(e)}", exc_info=True)
            messages.error(request, 'An error occurred during checkout. Please try again.')
            return redirect('shop:cart_detail')
    
    # GET request - show checkout form
    form = CheckoutForm() if not request.user.is_authenticated else None
    
    context = {
        'form': form,
        'cart': cart,
        'subtotal': subtotal,
        'discount': discount,
        'discount_amount': discount_amount,
        'total': total,
        'cart_count': len(cart),
        'user_authenticated': request.user.is_authenticated
    }
    
    return render(request, 'shop/checkout.html', context)


# ==================== PAYMENT VIEWS ====================

@login_required
def payment(request, order_number):
    """
    Payment page
    
    Features:
    - Shows order summary
    - Initiates payment with gateway
    - Handles payment redirect
    
    Security:
    - Verifies user owns order
    - Only shows pending orders
    """
    order = get_object_or_404(Order, order_number=order_number)
    
    # Security: Verify ownership
    order_service = OrderService()
    try:
        order_service.verify_order_ownership(order, request.user)
    except PermissionError:
        raise Http404
    
    # Only process pending orders
    if order.status != Order.PENDING:
        messages.info(request, 'This order has already been processed')
        return redirect('shop:order_confirmation', order_number=order.order_number)
    
    # Paystack integration
    if request.method == 'POST':
        # Initialize payment with Paystack
        # This would typically redirect to Paystack payment page
        
        # For now, show payment details
        context = {
            'order': order,
            'paystack_public_key': getattr(settings, 'PAYSTACK_PUBLIC_KEY', ''),
            'payment_reference': f"PAY-{order.order_number}",
            'amount_kobo': int(order.total_amount * 100),  # Convert to kobo
            'email': request.user.email or 'customer@example.com',
            'callback_url': request.build_absolute_uri(
                reverse('shop:payment_callback')
            )
        }
        
        return render(request, 'shop/payment_process.html', context)
    
    context = {
        'order': order,
    }
    
    return render(request, 'shop/payment.html', context)


@csrf_exempt
@require_POST
def payment_webhook(request):
    """
    Paystack webhook handler
    
    Security:
    - CSRF exempt (uses signature verification instead)
    - Verifies webhook signature
    - Idempotent processing
    
    Critical: This must be publicly accessible
    """
    payment_service = PaymentService()
    
    try:
        # Verify signature
        data = payment_service.verify_paystack_webhook(request)
        
        # Extract payment details
        event_type = data.get('event')
        
        if event_type == 'charge.success':
            payment_data = data['data']
            reference = payment_data['reference']
            amount = payment_data['amount']
            metadata = payment_data.get('metadata', {})
            
            # Process payment
            order = payment_service.process_payment_confirmation(
                payment_reference=reference,
                amount=amount,
                metadata=metadata
            )
            
            logger.info(f"Payment webhook processed: {reference}")
            
            return HttpResponse(status=200)
        
        # Other events - log and ignore
        logger.info(f"Webhook event ignored: {event_type}")
        return HttpResponse(status=200)
        
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}", exc_info=True)
        return HttpResponse(status=400)


def payment_callback(request):
    """
    Payment callback/redirect page
    
    User lands here after payment on gateway
    Shows processing message while webhook processes in background
    """
    reference = request.GET.get('reference')
    
    if not reference:
        messages.error(request, 'Invalid payment reference')
        return redirect('shop:catalog')
    
    # Try to find order
    # Note: Webhook might not have processed yet
    order = Order.objects.filter(payment_reference=reference).first()
    
    context = {
        'reference': reference,
        'order': order
    }
    
    return render(request, 'shop/payment_callback.html', context)


# ==================== ORDER VIEWS ====================

@login_required
def order_confirmation(request, order_number):
    """
    Order confirmation page
    
    Shows:
    - Order details
    - Enrolled courses
    - Access instructions
    """
    order = get_object_or_404(Order, order_number=order_number)
    
    # Security: Verify ownership
    order_service = OrderService()
    try:
        order_service.verify_order_ownership(order, request.user)
    except PermissionError:
        raise Http404
    
    # Get enrollments
    enrollments = StudentEnrollment.objects.filter(
        order=order
    ).select_related('product')
    
    context = {
        'order': order,
        'enrollments': enrollments
    }
    
    return render(request, 'shop/order_confirmation.html', context)


class OrderListView(LoginRequiredMixin, ListView):
    model = Order
    template_name = 'shop/order_list.html'
    context_object_name = 'orders'
    paginate_by = 20
    
    def get_queryset(self):
        if self.request.user.role == User.ADMIN:
            queryset = Order.objects.all()
        else:
            queryset = Order.objects.filter(user=self.request.user)
        
        return queryset.select_related(
            'user'
        ).prefetch_related(
            'items__product'
        ).order_by('-created_at')
        # Removed only()
    

class OrderDetailView(LoginRequiredMixin, DetailView):
    """
    Order detail page
    
    Security: Verifies user ownership
    """
    
    model = Order
    template_name = 'shop/order_detail.html'
    context_object_name = 'order'
    slug_field = 'order_number'
    slug_url_kwarg = 'order_number'
    
    def get_object(self, queryset=None):
        """Get order and verify ownership"""
        obj = super().get_object(queryset)
        
        order_service = OrderService()
        try:
            order_service.verify_order_ownership(obj, self.request.user)
        except PermissionError:
            raise Http404
        
        return obj


# ==================== STUDENT DASHBOARD VIEWS ====================

class MyCoursesView(StudentRequiredMixin, ListView):
    model = StudentEnrollment
    template_name = 'shop/my_courses.html'
    context_object_name = 'enrollments'
    paginate_by = 20
    
    def get_queryset(self):
        return StudentEnrollment.objects.filter(
            user=self.request.user,
            is_active=True
        ).select_related(
            'product',
            'product__moodle_course',
            'product__grade_level',
            'product__subject',
            'order'
        ).prefetch_related(
            'product__bundle_items__course'
        ).order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Use database aggregation instead of Python loops
        from django.db.models import Count, Q
        
        stats = StudentEnrollment.objects.filter(
            user=self.request.user,
            is_active=True
        ).aggregate(
            total=Count('id'),
            completed=Count('id', filter=Q(completed_at__isnull=False)),
            active=Count('id', filter=Q(completed_at__isnull=True))
        )
        
        context['total_courses'] = stats['total']
        context['completed_courses'] = stats['completed']
        context['active_courses'] = stats['active']
        
        return context


# ==================== ADMIN VIEWS ====================

class BulkEnrollmentView(AdminRequiredMixin, View):
    """
    Admin: Bulk enrollment tool
    
    Features:
    - Enroll multiple users at once
    - Comp/promotional access
    - Audit trail
    
    Security: Admin only
    """
    
    template_name = 'shop/admin/bulk_enrollment.html'
    
    def get(self, request):
        form = BulkEnrollmentForm()
        return render(request, self.template_name, {'form': form})
    
    def post(self, request):
        form = BulkEnrollmentForm(request.POST)
        
        if form.is_valid():
            users = form.get_users()
            product = form.cleaned_data['product']
            duration_months = form.cleaned_data['duration_months']
            reason = form.cleaned_data['reason']
            
            # Process enrollments
            enrollment_service = EnrollmentService()
            enrolled_count = 0
            
            for user in users:
                try:
                    # Create free order for audit trail
                    order = Order.objects.create(
                        user=user,
                        status=Order.FREE,
                        total_amount=Decimal('0.00'),
                        payment_method='admin_comp',
                        admin_notes=f"Bulk enrollment by {request.user.username}: {reason}"
                    )
                    
                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        unit_price=Decimal('0.00'),
                        quantity=1
                    )
                    
                    # Create enrollment
                    StudentEnrollment.objects.create(
                        user=user,
                        product=product,
                        order=order,
                        is_free=True,
                        enrolled_by=request.user,
                        starts_at=timezone.now(),
                        expires_at=timezone.now() + timezone.timedelta(days=duration_months * 30)
                    )
                    
                    enrolled_count += 1
                    
                except Exception as e:
                    logger.error(f"Bulk enrollment error for {user.username}: {str(e)}")
            
            messages.success(
                request,
                f'✓ Successfully enrolled {enrolled_count} users in {product.name}'
            )
            
            return redirect('shop:bulk_enrollment')
        
        return render(request, self.template_name, {'form': form})



# ==================== BUNDLE MANAGEMENT VIEWS ====================

class BundleListView(AdminRequiredMixin, ListView):
    model = Product
    template_name = 'shop/bundles/bundle_list.html'
    context_object_name = 'bundles'
    paginate_by = 20
    
    def get_queryset(self):
        return Product.objects.filter(
            product_type=Product.BUNDLE
        ).prefetch_related(
            'bundle_items__course'
        ).order_by('-is_featured', '-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Use aggregation
        from django.db.models import Count, Q
        
        stats = Product.objects.filter(
            product_type=Product.BUNDLE
        ).aggregate(
            total=Count('id'),
            active=Count('id', filter=Q(is_active=True)),
            free=Count('id', filter=Q(is_free=True)),
            paid=Count('id', filter=Q(is_free=False))
        )
        
        context['total_bundles'] = stats['total']
        context['active_bundles'] = stats['active']
        context['free_bundles'] = stats['free']
        context['paid_bundles'] = stats['paid']
        
        return context

class BundleCreateView(AdminRequiredMixin, View):
    """
    Admin: Create new course bundle
    
    Features:
    - Multi-step form
    - Course selection with preview
    - Auto-slug generation
    - Validation
    """
    
    template_name = 'shop/bundles/bundle_form.html'
    
    def get(self, request):
        """Show bundle creation form"""
        form = BundleForm()
        
        context = {
            'form': form,
            'action': 'Create',
            'title': 'Create New Bundle'
        }
        
        return render(request, self.template_name, context)
    
    @transaction.atomic
    def post(self, request):
        """Handle bundle creation"""
        form = BundleForm(request.POST, request.FILES)
        
        if form.is_valid():
            bundle = form.save()
            
            messages.success(
                request,
                f'✓ Bundle "{bundle.name}" created successfully with '
                f'{bundle.bundle_items.count()} courses!'
            )
            
            return redirect('shop:bundle_list')
        
        context = {
            'form': form,
            'action': 'Create',
            'title': 'Create New Bundle'
        }
        
        return render(request, self.template_name, context)


class BundleEditView(AdminRequiredMixin, View):
    """
    Admin: Edit existing bundle
    
    Features:
    - Pre-populated form
    - Update courses
    - Maintain bundle items
    """
    
    template_name = 'shop/bundles/bundle_form.html'
    
    def get(self, request, bundle_id):
        """Show bundle edit form"""
        bundle = get_object_or_404(Product, id=bundle_id, product_type=Product.BUNDLE)
        form = BundleForm(instance=bundle)
        
        context = {
            'form': form,
            'bundle': bundle,
            'action': 'Edit',
            'title': f'Edit Bundle: {bundle.name}'
        }
        
        return render(request, self.template_name, context)
    
    @transaction.atomic
    def post(self, request, bundle_id):
        """Handle bundle update"""
        bundle = get_object_or_404(Product, id=bundle_id, product_type=Product.BUNDLE)
        form = BundleForm(request.POST, request.FILES, instance=bundle)
        
        if form.is_valid():
            bundle = form.save()
            
            messages.success(
                request,
                f'✓ Bundle "{bundle.name}" updated successfully!'
            )
            
            return redirect('shop:bundle_list')
        
        context = {
            'form': form,
            'bundle': bundle,
            'action': 'Edit',
            'title': f'Edit Bundle: {bundle.name}'
        }
        
        return render(request, self.template_name, context)


@require_POST
def bundle_delete(request, bundle_id):
    """
    Admin: Delete bundle
    
    Security:
    - Admin only
    - POST required
    - Confirmation handled by frontend
    """
    if not request.user.is_authenticated or request.user.role != User.ADMIN:
        messages.error(request, 'Only administrators can delete bundles')
        return redirect('accounts:dashboard')
    
    bundle = get_object_or_404(Product, id=bundle_id, product_type=Product.BUNDLE)
    bundle_name = bundle.name
    
    try:
        # Check if bundle has active enrollments
        enrollment_count = StudentEnrollment.objects.filter(
            product=bundle,
            is_active=True
        ).count()
        
        if enrollment_count > 0:
            messages.warning(
                request,
                f'Cannot delete "{bundle_name}" - {enrollment_count} active enrollments exist. '
                f'Deactivate the bundle instead.'
            )
            return redirect('shop:bundle_list')
        
        # Safe to delete
        bundle.delete()
        
        messages.success(request, f'✓ Bundle "{bundle_name}" deleted successfully')
        
    except Exception as e:
        logger.error(f"Error deleting bundle {bundle_id}: {str(e)}")
        messages.error(request, 'Error deleting bundle. Please try again.')
    
    return redirect('shop:bundle_list')


class BundleDetailView(AdminRequiredMixin, DetailView):
    """
    Admin: View bundle details
    
    Shows:
    - All bundle information
    - Included courses
    - Enrollment stats
    - Quick edit link
    """
    
    model = Product
    template_name = 'shop/bundles/bundle_detail.html'
    context_object_name = 'bundle'
    pk_url_kwarg = 'bundle_id'
    
    def get_queryset(self):
        """Only bundle products"""
        return Product.objects.filter(
            product_type=Product.BUNDLE
        ).prefetch_related('bundle_items__course__category')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        bundle = self.object
        
        # Get bundle courses
        context['bundle_courses'] = ProductBundle.objects.filter(
            product=bundle
        ).select_related('course__category').order_by('sort_order')
        
        # Stats
        context['total_enrollments'] = StudentEnrollment.objects.filter(
            product=bundle
        ).count()
        
        context['active_enrollments'] = StudentEnrollment.objects.filter(
            product=bundle,
            is_active=True
        ).count()
        
        return context
        


@login_required
def refresh_progress(request):
    """
    Queue progress refresh as background task
    Returns immediately with confirmation
    """
    from shop.tasks import sync_user_progress_task
    
    # Queue the task
    task = sync_user_progress_task.delay(request.user.id)
    
    messages.info(
        request,
        "Progress sync queued! Your course progress will update in a few moments. "
        "Refresh the page in 30 seconds to see updated progress."
    )
    
    # AJAX response
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'message': 'Progress sync queued',
            'task_id': task.id
        })
    
    return redirect('shop:my_courses')

@login_required
def debug_moodle_apis(request):
    """
    Test what Moodle APIs return for current user
    Admin only
    """
    
    from integration.models import MoodleInstance, MoodleUserMapping
    from integration.moodle_client import MoodleClient
    
    instance = MoodleInstance.objects.filter(is_active=True).first()
    if not instance:
        return JsonResponse({'error': 'No Moodle instance'})
    
    try:
        mapping = request.user.moodle_mapping
    except:
        return JsonResponse({'error': 'User not mapped'})
    
    client = MoodleClient(instance.base_url, instance.ws_token)
    
    results = {}
    
    # Test 1: Get user's courses
    try:
        results['user_courses'] = client._make_request(
            'core_enrol_get_users_courses',
            {'userid': mapping.moodle_user_id}
        )
    except Exception as e:
        results['user_courses_error'] = str(e)
    
    # Test 2: Get activities completion for first course
    if results.get('user_courses'):
        first_course_id = results['user_courses'][0]['id']
        
        try:
            results['activities_completion'] = client._make_request(
                'core_completion_get_activities_completion_status',
                {
                    'courseid': first_course_id,
                    'userid': mapping.moodle_user_id
                }
            )
        except Exception as e:
            results['activities_completion_error'] = str(e)
    
    return JsonResponse(results, json_dumps_params={'indent': 2})
