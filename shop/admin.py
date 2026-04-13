"""
Django Admin configuration for Shop models

Features:
- List displays with filters
- Search functionality
- Inline editing
- Custom actions
- Read-only fields for security
"""

from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal

from accounts.models import (
    Product, ProductBundle, Order, OrderItem,
    StudentEnrollment, DiscountCode, GradeLevel, Subject
)


# ==================== GRADE LEVEL ====================

@admin.register(GradeLevel)
class GradeLevelAdmin(admin.ModelAdmin):
    """Admin for GradeLevel"""
    
    list_display = ['name', 'slug', 'sort_order', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'description']
    prepopulated_fields = {'slug': ('name',)}
    ordering = ['sort_order', 'name']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'slug', 'description')
        }),
        ('Display', {
            'fields': ('sort_order', 'is_active')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ['created_at', 'updated_at']


# ==================== SUBJECT ====================

@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    """Admin for Subject"""
    
    list_display = ['name', 'code', 'color_preview', 'icon', 'sort_order', 'is_active']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'code', 'description']
    ordering = ['sort_order', 'name']
    
    def color_preview(self, obj):
        """Show color preview"""
        return format_html(
            '<span style="background-color: {}; padding: 5px 20px; border-radius: 3px;">&nbsp;</span>',
            obj.color
        )
    color_preview.short_description = 'Color'
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'code', 'description')
        }),
        ('Visual', {
            'fields': ('icon', 'color', 'sort_order')
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
    )
    
    readonly_fields = ['created_at', 'updated_at']


# ==================== PRODUCT ====================

class ProductBundleInline(admin.TabularInline):
    """Inline for bundle items"""
    model = ProductBundle
    extra = 1
    autocomplete_fields = ['course']
    fields = ['course', 'include_terms', 'sort_order']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    """
    Admin for Product
    
    Features:
    - Bundle management via inline
    - Price display
    - Status indicators
    - Bulk actions
    """
    
    list_display = [
        'name', 'product_type', 'price_display', 'grade_level',
        'subject', 'is_free', 'is_active', 'is_featured', 'created_at'
    ]
    list_filter = [
        'product_type', 'is_free', 'is_active', 'is_featured',
        'grade_level', 'subject', 'created_at'
    ]
    search_fields = ['name', 'description', 'short_description']
    prepopulated_fields = {'slug': ('name',)}
    autocomplete_fields = ['moodle_course', 'grade_level', 'subject']
    
    inlines = [ProductBundleInline]
    
    actions = ['activate_products', 'deactivate_products', 'feature_products']
    
    def price_display(self, obj):
        """Display price with FREE indicator"""
        if obj.is_free:
            return format_html('<span style="color: {}; font-weight: {};">{}</span>', 'green', 'bold', 'FREE')
        return f"R{obj.price}"
    
    price_display.short_description = 'Price'
    
    def activate_products(self, request, queryset):
        """Bulk activate products"""
        count = queryset.update(is_active=True)
        self.message_user(request, f"{count} products activated")
    activate_products.short_description = "Activate selected products"
    
    def deactivate_products(self, request, queryset):
        """Bulk deactivate products"""
        count = queryset.update(is_active=False)
        self.message_user(request, f"{count} products deactivated")
    deactivate_products.short_description = "Deactivate selected products"
    
    def feature_products(self, request, queryset):
        """Bulk feature products"""
        count = queryset.update(is_featured=True)
        self.message_user(request, f"{count} products featured")
    feature_products.short_description = "Feature selected products"
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'slug', 'product_type', 'description', 'short_description')
        }),
        ('Pricing', {
            'fields': ('price', 'currency', 'is_free', 'auto_enroll_free')
        }),
        ('Course Link', {
            'fields': ('moodle_course', 'term_number'),
            'description': 'For single course or term products'
        }),
        ('Categorization', {
            'fields': ('grade_level', 'subject')
        }),
        ('Content', {
            'fields': ('image', 'included_features', 'course_outline', 'learning_outcomes', 'requirements')
        }),
        ('Access', {
            'fields': ('duration_months', 'preview_url')
        }),
        ('Status', {
            'fields': ('is_active', 'is_featured', 'sort_order', 'published_at')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ['is_free', 'created_at', 'updated_at']


# ==================== ORDER ====================

class OrderItemInline(admin.TabularInline):
    """Inline for order items"""
    model = OrderItem
    extra = 0
    readonly_fields = ['product', 'quantity', 'unit_price', 'subtotal']
    can_delete = False
    
    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """
    Admin for Order
    
    Security:
    - Read-only critical fields
    - Audit trail
    - Status tracking
    """
    
    list_display = [
        'order_number', 'user', 'status', 'total_amount',
        'payment_method', 'enrollment_status', 'created_at'
    ]
    list_filter = [
        'status', 'payment_method', 'enrollment_processed',
        'created_at', 'paid_at'
    ]
    search_fields = [
        'order_number', 'user__username', 'user__email',
        'user__phone_number', 'payment_reference'
    ]
    
    inlines = [OrderItemInline]
    
    actions = ['mark_as_completed', 'process_enrollment']
    
    def enrollment_status(self, obj):
        """
        Display enrollment processing status with visual indicator
        Shows whether order has been processed for course access
        """
        from django.utils.safestring import mark_safe
        
        if obj.enrollment_processed:
            return mark_safe('<span style="color: green;">✓ Processed</span>')
        elif obj.enrollment_error:
            return mark_safe(
                f'<span style="color: red;" title="{obj.enrollment_error}">✗ Failed</span>'
            )
        else:
            return mark_safe('<span style="color: orange;">⏳ Pending</span>')

    enrollment_status.short_description = 'Enrollment'
    
    def mark_as_completed(self, request, queryset):
        """Bulk mark orders as completed (admin override)"""
        count = queryset.filter(status=Order.PENDING).update(
            status=Order.COMPLETED,
            paid_at=timezone.now()
        )
        self.message_user(request, f"{count} orders marked as completed")
    mark_as_completed.short_description = "Mark as completed (override)"
    
    def process_enrollment(self, request, queryset):
        """Manually trigger enrollment processing"""
        from shop.services import EnrollmentService
        service = EnrollmentService()
        
        count = 0
        for order in queryset.filter(enrollment_processed=False):
            try:
                service.process_order_enrollment(order)
                count += 1
            except Exception as e:
                self.message_user(request, f"Error processing {order.order_number}: {str(e)}", level='error')
        
        self.message_user(request, f"{count} orders processed")
    process_enrollment.short_description = "Process enrollment"
    
    fieldsets = (
        ('Order Information', {
            'fields': ('order_number', 'user', 'status')
        }),
        ('Pricing', {
            'fields': ('subtotal', 'discount_amount', 'total_amount', 'currency')
        }),
        ('Payment', {
            'fields': ('payment_method', 'payment_reference', 'payment_metadata', 'paid_at')
        }),
        ('Enrollment', {
            'fields': ('enrollment_processed', 'enrollment_processed_at', 'enrollment_error')
        }),
        ('Notes', {
            'fields': ('customer_notes', 'admin_notes')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = [
        'order_number', 'user', 'subtotal', 'total_amount',
        'payment_reference', 'paid_at', 'enrollment_processed',
        'enrollment_processed_at', 'created_at', 'updated_at'
    ]


# ==================== STUDENT ENROLLMENT ====================

@admin.register(StudentEnrollment)
class StudentEnrollmentAdmin(admin.ModelAdmin):
    """Admin for StudentEnrollment"""
    
    list_display = [
        'user', 'product', 'is_active', 'is_free',
        'progress_display', 'expires_at', 'created_at'
    ]
    list_filter = [
        'is_active', 'is_free', 'created_at',
        'expires_at', 'completed_at'
    ]
    search_fields = [
        'user__username', 'user__email', 'product__name'
    ]
    autocomplete_fields = ['user', 'product', 'order', 'enrolled_by']
    
    actions = ['extend_access', 'mark_completed', 'deactivate_and_unenroll', 'force_sync_to_moodle']
    
    def progress_display(self, obj):
        """Display progress as colored bar with percentage"""
        percent = min(100, max(0, float(obj.progress_percentage)))
        
        if percent == 0:
            color = '#d1d5db'
        elif percent < 30:
            color = '#ef4444'
        elif percent < 70:
            color = '#f59e0b'
        elif percent < 100:
            color = '#3b82f6'
        else:
            color = '#10b981'
        
        # Format progress outside of format_html to avoid format spec issues
        progress_text = f"{float(obj.progress_percentage):.1f}%"
        
        return format_html(
            '<div style="width: 100px; background: #eee; border-radius: 3px;">'
            '<div style="width: {}%; background: {}; height: 20px; border-radius: 3px;"></div>'
            '</div> {}',
            percent,
            color,
            progress_text
        )

    progress_display.short_description = 'Progress'

    
    def extend_access(self, request, queryset):
        """Extend access by 1 month"""
        from datetime import timedelta
        for enrollment in queryset:
            enrollment.expires_at += timedelta(days=30)
            enrollment.save()
        self.message_user(request, f"{queryset.count()} enrollments extended by 1 month")
    extend_access.short_description = "Extend access by 1 month"
    
    def mark_completed(self, request, queryset):
        """Mark enrollments as completed"""
        count = queryset.update(
            completed_at=timezone.now(),
            progress_percentage=Decimal('100.00')
        )
        self.message_user(request, f"{count} enrollments marked as completed")
    mark_completed.short_description = "Mark as completed"
    
    def deactivate_and_unenroll(self, request, queryset):
        """
        Deactivate enrollments and unenroll from Moodle
        Triggers automatic Moodle sync via signals
        """
        count = 0
        for enrollment in queryset.filter(is_active=True):
            enrollment.is_active = False
            enrollment.save()  # This triggers the signal
            count += 1
        
        self.message_user(
            request,
            f"Deactivated {count} enrollments. Users will be unenrolled from Moodle automatically."
        )
    deactivate_and_unenroll.short_description = "Deactivate and unenroll from Moodle"
    
    def force_sync_to_moodle(self, request, queryset):
        """Force immediate sync to Moodle"""
        from integration.models import MoodleInstance, CourseEnrollment, ProductBundle
        from integration.services import MoodleIntegrationService
        
        try:
            instance = MoodleInstance.objects.filter(is_active=True, is_primary=True).first()
            if not instance:
                self.message_user(request, "No Moodle instance configured", level='error')
                return
            
            service = MoodleIntegrationService(instance)
            synced = 0
            errors = 0
            
            for student_enrollment in queryset:
                # Get related course enrollments
                course_enrollments = []
                
                if student_enrollment.product.product_type == 'bundle':
                    bundle_items = ProductBundle.objects.filter(
                        product=student_enrollment.product
                    ).select_related('course')
                    
                    for item in bundle_items:
                        try:
                            ce = CourseEnrollment.objects.get(
                                user=student_enrollment.user,
                                course=item.course
                            )
                            course_enrollments.append(ce)
                        except CourseEnrollment.DoesNotExist:
                            pass
                
                elif student_enrollment.product.moodle_course:
                    try:
                        ce = CourseEnrollment.objects.get(
                            user=student_enrollment.user,
                            course=student_enrollment.product.moodle_course
                        )
                        course_enrollments.append(ce)
                    except CourseEnrollment.DoesNotExist:
                        pass
                
                # Sync each enrollment
                for ce in course_enrollments:
                    try:
                        mapping = student_enrollment.user.moodle_mapping
                        
                        if ce.is_active:
                            # Enroll
                            service.client.enrol_user(
                                mapping.moodle_user_id,
                                ce.course.moodle_course_id
                            )
                        else:
                            # Unenroll
                            service.client.unenrol_user(
                                mapping.moodle_user_id,
                                ce.course.moodle_course_id
                            )
                        
                        ce.mark_synced()
                        synced += 1
                    
                    except Exception as e:
                        ce.mark_failed(str(e))
                        errors += 1
            
            self.message_user(
                request,
                f"Synced {synced} enrollments to Moodle. {errors} errors."
            )
        
        except Exception as e:
            self.message_user(request, f"Sync failed: {str(e)}", level='error')
    
    force_sync_to_moodle.short_description = "Force sync to Moodle now"
    
    fieldsets = (
        ('Enrollment', {
            'fields': ('user', 'product', 'order', 'enrolled_by')
        }),
        ('Access Period', {
            'fields': ('starts_at', 'expires_at', 'is_active')
        }),
        ('Type', {
            'fields': ('is_free',)
        }),
        ('Progress', {
            'fields': ('progress_percentage', 'last_accessed', 'completed_at')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ['created_at', 'updated_at']

# ==================== DISCOUNT CODE ====================

@admin.register(DiscountCode)
class DiscountCodeAdmin(admin.ModelAdmin):
    """Admin for DiscountCode"""
    
    list_display = [
        'code', 'discount_display', 'usage_display',
        'valid_from', 'valid_until', 'is_active'
    ]
    list_filter = ['discount_type', 'is_active', 'valid_from', 'valid_until']
    search_fields = ['code', 'description']
    filter_horizontal = ['applies_to_products']
    
    def discount_display(self, obj):
        """Display discount value"""
        if obj.discount_type == DiscountCode.PERCENTAGE:
            return f"{obj.discount_value}%"
        return f"R{obj.discount_value}"
    discount_display.short_description = 'Discount'
    
    def usage_display(self, obj):
        """Display usage stats"""
        if obj.max_uses:
            return f"{obj.times_used} / {obj.max_uses}"
        return f"{obj.times_used} / ∞"
    usage_display.short_description = 'Usage'
    
    fieldsets = (
        ('Code Information', {
            'fields': ('code', 'description')
        }),
        ('Discount', {
            'fields': ('discount_type', 'discount_value')
        }),
        ('Applicability', {
            'fields': ('applies_to_products', 'minimum_purchase')
        }),
        ('Usage Limits', {
            'fields': ('max_uses', 'max_uses_per_user', 'times_used')
        }),
        ('Validity', {
            'fields': ('valid_from', 'valid_until', 'is_active')
        }),
    )
    
    readonly_fields = ['times_used']