from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils import timezone
from django.db.models import Count, Q
from .models import (
    MoodleInstance, CourseCategory, Course,
    MoodleUserMapping, CourseEnrollment, SyncLog
)


@admin.register(MoodleInstance)
class MoodleInstanceAdmin(admin.ModelAdmin):
    """Admin interface for Moodle instances"""
    
    list_display = [
        'name', 'base_url_display', 'health_status_display',
        'is_active', 'is_primary', 'last_health_check'
    ]
    
    list_filter = ['health_status', 'is_active', 'is_primary']
    
    search_fields = ['name', 'base_url']
    
    readonly_fields = [
        'health_status', 'last_health_check', 'last_error',
        'created_at', 'updated_at'
    ]
    
    fieldsets = (
        ('Instance Details', {
            'fields': ('name', 'base_url', 'ws_token', 'ws_service_name')
        }),
        ('Status', {
            'fields': ('is_active', 'is_primary')
        }),
        ('Health Monitoring', {
            'fields': ('health_status', 'last_health_check', 'last_error'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['test_connection', 'mark_as_primary']
    
    def base_url_display(self, obj):
        """Truncate long URLs"""
        url = obj.base_url
        if len(url) > 50:
            return url[:47] + '...'
        return url
    base_url_display.short_description = 'Base URL'
    
    def health_status_display(self, obj):
        """Visual indicator for health status"""
        colors = {
            'healthy': 'green',
            'degraded': 'orange',
            'offline': 'red',
            'unknown': 'gray'
        }
        
        color = colors.get(obj.health_status, 'gray')
        
        return format_html(
            '<span style="color: {}; font-weight: bold;">● {}</span>',
            color,
            obj.health_status.upper()
        )
    health_status_display.short_description = 'Health'
    
    def test_connection(self, request, queryset):
        """Test connection for selected instances"""
        from .services import MoodleIntegrationService
        
        for instance in queryset:
            try:
                service = MoodleIntegrationService(instance)
                result = service.test_connection()
                
                if result['success']:
                    self.message_user(
                        request,
                        f"✓ {instance.name}: Connected successfully"
                    )
                else:
                    self.message_user(
                        request,
                        f"✗ {instance.name}: {result['error']}",
                        level='ERROR'
                    )
            except Exception as e:
                self.message_user(
                    request,
                    f"✗ {instance.name}: {str(e)}",
                    level='ERROR'
                )
    test_connection.short_description = 'Test connection to selected instances'
    
    def mark_as_primary(self, request, queryset):
        """Mark selected instance as primary"""
        if queryset.count() > 1:
            self.message_user(
                request,
                'Please select only one instance to mark as primary',
                level='ERROR'
            )
            return
        
        # Unmark all others
        MoodleInstance.objects.update(is_primary=False)
        
        # Mark selected as primary
        queryset.update(is_primary=True)
        
        self.message_user(
            request,
            f'{queryset.first().name} is now the primary instance'
        )
    mark_as_primary.short_description = 'Set as primary instance'


@admin.register(CourseCategory)
class CourseCategoryAdmin(admin.ModelAdmin):
    """Admin interface for course categories"""
    
    list_display = [
        'name', 'discipline', 'course_count', 'moodle_category_id',
        'is_active', 'last_synced'
    ]
    
    list_filter = ['discipline', 'is_active']
    
    search_fields = ['name', 'description']
    
    readonly_fields = ['moodle_category_id', 'last_synced', 'created_at', 'updated_at']
    
    ordering = ['sort_order', 'name']
    
    fieldsets = (
        ('Category Information', {
            'fields': ('name', 'discipline', 'description', 'sort_order')
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
        ('Moodle Sync', {
            'fields': ('moodle_category_id', 'last_synced'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def course_count(self, obj):
        """Display number of courses in category"""
        count = obj.courses.count()
        return format_html(
            '<a href="{}?category__id__exact={}">{} courses</a>',
            reverse('admin:integration_course_changelist'),
            obj.id,
            count
        )
    course_count.short_description = 'Courses'


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    """Admin interface for courses"""
    
    list_display = [
        'short_name', 'title', 'category', 'sync_status_display',
        'auto_enroll_enabled', 'enrollment_count', 'visible', 'last_synced'
    ]
    
    list_filter = [
        'sync_status', 'category', 'visible', 'auto_enroll_enabled',
        'category__discipline'
    ]
    
    search_fields = ['title', 'short_name', 'code', 'summary']
    
    readonly_fields = [
        'moodle_course_id', 'sync_status', 'last_synced',
        'sync_error', 'created_by', 'created_at', 'updated_at'
    ]
    
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Course Information', {
            'fields': ('title', 'short_name', 'code', 'category', 'summary')
        }),
        ('Dates & Visibility', {
            'fields': ('start_date', 'end_date', 'visible')
        }),
        ('Enrollment Settings', {
            'fields': ('auto_enroll_enabled', 'enrollment_capacity')
        }),
        ('Moodle Sync', {
            'fields': ('moodle_course_id', 'sync_status', 'last_synced', 'sync_error'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['mark_for_sync', 'enable_auto_enroll', 'disable_auto_enroll']
    
    def sync_status_display(self, obj):
        """Visual indicator for sync status"""
        colors = {
            'pending': 'orange',
            'synced': 'green',
            'modified': 'blue',
            'failed': 'red'
        }
        
        color = colors.get(obj.sync_status, 'gray')
        
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_sync_status_display()
        )
    sync_status_display.short_description = 'Sync Status'
    
    def enrollment_count(self, obj):
        """Display active enrollment count"""
        count = obj.enrollments.filter(is_active=True).count()
        return format_html(
            '<a href="{}?course__id__exact={}">{} students</a>',
            reverse('admin:integration_courseenrollment_changelist'),
            obj.id,
            count
        )
    enrollment_count.short_description = 'Enrolled'
    
    def mark_for_sync(self, request, queryset):
        """Mark selected courses for sync"""
        updated = queryset.filter(
            sync_status='synced'
        ).update(sync_status='modified')
        
        self.message_user(
            request,
            f'{updated} course(s) marked for sync'
        )
    mark_for_sync.short_description = 'Mark for sync'
    
    def enable_auto_enroll(self, request, queryset):
        """Enable auto-enrollment for selected courses"""
        updated = queryset.update(auto_enroll_enabled=True)
        self.message_user(
            request,
            f'Auto-enrollment enabled for {updated} course(s)'
        )
    enable_auto_enroll.short_description = 'Enable auto-enrollment'
    
    def disable_auto_enroll(self, request, queryset):
        """Disable auto-enrollment for selected courses"""
        updated = queryset.update(auto_enroll_enabled=False)
        self.message_user(
            request,
            f'Auto-enrollment disabled for {updated} course(s)'
        )
    disable_auto_enroll.short_description = 'Disable auto-enrollment'


@admin.register(MoodleUserMapping)
class MoodleUserMappingAdmin(admin.ModelAdmin):
    """Admin interface for user mappings"""
    
    list_display = [
        'cas_user', 'moodle_username', 'moodle_user_id',
        'sync_status_display', 'last_synced', 'last_sso_access'
    ]
    
    list_filter = ['sync_status']
    
    search_fields = [
        'cas_user__username', 'cas_user__first_name', 'cas_user__last_name',
        'moodle_username'
    ]
    
    readonly_fields = [
        'moodle_user_id', 'sync_status', 'last_synced',
        'sync_error', 'last_sso_token', 'last_sso_access',
        'created_at', 'updated_at'
    ]
    
    fieldsets = (
        ('User Mapping', {
            'fields': ('cas_user', 'moodle_username', 'moodle_user_id')
        }),
        ('Sync Status', {
            'fields': ('sync_status', 'last_synced', 'sync_error')
        }),
        ('SSO Information', {
            'fields': ('last_sso_token', 'last_sso_access'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def sync_status_display(self, obj):
        """Visual indicator for sync status"""
        colors = {
            'pending': 'orange',
            'synced': 'green',
            'modified': 'blue',
            'failed': 'red'
        }
        
        color = colors.get(obj.sync_status, 'gray')
        
        return format_html(
            '<span style="color: {};">{}</span>',
            color,
            obj.get_sync_status_display()
        )
    sync_status_display.short_description = 'Status'


@admin.register(CourseEnrollment)
class CourseEnrollmentAdmin(admin.ModelAdmin):
    """Admin interface for course enrollments"""
    
    list_display = [
        'user', 'course', 'is_active', 'sync_status_display',
        'enrolled_at', 'last_synced'
    ]
    
    list_filter = ['is_active', 'sync_status', 'enrolled_at']
    
    search_fields = [
        'user__username', 'user__first_name', 'user__last_name',
        'course__title', 'course__short_name'
    ]
    
    readonly_fields = [
        'moodle_enrollment_id', 'enrolled_at', 'enrolled_by',
        'unenrolled_at', 'unenrolled_by', 'sync_status',
        'last_synced', 'sync_error', 'created_at', 'updated_at'
    ]
    
    date_hierarchy = 'enrolled_at'
    
    fieldsets = (
        ('Enrollment Details', {
            'fields': ('user', 'course', 'is_active')
        }),
        ('Enrollment Tracking', {
            'fields': ('enrolled_at', 'enrolled_by', 'unenrolled_at', 'unenrolled_by')
        }),
        ('Moodle Sync', {
            'fields': ('moodle_enrollment_id', 'sync_status', 'last_synced', 'sync_error'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['mark_for_sync']
    
    def sync_status_display(self, obj):
        """Visual indicator for sync status"""
        colors = {
            'pending': 'orange',
            'synced': 'green',
            'failed': 'red'
        }
        
        color = colors.get(obj.sync_status, 'gray')
        
        return format_html(
            '<span style="color: {};">{}</span>',
            color,
            obj.get_sync_status_display()
        )
    sync_status_display.short_description = 'Sync'
    
    def mark_for_sync(self, request, queryset):
        """Mark selected enrollments for sync"""
        updated = queryset.update(sync_status='pending')
        self.message_user(
            request,
            f'{updated} enrollment(s) marked for sync'
        )
    mark_for_sync.short_description = 'Mark for sync'


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    """Admin interface for sync logs"""
    
    list_display = [
        'sync_type', 'status_display', 'success_rate_display',
        'started_at', 'duration_display', 'initiated_by'
    ]
    
    list_filter = ['sync_type', 'status', 'started_at']
    
    search_fields = ['error_summary']
    
    readonly_fields = [
        'sync_type', 'status', 'started_at', 'completed_at',
        'duration_seconds', 'total_items', 'successful_items',
        'failed_items', 'skipped_items', 'details', 'error_summary',
        'initiated_by', 'moodle_instance'
    ]
    
    date_hierarchy = 'started_at'
    
    fieldsets = (
        ('Sync Information', {
            'fields': ('sync_type', 'status', 'initiated_by', 'moodle_instance')
        }),
        ('Timing', {
            'fields': ('started_at', 'completed_at', 'duration_seconds')
        }),
        ('Results', {
            'fields': ('total_items', 'successful_items', 'failed_items', 'skipped_items')
        }),
        ('Details', {
            'fields': ('details', 'error_summary'),
            'classes': ('collapse',)
        }),
    )
    
    def has_add_permission(self, request):
        """Prevent manual creation of sync logs"""
        return False
    
    def has_change_permission(self, request, obj=None):
        """Make sync logs read-only"""
        return False
    
    def status_display(self, obj):
        """Visual indicator for status"""
        colors = {
            'queued': 'gray',
            'running': 'blue',
            'completed': 'green',
            'partial': 'orange',
            'failed': 'red',
            'cancelled': 'gray'
        }
        
        color = colors.get(obj.status, 'gray')
        
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_display.short_description = 'Status'
    
    def success_rate_display(self, obj):
        """Display success rate as percentage"""
        rate = obj.success_rate
        color = 'green' if rate >= 90 else 'orange' if rate >= 70 else 'red'
        
        return format_html(
            '<span style="color: {};">{:.1f}%</span>',
            color,
            rate
        )
    success_rate_display.short_description = 'Success Rate'
    
    def duration_display(self, obj):
        """Format duration nicely"""
        if not obj.duration_seconds:
            return '-'
        
        minutes, seconds = divmod(obj.duration_seconds, 60)
        if minutes > 0:
            return f'{minutes}m {seconds}s'
        return f'{seconds}s'
    duration_display.short_description = 'Duration'