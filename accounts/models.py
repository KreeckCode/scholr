from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone


class User(AbstractUser):
    """
    Custom user model for scholr CAS
    Extends Django's AbstractUser to support role-based access and African context needs
    Phone numbers are primary contact method since many pastors lack email
    """
    
    # Role choices for the system
    STUDENT = 'student'
    DEAN = 'dean'
    ADMIN = 'admin'

    # School management roles - academics app context
    TEACHER = 'teacher'
    PRINCIPAL = 'principal'
    HOD = 'hod'
    ADMIN_CLERK = 'admin_clerk'
    PARENT = 'parent'
    LEARNER = 'learner'
    
    ROLE_CHOICES = [
        # LMS roles
        (STUDENT, 'Student (Pastor)'),
        (DEAN, 'Dean (Facilitator)'),
        (ADMIN, 'System Administrator'),
        # School roles - added in academics Stage 1
        (TEACHER, 'Teacher'),
        (PRINCIPAL, 'Principal'),
        (HOD, 'Head of Department'),
        (ADMIN_CLERK, 'Administrative Clerk'),
        (PARENT, 'Parent / Guardian'),
        (LEARNER, 'Learner'),
    ]
    # Role groupings - I'm using these to avoid scattering role checks everywhere
    LMS_ROLES = {STUDENT, DEAN, ADMIN}
    SCHOOL_ROLES = {TEACHER, PRINCIPAL, HOD, ADMIN_CLERK, PARENT, LEARNER}
    STAFF_ROLES = {TEACHER, PRINCIPAL, HOD, ADMIN_CLERK}
    
    # Phone validator for international format
    phone_regex = RegexValidator(
        regex=r'^\+?1?\d{9,15}$',
        message="Phone must be in format: '+999999999'. Up to 15 digits allowed."
    )
    
    # Core fields
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default=STUDENT,
        db_index=True,
        help_text="User's role in the system determines permissions"
    )
    
    phone_number = models.CharField(
        validators=[phone_regex],
        max_length=17,
        unique=True,
        help_text="Primary contact number - required for all users"
    )
    
    # Email is optional in African rural context
    email = models.EmailField(
        _('email address'),
        blank=True,
        null=True,
        help_text="Email is optional - many pastors don't have regular access"
    )
    
    # Account status fields
    is_verified = models.BooleanField(
        default=False,
        help_text="Whether dean/admin has verified this account"
    )
    
    verified_by = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_users',
        help_text="Admin/Dean who verified this account"
    )
    
    verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the account was verified"
    )
    
    # Tracking fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_sync = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time this user's data was synced from offline device"
    )
    
    class Meta:
        db_table = 'users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['email'], name='idx_user_email'),
            models.Index(fields=['phone_number'], name='idx_user_phone'),
            models.Index(fields=['role', 'is_active'], name='idx_user_role_active'),
            models.Index(fields=['is_verified'], name='idx_user_verified'),
            models.Index(fields=['created_at'], name='idx_user_created'),
        ]
    
    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"
    
    def get_full_name(self):
        """
        Return full name or username if name not set
        Handles cases where pastors may only provide one name
        """
        full_name = f"{self.first_name} {self.last_name}".strip()
        return full_name if full_name else self.username
    
    def can_verify_users(self):
        """Check if user can verify other accounts - LMS deans/admins and school principals."""
        return self.role in {self.DEAN, self.ADMIN, self.PRINCIPAL} and self.is_active

    def can_manage_content(self):
        """Check if user can upload/manage course content - LMS admins only."""
        return self.role == self.ADMIN and self.is_active

    def is_school_staff(self):
        """True for any role that works inside the school building."""
        return self.role in self.STAFF_ROLES and self.is_active

    def is_lms_user(self):
        """True for pastoral LMS roles - determines Moodle sync eligibility."""
        return self.role in self.LMS_ROLES

    def get_students(self):
        """
        For deans: get LMS students in their region.
        For admins: get all LMS students.
        School learners are fetched via academics.Learner, not here.
        """
        if self.role == self.DEAN:
            return User.objects.filter(
                role=self.STUDENT,
                profile__region=self.profile.region
            ).select_related('profile')
        elif self.role == self.ADMIN:
            return User.objects.filter(role=self.STUDENT).select_related('profile')
        return User.objects.none()


class UserProfile(models.Model):
    """
    Extended profile information for users
    Stores demographic and ministry-related data for analytics and reporting
    One-to-one relationship with User model
    """
    
    # Education level choices
    EDUCATION_CHOICES = [
        ('none', 'No Formal Education'),
        ('primary', 'Primary Education'),
        ('secondary', 'Secondary Education'),
        ('certificate', 'Certificate/Diploma'),
        ('undergraduate', 'Undergraduate Degree'),
        ('postgraduate', 'Postgraduate Degree'),
        ('theological', 'Theological Training'),
    ]
    
    # Church size categories
    CHURCH_SIZE_CHOICES = [
        ('small', 'Small (1-50 members)'),
        ('medium', 'Medium (51-200 members)'),
        ('large', 'Large (201-500 members)'),
        ('mega', 'Mega Church (500+ members)'),
    ]
    
    # Core relationship
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile',
        primary_key=True
    )
    
    # Geographic information - critical for offline deployment and analytics
    country = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Country where pastor ministers"
    )
    
    region = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Region/Province/State within country"
    )
    
    village_town = models.CharField(
        max_length=100,
        blank=True,
        help_text="Village or town name"
    )
    
    # Ministry information
    church_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Name of church or ministry"
    )
    
    church_affiliation = models.CharField(
        max_length=200,
        blank=True,
        help_text="Denomination or network affiliation"
    )
    
    church_size = models.CharField(
        max_length=20,
        choices=CHURCH_SIZE_CHOICES,
        blank=True,
        help_text="Approximate size of congregation"
    )
    
    years_in_ministry = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Total years serving in ministry"
    )
    
    position_title = models.CharField(
        max_length=100,
        blank=True,
        help_text="Current ministry position (e.g., Senior Pastor, Youth Pastor)"
    )
    
    # Personal information
    date_of_birth = models.DateField(
        null=True,
        blank=True,
        help_text="Used for age analytics, not displayed publicly"
    )
    
    education_level = models.CharField(
        max_length=20,
        choices=EDUCATION_CHOICES,
        blank=True,
        help_text="Highest level of formal education completed"
    )
    
    languages_spoken = models.CharField(
        max_length=200,
        blank=True,
        help_text="Comma-separated list of languages (e.g., English, Swahili, French)"
    )
    
    # Additional contact
    whatsapp_number = models.CharField(
        max_length=17,
        blank=True,
        help_text="WhatsApp number if different from phone number"
    )
    
    # Profile completion and engagement
    profile_photo = models.ImageField(
        upload_to='profile_photos/%Y/%m/',
        blank=True,
        null=True,
        help_text="Optional profile photo"
    )
    
    bio = models.TextField(
        blank=True,
        max_length=500,
        help_text="Brief biography or ministry background"
    )
    
    # Administrative fields
    registration_notes = models.TextField(
        blank=True,
        help_text="Internal notes from dean/admin during verification"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'user_profiles'
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'
        indexes = [
            models.Index(fields=['country', 'region']),
            models.Index(fields=['church_affiliation']),
        ]
    
    def __str__(self):
        return f"Profile: {self.user.get_full_name()}"
    
    def get_age(self):
        """Calculate age from date of birth"""
        if not self.date_of_birth:
            return None
        from datetime import date
        today = date.today()
        return today.year - self.date_of_birth.year - (
            (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
        )
    
    def get_languages_list(self):
        """Return languages as a list"""
        if not self.languages_spoken:
            return []
        return [lang.strip() for lang in self.languages_spoken.split(',')]
    
    def is_profile_complete(self):
        """
        Check if profile has minimum required information
        Useful for prompting users to complete registration
        """
        required_fields = [
            self.country,
            self.region,
            self.church_name,
            self.education_level,
        ]
        return all(required_fields)


class LoginAttempt(models.Model):
    """
    Track login attempts for security monitoring
    Helps identify brute force attacks or suspicious activity
    Important for offline-sync security where devices may be shared
    """
    
    username = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=17, blank=True)
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField(blank=True)
    success = models.BooleanField(default=False)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'login_attempts'
        verbose_name = 'Login Attempt'
        verbose_name_plural = 'Login Attempts'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['username', 'timestamp']),
            models.Index(fields=['ip_address', 'timestamp']),
            models.Index(fields=['success', 'timestamp']),
        ]
    
    def __str__(self):
        status = "Success" if self.success else "Failed"
        return f"{status}: {self.username} at {self.timestamp}"
    




# ==================== PRODUCT & CATALOG MODELS ====================

class GradeLevel(models.Model):
    """
    Organizational structure for courses (e.g., Grade 10, Grade 11)
    Used to group courses logically in the catalog
    """
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Grade level name (e.g., 'Grade 10', 'Certificate Level 1')"
    )
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    sort_order = models.IntegerField(
        default=0,
        help_text="Display order in catalog (lower numbers first)"
    )
    is_active = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'grade_levels'
        verbose_name = 'Grade Level'
        verbose_name_plural = 'Grade Levels'
        ordering = ['sort_order', 'name']
    
    def __str__(self):
        return self.name


class Subject(models.Model):
    """
    Subject/discipline definition (e.g., Mathematics, Theology)
    Used for categorization and filtering
    """
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(
        max_length=20,
        unique=True,
        help_text="Short code (e.g., 'MATH', 'THEO')"
    )
    description = models.TextField(blank=True)
    icon = models.CharField(
        max_length=50,
        blank=True,
        help_text="Icon class name (e.g., 'fa-calculator')"
    )
    color = models.CharField(
        max_length=7,
        default='#3B82F6',
        help_text="Hex color for UI theming"
    )
    
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'subjects'
        verbose_name = 'Subject'
        verbose_name_plural = 'Subjects'
        ordering = ['sort_order', 'name']
    
    def __str__(self):
        return f"{self.code} - {self.name}"


class Product(models.Model):
    """
    Sellable products - can be single course, bundle, or term-specific access
    Supports both FREE and PAID courses via price field
    """
    
    # Product type choices
    COURSE = 'course'
    BUNDLE = 'bundle'
    TERM = 'term'
    
    PRODUCT_TYPE_CHOICES = [
        (COURSE, 'Single Course'),
        (BUNDLE, 'Course Bundle'),
        (TERM, 'Term Access'),
    ]
    
    # Core fields
    name = models.CharField(
        max_length=200,
        help_text="Product name shown to customers"
    )
    slug = models.SlugField(unique=True, max_length=250)
    product_type = models.CharField(
        max_length=20,
        choices=PRODUCT_TYPE_CHOICES,
        default=COURSE
    )
    
    # Pricing - FREE courses have price = 0
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Price in ZAR. Set to 0 for FREE courses."
    )
    currency = models.CharField(max_length=3, default='ZAR')
    
    # Course relationship (for single course or term products)
    moodle_course = models.ForeignKey(
        'integration.Course',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products',
        help_text="Link to Moodle course (for single course products)"
    )
    
    # Term-specific fields
    term_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Specific term (1-4) if product_type is 'term'"
    )
    
    # Categorization
    grade_level = models.ForeignKey(
        'GradeLevel',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products'
    )
    subject = models.ForeignKey(
        'Subject',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products'
    )
    
    # Content
    description = models.TextField(
        help_text="Full product description (supports Markdown)"
    )
    short_description = models.CharField(
        max_length=300,
        blank=True,
        help_text="Brief description for catalog listing"
    )
    
    # Media
    image = models.ImageField(
        upload_to='products/%Y/%m/',
        blank=True,
        null=True,
        help_text="Product featured image"
    )
    
    # Access duration
    duration_months = models.PositiveIntegerField(
        default=12,
        help_text="How many months of access after purchase"
    )
    
    # What's included (for display)
    included_features = models.JSONField(
        default=list,
        blank=True,
        help_text="List of features (e.g., ['40+ Videos', 'PDFs', 'Quizzes'])"
    )
    
    # Course outline (for display)
    course_outline = models.JSONField(
        default=list,
        blank=True,
        help_text="Structured outline (e.g., terms/lessons)"
    )
    
    # Status flags
    is_active = models.BooleanField(
        default=True,
        help_text="Whether product is available for purchase"
    )
    is_featured = models.BooleanField(
        default=False,
        help_text="Show in featured products section"
    )
    is_free = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Auto-set to True if price = 0"
    )
    
    # Auto-enrollment for free courses
    auto_enroll_free = models.BooleanField(
        default=True,
        help_text="Automatically enroll verified students in free courses"
    )
    
    # Display order
    sort_order = models.IntegerField(
        default=0,
        help_text="Display order in catalog"
    )
    
    # Metadata
    preview_url = models.URLField(
        blank=True,
        help_text="Link to preview/demo content"
    )
    requirements = models.TextField(
        blank=True,
        help_text="Prerequisites or requirements"
    )
    learning_outcomes = models.TextField(
        blank=True,
        help_text="What students will learn"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When product was made available"
    )
    
    class Meta:
        db_table = 'products'
        verbose_name = 'Product'
        verbose_name_plural = 'Products'
        ordering = ['sort_order', '-created_at']
        indexes = [
            models.Index(fields=['is_active', 'is_featured'], name='idx_product_active_featured'),
            models.Index(fields=['product_type', 'is_active'], name='idx_product_type_active'),
            models.Index(fields=['slug'], name='idx_product_slug'),
            models.Index(fields=['grade_level', 'is_active'], name='idx_product_grade_active'),
            models.Index(fields=['subject', 'is_active'], name='idx_product_subject_active'),
            models.Index(fields=['price'], name='idx_product_price'),
            models.Index(fields=['sort_order'], name='idx_product_sort'),
            models.Index(fields=['created_at'], name='idx_product_created'),
        ]
    
    def __str__(self):
        price_display = "FREE" if self.is_free else f"R{self.price}"
        return f"{self.name} ({price_display})"
    
    def save(self, *args, **kwargs):
        """Auto-set is_free flag based on price"""
        self.is_free = (self.price == Decimal('0.00'))
        super().save(*args, **kwargs)
    
    def get_absolute_url(self):
        """URL for product detail page"""
        from django.urls import reverse
        return reverse('catalog:product_detail', kwargs={'slug': self.slug})
    
    def is_purchasable(self):
        """Check if product can be purchased"""
        return self.is_active and not self.is_free
    
    def requires_payment(self):
        """Check if product requires payment"""
        return not self.is_free and self.price > 0


class ProductBundle(models.Model):
    """
    Defines which courses are included in a bundle product
    Example: Grade 10 Complete includes Math + Economics + Business
    """
    product = models.ForeignKey(
        'Product',
        on_delete=models.CASCADE,
        related_name='bundle_items',
        limit_choices_to={'product_type': Product.BUNDLE}
    )
    course = models.ForeignKey(
        'integration.Course',
        on_delete=models.CASCADE,
        related_name='included_in_bundles'
    )
    
    # Optional: Restrict to specific terms
    include_terms = models.JSONField(
        default=list,
        blank=True,
        help_text="List of term numbers to include (e.g., [1, 2, 3, 4] or [1, 2])"
    )
    
    # Display order within bundle
    sort_order = models.IntegerField(default=0)
    
    class Meta:
        db_table = 'product_bundles'
        verbose_name = 'Product Bundle Item'
        verbose_name_plural = 'Product Bundle Items'
        unique_together = [['product', 'course']]
        ordering = ['sort_order']
        indexes = [
            models.Index(fields=['product'], name='idx_bundle_product'),
            models.Index(fields=['course'], name='idx_bundle_course'),
            models.Index(fields=['sort_order'], name='idx_bundle_sort'),
        ]
    
    def __str__(self):
        terms = f" (Terms {self.include_terms})" if self.include_terms else ""
        return f"{self.product.name}: {self.course.title}{terms}"


# ==================== ORDER & PAYMENT MODELS ====================

class Order(models.Model):
    """
    Customer orders - tracks purchases and payments
    FREE products create orders with total_amount = 0 and auto-complete
    """
    
    # Order status choices
    PENDING = 'pending'
    PROCESSING = 'processing'
    COMPLETED = 'completed'
    FAILED = 'failed'
    REFUNDED = 'refunded'
    FREE = 'free'  # For free course "orders"
    
    STATUS_CHOICES = [
        (PENDING, 'Pending Payment'),
        (PROCESSING, 'Processing Payment'),
        (COMPLETED, 'Completed'),
        (FAILED, 'Payment Failed'),
        (REFUNDED, 'Refunded'),
        (FREE, 'Free Enrollment'),
    ]
    
    # Core fields
    order_number = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="Unique order identifier"
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='orders'
    )
    
    # Pricing
    subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00')
    )
    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00')
    )
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00')
    )
    currency = models.CharField(max_length=3, default='ZAR')
    
    # Status
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING,
        db_index=True
    )
    
    # Payment details
    payment_method = models.CharField(
        max_length=50,
        blank=True,
        help_text="Payment gateway used (paystack, stripe, free)"
    )
    payment_reference = models.CharField(
        max_length=200,
        blank=True,
        db_index=True,
        help_text="Payment gateway transaction reference"
    )
    payment_metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional payment data from gateway"
    )
    
    # Timestamps
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Enrollment tracking
    enrollment_processed = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether enrollments have been created"
    )
    enrollment_processed_at = models.DateTimeField(null=True, blank=True)
    enrollment_error = models.TextField(
        blank=True,
        help_text="Error message if enrollment failed"
    )
    # Proof of payment (manual EFT flow)
    proof_of_payment = models.FileField(
        upload_to='proofs_of_payment/%Y/%m/',
        blank=True,
        null=True,
        help_text="Customer uploaded proof of payment"
    )
    pop_submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When customer submitted proof of payment"
    )
    
    # Notes
    customer_notes = models.TextField(blank=True)
    admin_notes = models.TextField(blank=True)
    
    class Meta:
        db_table = 'orders'
        verbose_name = 'Order'
        verbose_name_plural = 'Orders'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status'], name='idx_order_user_status'),
            models.Index(fields=['order_number'], name='idx_order_number'),
            models.Index(fields=['status', 'created_at'], name='idx_order_status_created'),
            models.Index(fields=['payment_reference'], name='idx_order_payment_ref'),
            models.Index(fields=['created_at'], name='idx_order_created'),
            models.Index(fields=['paid_at'], name='idx_order_paid'),
        ]
    
    def __str__(self):
        return f"Order {self.order_number} - {self.user.get_full_name()} (R{self.total_amount})"
    
    def save(self, *args, **kwargs):
        """Generate order number if not set"""
        if not self.order_number:
            import uuid
            self.order_number = f"JH{timezone.now().strftime('%Y%m%d')}{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)
    
    def is_paid(self):
        """Check if order is paid"""
        return self.status in [self.COMPLETED, self.FREE]
    
    def is_free_order(self):
        """Check if this is a free order"""
        return self.total_amount == Decimal('0.00') or self.status == self.FREE
    
    def calculate_totals(self):
        """Recalculate order totals from items"""
        items = self.items.all()
        self.subtotal = sum(item.subtotal for item in items)
        self.total_amount = self.subtotal - self.discount_amount
        
        # Free order if total is 0
        if self.total_amount == Decimal('0.00'):
            self.status = self.FREE
            self.payment_method = 'free'
    
    def mark_paid(self, payment_reference='', payment_method=''):
        """Mark order as paid and trigger enrollment"""
        self.status = self.COMPLETED
        self.paid_at = timezone.now()
        if payment_reference:
            self.payment_reference = payment_reference
        if payment_method:
            self.payment_method = payment_method
        self.save()
    
    def process_enrollment(self):
        """
        Trigger enrollment processing
        Called after payment confirmation or immediately for free orders
        """
        from .services import EnrollmentService
        
        if self.enrollment_processed:
            return True
        
        try:
            service = EnrollmentService()
            enrollments = service.process_order_enrollment(self)
            
            self.enrollment_processed = True
            self.enrollment_processed_at = timezone.now()
            self.save()
            
            return True
        except Exception as e:
            self.enrollment_error = str(e)
            self.save()
            return False


class OrderItem(models.Model):
    """
    Individual items in an order
    One order can have multiple products
    """
    order = models.ForeignKey(
        'Order',
        on_delete=models.CASCADE,
        related_name='items'
    )
    product = models.ForeignKey(
        'Product',
        on_delete=models.PROTECT,
        related_name='order_items'
    )
    
    # Pricing at time of purchase (products can change price later)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Price per unit at time of purchase"
    )
    subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="quantity × unit_price"
    )
    
    # Track what was enrolled from this item
    enrollment_log = models.JSONField(
        default=dict,
        blank=True,
        help_text="Courses enrolled from this product"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'order_items'
        verbose_name = 'Order Item'
        verbose_name_plural = 'Order Items'
        ordering = ['id']
    
    def __str__(self):
        return f"{self.product.name} × {self.quantity}"
    
    def save(self, *args, **kwargs):
        """Calculate subtotal automatically"""
        self.subtotal = self.unit_price * self.quantity
        super().save(*args, **kwargs)


# ==================== ENROLLMENT & ACCESS MODELS ====================

class StudentEnrollment(models.Model):
    """
    Tracks student access to products (beyond Moodle CourseEnrollment)
    Manages access duration and expiration
    Separate from integration.CourseEnrollment which is Moodle-specific
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='product_enrollments',
        limit_choices_to={'role': User.STUDENT}
    )
    product = models.ForeignKey(
        'Product',
        on_delete=models.CASCADE,
        related_name='enrollments'
    )
    order = models.ForeignKey(
        'Order',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='enrollments',
        help_text="Order that created this enrollment (null for free/manual)"
    )
    
    # Access period
    starts_at = models.DateTimeField(
        default=timezone.now,
        help_text="When access begins"
    )
    expires_at = models.DateTimeField(
        help_text="When access ends"
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True
    )
    
    # Enrollment type
    is_free = models.BooleanField(
        default=False,
        help_text="Whether this is a free enrollment"
    )
    enrolled_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='manual_enrollments',
        help_text="Admin who manually enrolled (for comps/gifts)"
    )
    
    # Progress tracking (aggregated from Moodle)
    progress_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Overall completion percentage"
    )
    last_accessed = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time student accessed content"
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When student completed all requirements"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'student_enrollments'
        verbose_name = 'Student Enrollment'
        verbose_name_plural = 'Student Enrollments'
        unique_together = [['user', 'product']]
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_active'], name='idx_enrollment_user_active'),
            models.Index(fields=['product', 'is_active'], name='idx_enrollment_product_active'),
            models.Index(fields=['user', 'product'], name='idx_enrollment_user_product'),
            models.Index(fields=['expires_at'], name='idx_enrollment_expires'),
            models.Index(fields=['created_at'], name='idx_enrollment_created'),
            models.Index(fields=['completed_at'], name='idx_enrollment_completed'),
            models.Index(fields=['is_active', 'expires_at'], name='idx_enrollment_active_expires'),
        ]
    
    def __str__(self):
        return f"{self.user.get_full_name()} → {self.product.name}"
    
    def save(self, *args, **kwargs):
        """Set expiration date if not set"""
        if not self.expires_at:
            self.expires_at = self.starts_at + timedelta(days=self.product.duration_months * 30)
        super().save(*args, **kwargs)
    
    def is_expired(self):
        """Check if enrollment has expired"""
        return timezone.now() > self.expires_at
    
    def days_remaining(self):
        """Calculate days until expiration"""
        if self.is_expired():
            return 0
        delta = self.expires_at - timezone.now()
        return delta.days
    
    def extend_access(self, months=1):
        """Extend access by specified months"""
        self.expires_at = self.expires_at + timedelta(days=months * 30)
        self.save()
    
    def mark_completed(self):
        """Mark enrollment as completed"""
        if not self.completed_at:
            self.completed_at = timezone.now()
            self.progress_percentage = Decimal('100.00')
            self.save()


class DiscountCode(models.Model):
    """
    Promotional discount codes for products
    Optional - can be added later for marketing campaigns
    """
    
    # Discount type choices
    PERCENTAGE = 'percentage'
    FIXED = 'fixed'
    
    DISCOUNT_TYPE_CHOICES = [
        (PERCENTAGE, 'Percentage Discount'),
        (FIXED, 'Fixed Amount Discount'),
    ]
    
    code = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="Discount code (e.g., 'SUMMER2026')"
    )
    description = models.CharField(
        max_length=200,
        help_text="Internal description of promotion"
    )
    
    # Discount value
    discount_type = models.CharField(
        max_length=20,
        choices=DISCOUNT_TYPE_CHOICES,
        default=PERCENTAGE
    )
    discount_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Percentage (e.g., 15 for 15%) or fixed amount"
    )
    
    # Applicability
    applies_to_products = models.ManyToManyField(
        'Product',
        blank=True,
        related_name='discount_codes',
        help_text="Specific products (leave empty for all products)"
    )
    minimum_purchase = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Minimum order amount required"
    )
    
    # Usage limits
    max_uses = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Max total uses (null = unlimited)"
    )
    max_uses_per_user = models.PositiveIntegerField(
        default=1,
        help_text="Max uses per user"
    )
    times_used = models.PositiveIntegerField(
        default=0,
        help_text="Current usage count"
    )
    
    # Validity period
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField()
    
    # Status
    is_active = models.BooleanField(default=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'discount_codes'
        verbose_name = 'Discount Code'
        verbose_name_plural = 'Discount Codes'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.code} ({self.discount_value}{'%' if self.discount_type == self.PERCENTAGE else ' ZAR'})"
    
    def is_valid(self):
        """Check if discount code is currently valid"""
        now = timezone.now()
        return (
            self.is_active and
            self.valid_from <= now <= self.valid_until and
            (self.max_uses is None or self.times_used < self.max_uses)
        )
    
    def calculate_discount(self, subtotal):
        """Calculate discount amount for given subtotal"""
        if not self.is_valid() or subtotal < self.minimum_purchase:
            return Decimal('0.00')
        
        if self.discount_type == self.PERCENTAGE:
            return (subtotal * self.discount_value / 100).quantize(Decimal('0.01'))
        else:
            return min(self.discount_value, subtotal)