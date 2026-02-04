from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html
from django.utils import timezone
from .models import User, UserProfile, LoginAttempt


class UserProfileInline(admin.StackedInline):
    """
    Inline profile editing within user admin
    Allows admins to manage user and profile in single view
    """
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Profile Information'
    
    fieldsets = (
        ('Geographic Information', {
            'fields': ('country', 'region', 'village_town')
        }),
        ('Ministry Details', {
            'fields': ('church_name', 'church_affiliation', 'church_size', 
                      'years_in_ministry', 'position_title')
        }),
        ('Personal Information', {
            'fields': ('date_of_birth', 'education_level', 'languages_spoken')
        }),
        ('Contact & Media', {
            'fields': ('whatsapp_number', 'profile_photo', 'bio')
        }),
        ('Administrative', {
            'fields': ('registration_notes',),
            'classes': ('collapse',)
        }),
    )


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """
    Custom admin for User model
    Extends Django's UserAdmin with role-based filtering and verification workflow
    """
    
    inlines = [UserProfileInline]
    
    # List display configuration
    list_display = [
        'username', 'get_full_name_display', 'phone_number', 'role', 
        'is_verified_display', 'country_display', 'created_at'
    ]
    
    list_filter = [
        'role', 'is_verified', 'is_active', 'is_staff', 
        'created_at', 'last_login'
    ]
    
    search_fields = [
        'username', 'first_name', 'last_name', 'phone_number', 
        'email', 'profile__country', 'profile__region', 'profile__church_name'
    ]
    
    ordering = ['-created_at']
    
    # Fieldsets for add/change forms
    fieldsets = (
        ('Account Information', {
            'fields': ('username', 'password')
        }),
        ('Personal Details', {
            'fields': ('first_name', 'last_name', 'email', 'phone_number')
        }),
        ('Role & Permissions', {
            'fields': ('role', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')
        }),
        ('Verification', {
            'fields': ('is_verified', 'verified_by', 'verified_at'),
            'description': 'Account verification is required for students to access courses'
        }),
        ('Sync & Timestamps', {
            'fields': ('last_sync', 'last_login', 'date_joined'),
            'classes': ('collapse',)
        }),
    )
    
    add_fieldsets = (
        ('Account Information', {
            'classes': ('wide',),
            'fields': ('username', 'password1', 'password2'),
        }),
        ('Personal Details', {
            'classes': ('wide',),
            'fields': ('first_name', 'last_name', 'email', 'phone_number'),
        }),
        ('Role', {
            'classes': ('wide',),
            'fields': ('role',),
        }),
    )
    
    readonly_fields = ['date_joined', 'last_login', 'created_at', 'updated_at']
    
    # Custom display methods
    def get_full_name_display(self, obj):
        """Display full name in list view"""
        return obj.get_full_name() or '-'
    get_full_name_display.short_description = 'Full Name'
    
    def is_verified_display(self, obj):
        """Visual indicator for verification status"""
        if obj.is_verified:
            return format_html('<span style="color: {};">{}</span>', 'green', '✓ Verified')
        return format_html('<span style="color: {};">{}</span>', 'orange', '⚠ Pending')

    is_verified_display.short_description = 'Verified'
    is_verified_display.admin_order_field = 'is_verified'
    
    def country_display(self, obj):
        """Display country from profile"""
        try:
            return obj.profile.country
        except UserProfile.DoesNotExist:
            return '-'
    country_display.short_description = 'Country'
    
    # Custom actions
    actions = ['verify_users', 'unverify_users', 'export_to_csv']
    
    def verify_users(self, request, queryset):
        """
        Bulk verify selected users
        Sets verification timestamp and verifying admin
        """
        updated = queryset.update(
            is_verified=True,
            verified_by=request.user,
            verified_at=timezone.now()
        )
        self.message_user(
            request, 
            f'{updated} user(s) successfully verified.'
        )
    verify_users.short_description = 'Verify selected users'
    
    def unverify_users(self, request, queryset):
        """Bulk unverify users if needed"""
        updated = queryset.update(
            is_verified=False,
            verified_by=None,
            verified_at=None
        )
        self.message_user(
            request, 
            f'{updated} user(s) unverified.'
        )
    unverify_users.short_description = 'Unverify selected users'
    
    def export_to_csv(self, request, queryset):
        """
        Export selected users to CSV
        Useful for offline reports and analytics
        """
        import csv
        from django.http import HttpResponse
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="users_export.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'Username', 'Full Name', 'Phone', 'Email', 'Role', 
            'Country', 'Region', 'Church', 'Verified', 'Created'
        ])
        
        for user in queryset.select_related('profile'):
            try:
                profile = user.profile
                writer.writerow([
                    user.username,
                    user.get_full_name(),
                    user.phone_number,
                    user.email,
                    user.get_role_display(),
                    profile.country,
                    profile.region,
                    profile.church_name,
                    'Yes' if user.is_verified else 'No',
                    user.created_at.strftime('%Y-%m-%d'),
                ])
            except UserProfile.DoesNotExist:
                # Handle users without profiles
                writer.writerow([
                    user.username,
                    user.get_full_name(),
                    user.phone_number,
                    user.email,
                    user.get_role_display(),
                    '-', '-', '-',
                    'Yes' if user.is_verified else 'No',
                    user.created_at.strftime('%Y-%m-%d'),
                ])
        
        return response
    export_to_csv.short_description = 'Export to CSV'
    
    def get_queryset(self, request):
        """
        Optimise queryset with select_related for better performance
        Reduces database queries when displaying list
        """
        qs = super().get_queryset(request)
        return qs.select_related('profile', 'verified_by')


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """
    Standalone admin for UserProfile
    Useful for bulk profile management and analytics
    """
    
    list_display = [
        'user', 'country', 'region', 'church_name', 'church_size',
        'years_in_ministry', 'education_level', 'profile_complete_status'
    ]
    
    list_filter = [
        'country', 'region', 'church_size', 'education_level',
        'created_at'
    ]
    
    search_fields = [
        'user__username', 'user__first_name', 'user__last_name',
        'country', 'region', 'village_town', 'church_name', 'church_affiliation'
    ]
    
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('User Link', {
            'fields': ('user',)
        }),
        ('Geographic Information', {
            'fields': ('country', 'region', 'village_town')
        }),
        ('Ministry Details', {
            'fields': ('church_name', 'church_affiliation', 'church_size', 
                      'years_in_ministry', 'position_title')
        }),
        ('Personal Information', {
            'fields': ('date_of_birth', 'education_level', 'languages_spoken')
        }),
        ('Contact & Media', {
            'fields': ('whatsapp_number', 'profile_photo', 'bio')
        }),
        ('Administrative', {
            'fields': ('registration_notes', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def profile_complete_status(self, obj):
        """Visual indicator of profile completion"""
        if obj.is_profile_complete():
            return format_html('<span style="color: green;">✓ Complete</span>')
        return format_html('<span style="color: orange;">Incomplete</span>')
    profile_complete_status.short_description = 'Profile Status'


@admin.register(LoginAttempt)
class LoginAttemptAdmin(admin.ModelAdmin):
    """
    Admin for monitoring login attempts
    Critical for security in shared-device environments
    """
    
    list_display = [
        'username', 'phone_number', 'ip_address', 'success', 
        'timestamp', 'user_agent_short'
    ]
    
    list_filter = ['success', 'timestamp']
    
    search_fields = ['username', 'phone_number', 'ip_address']
    
    readonly_fields = [
        'username', 'phone_number', 'ip_address', 'user_agent', 
        'success', 'timestamp'
    ]
    
    date_hierarchy = 'timestamp'
    
    ordering = ['-timestamp']
    
    def user_agent_short(self, obj):
        """Truncate user agent for display"""
        if obj.user_agent:
            return obj.user_agent[:50] + '...' if len(obj.user_agent) > 50 else obj.user_agent
        return '-'
    user_agent_short.short_description = 'User Agent'
    
    def has_add_permission(self, request):
        """Prevent manual addition of login attempts"""
        return False
    
    def has_change_permission(self, request, obj=None):
        """Make login attempts read-only"""
        return False
    
    # Allow deletion for cleanup purposes
    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser