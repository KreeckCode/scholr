from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.db.models import Q, Count
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.core.paginator import Paginator
from django.db import transaction
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST
from integration.models import Course
from integration.services import MoodleIntegrationService

def is_staff_or_admin(user):
    """Check if user is staff, admin, or dean"""
    return user.is_staff or user.role in ['admin', 'dean']
from accounts.models import User
from .models import (
    MoodleInstance, Course, CourseCategory, 
    CourseEnrollment, SyncLog, MoodleUserMapping
)
from .forms import (
    MoodleConfigurationForm, CourseForm, CourseCategoryForm,
    BulkEnrollmentForm, SyncFilterForm, TestConnectionForm
)
from .services import MoodleIntegrationService
from .exceptions import MoodleConnectionError, MoodleAPIError


def admin_required(view_func):
    """Decorator to ensure only admins can access integration features"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.error(request, 'Please log in to access this page.')
            return redirect('accounts:login')
        
        if request.user.role != User.ADMIN:
            messages.error(request, 'Only administrators can access Moodle integration.')
            return redirect('accounts:dashboard')
        
        return view_func(request, *args, **kwargs)
    
    return wrapper


@login_required
@admin_required
def integration_dashboard(request):
    """
    Main dashboard for Moodle integration
    Shows sync status, pending items, recent activity
    """
    # Get primary Moodle instance
    moodle_instance = MoodleInstance.objects.filter(
        is_active=True,
        is_primary=True
    ).first()
    
    # Connection status
    connection_status = {
        'connected': False,
        'error': None
    }
    
    if moodle_instance:
        connection_status['instance'] = moodle_instance
        connection_status['connected'] = moodle_instance.health_status == 'healthy'
        connection_status['error'] = moodle_instance.last_error
    
    # Pending sync counts
    pending_counts = {
        'categories': CourseCategory.objects.filter(
            is_active=True,
            moodle_category_id__isnull=True
        ).count(),
        'courses': Course.objects.filter(
            sync_status__in=['pending', 'modified', 'failed']
        ).count(),
        'users': MoodleUserMapping.objects.filter(
            sync_status__in=['pending', 'modified', 'failed']
        ).count(),
        'enrollments': CourseEnrollment.objects.filter(
            sync_status='pending'
        ).count(),
    }
    
    pending_counts['total'] = sum(pending_counts.values())
    
    # Recent sync logs
    recent_syncs = SyncLog.objects.select_related(
        'initiated_by', 'moodle_instance'
    ).order_by('-started_at')[:10]
    
    # Course statistics
    course_stats = {
        'total': Course.objects.count(),
        'synced': Course.objects.filter(sync_status='synced').count(),
        'auto_enroll_enabled': Course.objects.filter(auto_enroll_enabled=True).count(),
        'visible': Course.objects.filter(visible=True).count(),
    }
    
    # User sync statistics
    user_stats = {
        'total_verified': User.objects.filter(
            role=User.STUDENT,
            is_verified=True
        ).count(),
        'synced': MoodleUserMapping.objects.filter(
            sync_status='synced'
        ).count(),
        'pending': MoodleUserMapping.objects.filter(
            sync_status='pending'
        ).count(),
    }
    
    context = {
        'connection_status': connection_status,
        'pending_counts': pending_counts,
        'recent_syncs': recent_syncs,
        'course_stats': course_stats,
        'user_stats': user_stats,
    }
    
    return render(request, 'integration/dashboard.html', context)


@login_required
@admin_required
def sync_dashboard(request):
    """
    Sync dashboard showing pending items and recent history
    Optimized with select_related and aggregation
    """
    from django.db.models import Count, Q
    
    # Get counts with single query using aggregation
    category_stats = Category.objects.aggregate(
        pending=Count('id', filter=Q(sync_status='pending') | Q(sync_status='modified'))
    )
    
    course_stats = Course.objects.aggregate(
        pending=Count('id', filter=Q(sync_status='pending') | Q(sync_status='modified'))
    )
    
    user_stats = MoodleUserMapping.objects.aggregate(
        pending=Count('id', filter=Q(sync_status='pending'))
    )
    
    enrollment_stats = CourseEnrollment.objects.aggregate(
        pending=Count('id', filter=Q(sync_status='pending'))
    )
    
    # Get limited pending items with optimization
    pending_categories = Category.objects.filter(
        Q(sync_status='pending') | Q(sync_status='modified')
    ).only('id', 'name', 'sync_status', 'moodle_category_id', 'sync_error')[:10]
    
    pending_courses = Course.objects.filter(
        Q(sync_status='pending') | Q(sync_status='modified')
    ).select_related(
        'category'
    ).only(
        'id', 'title', 'short_name', 'sync_status', 
        'moodle_course_id', 'sync_error',
        'category__id', 'category__name', 'category__moodle_category_id'
    )[:10]
    
    pending_users = User.objects.filter(
        role=User.STUDENT,
        moodle_mapping__sync_status='pending'
    ).select_related(
        'profile'
    ).only(
        'id', 'username', 'first_name', 'last_name',
        'profile__region', 'profile__country', 'profile__church_name'
    )[:10]
    
    # Recent sync history
    sync_history = SyncLog.objects.select_related(
        'initiated_by'
    ).only(
        'id', 'sync_type', 'status', 'started_at', 'completed_at',
        'total_items', 'successful_items',
        'initiated_by__username'
    ).order_by('-started_at')[:20]
    
    context = {
        'pending_categories': pending_categories,
        'pending_courses': pending_courses,
        'pending_users': pending_users,
        'pending_enrollments_count': enrollment_stats['pending'],
        'category_count': category_stats['pending'],
        'course_count': course_stats['pending'],
        'user_count': user_stats['pending'],
        'sync_history': sync_history,
    }
    
    return render(request, 'integration/sync_dashboard.html', context)


@login_required
@admin_required
@require_http_methods(['POST'])
def trigger_sync(request):
    """
    Trigger manual sync operation
    Accepts sync_type parameter to determine what to sync
    """
    sync_type = request.POST.get('sync_type', 'full')
    
    try:
        # Get primary instance
        moodle_instance = MoodleInstance.objects.filter(
            is_active=True,
            is_primary=True
        ).first()
        
        if not moodle_instance:
            messages.error(request, 'No Moodle instance configured.')
            return redirect('integration:sync_dashboard')
        
        # Create service
        service = MoodleIntegrationService(moodle_instance)
        
        # Trigger appropriate sync
        if sync_type == 'categories':
            sync_log = service.sync_categories(initiated_by=request.user)
            messages.success(request, f'Category sync completed: {sync_log.successful_items} successful, {sync_log.failed_items} failed')
        
        elif sync_type == 'courses':
            sync_log = service.sync_courses(initiated_by=request.user)
            messages.success(request, f'Course sync completed: {sync_log.successful_items} successful, {sync_log.failed_items} failed')
        
        elif sync_type == 'users':
            sync_log = service.sync_users(initiated_by=request.user)
            messages.success(request, f'User sync completed: {sync_log.successful_items} successful, {sync_log.failed_items} failed')
        
        elif sync_type == 'enrollments':
            sync_log = service.sync_enrollments(initiated_by=request.user)
            messages.success(request, f'Enrollment sync completed: {sync_log.successful_items} successful, {sync_log.failed_items} failed')
        
        elif sync_type == 'full':
            sync_log = service.full_sync(initiated_by=request.user)
            messages.success(request, f'Full sync completed: {sync_log.successful_items}/{sync_log.total_items} successful')
        
        else:
            messages.error(request, f'Invalid sync type: {sync_type}')
            return redirect('integration:sync_dashboard')
        
        # Show errors if any
        if sync_log.failed_items > 0:
            messages.warning(request, f'{sync_log.failed_items} items failed to sync. Check sync log for details.')
        
        return redirect('integration:sync_log_detail', log_id=sync_log.id)
        
    except MoodleConnectionError as e:
        messages.error(request, f'Cannot connect to Moodle: {str(e)}')
        return redirect('integration:sync_dashboard')
    
    except Exception as e:
        messages.error(request, f'Sync failed: {str(e)}')
        return redirect('integration:sync_dashboard')


@login_required
@admin_required
def test_connection(request):
    """Test connection to Moodle"""
    try:
        moodle_instance = MoodleInstance.objects.filter(
            is_active=True,
            is_primary=True
        ).first()
        
        if not moodle_instance:
            messages.error(request, 'No Moodle instance configured.')
            return redirect('integration:moodle_config')
        
        service = MoodleIntegrationService(moodle_instance)
        result = service.test_connection()
        
        if result['success']:
            messages.success(
                request,
                f"✓ Connected to {result['site_name']} (Moodle {result['moodle_version']})"
            )
        else:
            messages.error(
                request,
                f"✗ Connection failed: {result['error']}"
            )
        
    except Exception as e:
        messages.error(request, f'Connection test failed: {str(e)}')
    
    return redirect('integration:dashboard')


@login_required
@admin_required
def moodle_config(request):
    """Configure Moodle instance settings"""
    instance = MoodleInstance.objects.filter(is_primary=True).first()
    
    if request.method == 'POST':
        form = MoodleConfigurationForm(request.POST, instance=instance)
        
        if form.is_valid():
            # If setting as primary, unmark others
            if form.cleaned_data['is_primary']:
                MoodleInstance.objects.update(is_primary=False)
            
            instance = form.save()
            
            # Test connection
            try:
                service = MoodleIntegrationService(instance)
                result = service.test_connection()
                
                if result['success']:
                    messages.success(request, f"Configuration saved and connection verified!")
                else:
                    messages.warning(request, f"Configuration saved but connection failed: {result['error']}")
            except Exception as e:
                messages.warning(request, f"Configuration saved but connection test failed: {str(e)}")
            
            return redirect('integration:dashboard')
    else:
        form = MoodleConfigurationForm(instance=instance)
    
    context = {
        'form': form,
        'instance': instance,
    }
    
    return render(request, 'integration/moodle_config.html', context)


# ==================== COURSE MANAGEMENT ====================

@login_required
@admin_required
def course_list(request):
    """List all courses with filtering and search"""
    courses = Course.objects.select_related('category', 'created_by').all()
    
    # Search
    search = request.GET.get('search', '')
    if search:
        courses = courses.filter(
            Q(title__icontains=search) |
            Q(short_name__icontains=search) |
            Q(code__icontains=search)
        )
    
    # Filter by category
    category_id = request.GET.get('category', '')
    if category_id:
        courses = courses.filter(category_id=category_id)
    
    # Filter by sync status
    sync_status = request.GET.get('sync_status', '')
    if sync_status:
        courses = courses.filter(sync_status=sync_status)
    
    # Filter by auto-enroll
    auto_enroll = request.GET.get('auto_enroll', '')
    if auto_enroll == 'yes':
        courses = courses.filter(auto_enroll_enabled=True)
    elif auto_enroll == 'no':
        courses = courses.filter(auto_enroll_enabled=False)
    
    # Pagination
    paginator = Paginator(courses, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Get categories for filter
    categories = CourseCategory.objects.filter(is_active=True)
    
    context = {
        'page_obj': page_obj,
        'categories': categories,
        'search': search,
        'selected_category': category_id,
        'selected_sync_status': sync_status,
        'selected_auto_enroll': auto_enroll,
    }
    
    return render(request, 'integration/course_list.html', context)


@login_required
@admin_required
def course_create(request):
    """Create a new course"""
    if request.method == 'POST':
        form = CourseForm(request.POST)
        
        if form.is_valid():
            course = form.save(commit=False)
            course.created_by = request.user
            course.sync_status = 'pending'
            course.save()
            
            messages.success(
                request,
                f'Course "{course.title}" created successfully. Use sync to push to Moodle.'
            )
            return redirect('integration:course_detail', course_id=course.id)
    else:
        form = CourseForm()
    
    context = {
        'form': form,
        'action': 'Create',
    }
    
    return render(request, 'integration/course_form.html', context)

@login_required
def course_edit(request, course_id):
    """Edit course and sync changes to Moodle"""
    course = get_object_or_404(Course, pk=course_id)
    
    if request.method == 'POST':
        form = CourseForm(request.POST, instance=course)
        if form.is_valid():
            course = form.save()
            
            # Sync to Moodle if course has been synced before
            if course.moodle_course_id:  # FIXED - was moodle_id
                try:
                    service = MoodleIntegrationService()
                    service.update_course(course)
                    messages.success(
                        request, 
                        f'Course "{course.title}" updated in both Django and Moodle.'
                    )
                except Exception as e:
                    course.sync_status = 'modified'
                    course.save(update_fields=['sync_status'])
                    messages.warning(
                        request,
                        f'PLEASE SYNC!!!!Course updated in Django but Moodle sync failed: {str(e)}'
                    )
            else:
                messages.success(
                    request, 
                    f'Course "{course.title}" updated successfully.'
                )
            
            return redirect('integration:course_detail', course_id=course.pk)
    else:
        form = CourseForm(instance=course)
    
    return render(request, 'integration/course_form.html', {
        'form': form,
        'course': course,
        'is_edit': True
    })

@login_required
@admin_required
def course_detail(request, course_id):
    """View course details and enrollment"""
    course = get_object_or_404(
        Course.objects.select_related('category', 'created_by'),
        id=course_id
    )
    
    # Get enrollments
    enrollments = CourseEnrollment.objects.filter(
        course=course,
        is_active=True
    ).select_related('user', 'user__profile').order_by('-enrolled_at')
    
    # Pagination
    paginator = Paginator(enrollments, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'course': course,
        'page_obj': page_obj,
        'total_enrollments': enrollments.count(),
    }
    
    return render(request, 'integration/course_detail.html', context)


@login_required
@user_passes_test(is_staff_or_admin)
@require_POST
def course_delete(request, course_id):  # Changed from pk to course_id
    """
    Delete course from both Django and Moodle
    
    Process:
    1. Fetch course from Django
    2. Delete from Moodle via API
    3. Delete from Django database
    4. Redirect with success/error message
    """
    course = get_object_or_404(Course, pk=course_id)  # Changed to use course_id
    course_title = course.title
    moodle_id = course.moodle_course_id  # Changed from moodle_id to moodle_course_id
    
    try:
        # Delete from Moodle first if course has been synced
        if moodle_id:
            service = MoodleIntegrationService()
            service.delete_course(moodle_id)
            messages.success(
                request, 
                f'Course "{course_title}" deleted from Moodle successfully.'
            )
        
        # Delete from Django
        course.delete()
        messages.success(
            request, 
            f'Course "{course_title}" deleted from system successfully.'
        )
        
    except Exception as e:
        messages.error(
            request, 
            f'Error deleting course: {str(e)}'
        )
    
    return redirect('integration:course_list')


@login_required
@admin_required
@require_http_methods(['POST'])
def toggle_auto_enroll(request, course_id):
    """Toggle auto-enrollment for a course"""
    course = get_object_or_404(Course, id=course_id)
    
    course.auto_enroll_enabled = not course.auto_enroll_enabled
    course.save()
    
    status = "enabled" if course.auto_enroll_enabled else "disabled"
    messages.success(request, f'Auto-enrollment {status} for "{course.title}"')
    
    return redirect('integration:course_detail', course_id=course.id)


@login_required
@admin_required
def bulk_enroll(request):
    """Bulk enroll students in a course"""
    if request.method == 'POST':
        form = BulkEnrollmentForm(request.POST)
        
        if form.is_valid():
            course = form.cleaned_data['course']
            filter_type = form.cleaned_data['filter_type']
            
            # Build user queryset based on filter
            users = User.objects.filter(
                role=User.STUDENT,
                is_verified=True,
                is_active=True
            )
            
            if filter_type == 'region':
                region = form.cleaned_data['region']
                users = users.filter(profile__region__iexact=region)
            
            elif filter_type == 'education':
                education = form.cleaned_data['education_level']
                users = users.filter(profile__education_level=education)
            
            elif filter_type == 'church_size':
                size = form.cleaned_data['church_size']
                users = users.filter(profile__church_size=size)
            
            # Create enrollments
            enrolled_count = 0
            already_enrolled = 0
            
            for user in users:
                enrollment, created = CourseEnrollment.objects.get_or_create(
                    user=user,
                    course=course,
                    defaults={
                        'is_active': True,
                        'enrolled_by': request.user,
                        'sync_status': 'pending'
                    }
                )
                
                if created:
                    enrolled_count += 1
                else:
                    already_enrolled += 1
            
            messages.success(
                request,
                f'Enrolled {enrolled_count} students in "{course.title}". '
                f'{already_enrolled} were already enrolled. Use sync to push to Moodle.'
            )
            
            return redirect('integration:course_detail', course_id=course.id)
    else:
        form = BulkEnrollmentForm()
    
    context = {
        'form': form,
    }
    
    return render(request, 'integration/bulk_enroll.html', context)


# ==================== SYNC LOG VIEWS ====================

@login_required
@admin_required
def sync_log_list(request):
    """List all sync logs"""
    logs = SyncLog.objects.select_related('initiated_by', 'moodle_instance').order_by('-started_at')
    
    # Filter by type
    sync_type = request.GET.get('type', '')
    if sync_type:
        logs = logs.filter(sync_type=sync_type)
    
    # Filter by status
    status = request.GET.get('status', '')
    if status:
        logs = logs.filter(status=status)
    
    # Pagination
    paginator = Paginator(logs, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'selected_type': sync_type,
        'selected_status': status,
    }
    
    return render(request, 'integration/sync_log_list.html', context)


@login_required
@admin_required
def sync_log_detail(request, log_id):
    """View detailed sync log"""
    log = get_object_or_404(
        SyncLog.objects.select_related('initiated_by', 'moodle_instance'),
        id=log_id
    )
    
    context = {
        'log': log,
    }
    
    return render(request, 'integration/sync_log_detail.html', context)


# ==================== SSO / STUDENT ACCESS ====================

@login_required
def access_lms(request):
    """
    Generate SSO URL for student to access Moodle
    Available to verified students only
    """
    user = request.user
    
    # Check if student and verified
    if user.role != User.STUDENT or not user.is_verified:
        messages.error(request, 'Only verified students can access the LMS.')
        return redirect('accounts:dashboard')
    
    try:
        # Get primary instance
        moodle_instance = MoodleInstance.objects.filter(
            is_active=True,
            is_primary=True
        ).first()
        
        if not moodle_instance:
            messages.error(
                request,
                'LMS is not configured. Please contact an administrator.'
            )
            return redirect('accounts:dashboard')
        
        # Check connection health
        if moodle_instance.health_status == 'offline':
            messages.error(
                request,
                'LMS is currently offline. Please try again later.'
            )
            return redirect('accounts:dashboard')
        
        # Check if user is synced
        if not hasattr(user, 'moodle_mapping') or not user.moodle_mapping.moodle_user_id:
            messages.error(
                request,
                'Your account is not yet synced to the LMS. Please contact your dean.'
            )
            return redirect('accounts:dashboard')
        
        # Generate SSO URL
        service = MoodleIntegrationService(moodle_instance)
        sso_url = service.generate_sso_url(user)

        # Log the URL for debugging
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Redirecting {user.username} to SSO URL: {sso_url}")

        # Check if it's a proper SSO URL
        if 'auth/userkey/login.php' in sso_url and 'key=' in sso_url:
            logger.info("Using auth_userkey SSO")
        elif 'login/index.php' in sso_url:
            logger.warning("Falling back to manual login page")

        # Redirect to Moodle
        return redirect(sso_url)
        
    except MoodleConnectionError:
        messages.error(
            request,
            'Cannot connect to LMS. It may be offline. Please try again later.'
        )
        return redirect('accounts:dashboard')
    
    except Exception as e:
        messages.error(
            request,
            f'Error accessing LMS: {str(e)}'
        )
        return redirect('accounts:dashboard')


# ==================== AJAX / API ENDPOINTS ====================

@login_required
@admin_required
def get_sync_status(request):
    """
    AJAX endpoint to get current sync status
    Returns JSON with pending counts and health
    """
    moodle_instance = MoodleInstance.objects.filter(
        is_active=True,
        is_primary=True
    ).first()
    
    data = {
        'connected': False,
        'health_status': 'unknown',
        'pending': {
            'categories': 0,
            'courses': 0,
            'users': 0,
            'enrollments': 0,
            'total': 0,
        }
    }
    
    if moodle_instance:
        data['connected'] = True
        data['health_status'] = moodle_instance.health_status
        data['last_check'] = moodle_instance.last_health_check.isoformat() if moodle_instance.last_health_check else None
        
        data['pending']['categories'] = CourseCategory.objects.filter(
            is_active=True,
            moodle_category_id__isnull=True
        ).count()
        
        data['pending']['courses'] = Course.objects.filter(
            sync_status__in=['pending', 'modified', 'failed']
        ).count()
        
        data['pending']['users'] = MoodleUserMapping.objects.filter(
            sync_status__in=['pending', 'modified', 'failed']
        ).count()
        
        data['pending']['enrollments'] = CourseEnrollment.objects.filter(
            sync_status='pending'
        ).count()
        
        data['pending']['total'] = sum(data['pending'].values())
    
    return JsonResponse(data)


@login_required
@admin_required
def check_course_name(request):
    """
    AJAX endpoint to check if course short name is available
    Returns JSON with availability status
    """
    short_name = request.GET.get('short_name', '').strip().upper()
    course_id = request.GET.get('course_id', None)
    
    if not short_name:
        return JsonResponse({'available': False, 'message': 'Short name is required'})
    
    # Check database
    existing = Course.objects.filter(short_name__iexact=short_name)
    
    if course_id:
        existing = existing.exclude(id=course_id)
    
    if existing.exists():
        return JsonResponse({
            'available': False,
            'message': f'Short name "{short_name}" is already in use'
        })
    
    return JsonResponse({
        'available': True,
        'message': f'Short name "{short_name}" is available'
    })


@login_required
@admin_required
def preview_bulk_enrollment(request):
    """AJAX endpoint to preview enrollment count"""
    course_id = request.GET.get('course_id')
    filter_type = request.GET.get('filter_type')
    
    if not course_id or not filter_type:
        return JsonResponse({'count': 0})
    
    from accounts.models import User
    
    users = User.objects.filter(
        role=User.STUDENT,
        is_verified=True,
        is_active=True
    )
    
    if filter_type == 'region':
        region = request.GET.get('region', '')
        if region:
            users = users.filter(profile__region__iexact=region)
    
    elif filter_type == 'education':
        education = request.GET.get('education_level', '')
        if education:
            users = users.filter(profile__education_level=education)
    
    elif filter_type == 'church_size':
        size = request.GET.get('church_size', '')
        if size:
            users = users.filter(profile__church_size=size)
    
    # Exclude already enrolled
    from integration.models import CourseEnrollment
    already_enrolled = CourseEnrollment.objects.filter(
        course_id=course_id,
        is_active=True
    ).values_list('user_id', flat=True)
    
    users = users.exclude(id__in=already_enrolled)
    
    return JsonResponse({'count': users.count()})


@login_required
@user_passes_test(lambda u: u.role == User.ADMIN)
def reconcile_courses(request):
    """
    Reconcile course sync status with Moodle
    Shows what would be fixed and allows user to apply fixes
    """
    from .moodle_client import MoodleClient
    from .models import MoodleInstance, Course
    
    # Get Moodle instance
    moodle_instance = MoodleInstance.objects.filter(
        is_active=True,
        is_primary=True
    ).first()
    
    if not moodle_instance:
        messages.error(request, 'No active Moodle instance configured')
        return redirect('integration:sync')
    
    # Initialize client
    client = MoodleClient(
        base_url=moodle_instance.base_url,
        token=moodle_instance.ws_token
    )
    
    # Get all Moodle courses
    try:
        moodle_courses = {c['shortname']: c for c in client.get_courses()}
    except Exception as e:
        messages.error(request, f'Failed to fetch Moodle courses: {str(e)}')
        return redirect('integration:sync')
    
    # Check all courses
    courses = Course.objects.all()
    issues = []
    
    for course in courses:
        issue = None
        
        if course.moodle_course_id:
            # Verify ID is valid
            existing = client.get_course_by_id(course.moodle_course_id)
            
            if not existing:
                # ID is stale
                if course.short_name in moodle_courses:
                    issue = {
                        'course': course,
                        'type': 'stale_id_reconcile',
                        'message': f'Stale ID {course.moodle_course_id}, but found by shortname',
                        'new_id': moodle_courses[course.short_name]['id'],
                        'severity': 'warning'
                    }
                else:
                    issue = {
                        'course': course,
                        'type': 'stale_id_missing',
                        'message': f'Stale ID {course.moodle_course_id}, course not found in Moodle',
                        'severity': 'error'
                    }
        else:
            # No ID, check if exists
            if course.short_name in moodle_courses:
                issue = {
                    'course': course,
                    'type': 'unlinked',
                    'message': 'Course exists in Moodle but not linked',
                    'new_id': moodle_courses[course.short_name]['id'],
                    'severity': 'info'
                }
            elif course.sync_status == 'synced':
                # Marked as synced but doesn't exist!
                issue = {
                    'course': course,
                    'type': 'false_sync',
                    'message': 'Marked as synced but not found in Moodle',
                    'severity': 'error'
                }
        
        if issue:
            issues.append(issue)
    
    # Handle fix action
    if request.method == 'POST' and 'fix_all' in request.POST:
        fixed_count = 0
        
        for issue in issues:
            course = issue['course']
            
            if issue['type'] == 'stale_id_reconcile':
                course.moodle_course_id = issue['new_id']
                course.sync_status = 'synced'
                course.last_synced = timezone.now()
                course.sync_error = ''
                course.save()
                fixed_count += 1
                
            elif issue['type'] == 'unlinked':
                course.moodle_course_id = issue['new_id']
                course.sync_status = 'synced'
                course.last_synced = timezone.now()
                course.save()
                fixed_count += 1
                
            elif issue['type'] in ['stale_id_missing', 'false_sync']:
                course.moodle_course_id = None
                course.sync_status = 'pending'
                course.save()
                fixed_count += 1
        
        messages.success(request, f'Fixed {fixed_count} issues')
        return redirect('integration:sync_dashboard')
    
    context = {
        'issues': issues,
        'total_courses': courses.count(),
        'moodle_courses_count': len(moodle_courses),
    }
    
    return render(request, 'integration/reconcile_courses.html', context)


from shop.tasks import reconcile_enrollments_task

@login_required
@admin_required
@require_http_methods(['POST'])
def reconcile_enrollments(request):
    """
    Queue enrollment reconciliation as background task
    """
    # Queue the task
    task = reconcile_enrollments_task.delay(initiated_by_id=request.user.id)
    
    messages.info(
        request,
        "Enrollment reconciliation started in background. "
        "This will take 5-10 minutes. Check sync history for results."
    )
    
    return redirect('integration:sync_dashboard')

