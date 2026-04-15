from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, UpdateView, DetailView, ListView
from django.http import JsonResponse
from accounts.models import StudentEnrollment
from integration.views import admin_required
from .models import User, UserProfile, LoginAttempt
from .forms import (
    PastorRegistrationForm, 
    CustomAuthenticationForm,
    UserProfileForm,
    UserUpdateForm
)
from django.db.models import Q, Count



from shop.tasks import process_pending_enrollments_task

@login_required
@admin_required
def verify_student(request, user_id):
    """
    Verify student and trigger pending enrollments
    """
    user = get_object_or_404(User, id=user_id, role=User.STUDENT)
    
    if request.method == 'POST':
        # Verify user
        user.is_verified = True
        user.verified_by = request.user
        user.verified_at = timezone.now()
        user.save(update_fields=['is_verified', 'verified_by', 'verified_at'])
        
        # Trigger Moodle sync for this user
        from shop.tasks import sync_moodle_users
        sync_moodle_users.delay(
            user_ids=[user.id],
            initiated_by_id=request.user.id
        )
        
        # Check for pending enrollments
        from accounts.models import StudentEnrollment
        pending_enrollments = StudentEnrollment.objects.filter(
            user=user,
            is_active=True
        ).count()
        
        if pending_enrollments > 0:
            # Queue enrollment processing
            from shop.tasks import process_order_enrollment_task
            
            # Get user's paid orders that haven't been processed
            from accounts.models import Order
            pending_orders = Order.objects.filter(
                user=user,
                status__in=[Order.COMPLETED, Order.FREE],
                enrollment_processed=False
            )
            
            for order in pending_orders:
                process_order_enrollment_task.delay(order.id)
            
            messages.success(
                request,
                f'✓ User verified! Queued Moodle sync and {pending_enrollments} enrollment(s). '
                f'Check back in 2 minutes.'
            )
        else:
            messages.success(request, f'✓ User {user.username} verified successfully!')
        
        return redirect('accounts:student_detail', user_id=user.id)
    
    context = {
        'student': user,
    }
    
    return render(request, 'accounts/verify_student.html', context)


def get_client_ip(request):
    """
    Extract client IP from request
    Handles proxy headers for accurate IP tracking
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


class RegisterView(CreateView):
    """
    Self-service registration for pastors
    Creates User and UserProfile in single transaction
    Redirects to profile completion after successful registration
    """
    model = User
    form_class = PastorRegistrationForm
    template_name = 'accounts/register.html'
    success_url = reverse_lazy('accounts:profile_complete')
    
    def dispatch(self, request, *args, **kwargs):
        """Redirect authenticated users to dashboard"""
        if request.user.is_authenticated:
            return redirect('accounts:dashboard')
        return super().dispatch(request, *args, **kwargs)
    
    def form_valid(self, form):
        """
        Handle successful registration
        Log the user in automatically and show welcome message
        """
        response = super().form_valid(form)
        
        # Log user in automatically after registration
        login(self.request, self.object, backend='django.contrib.auth.backends.ModelBackend')
        
        messages.success(
            self.request,
            f'Welcome {self.object.get_full_name()}! Your account has been created. '
            f'Please complete your profile to get started.'
        )
        
        # Track successful registration (not a login attempt but useful for analytics)
        LoginAttempt.objects.create(
            username=self.object.username,
            phone_number=self.object.phone_number,
            ip_address=get_client_ip(self.request),
            user_agent=self.request.META.get('HTTP_USER_AGENT', ''),
            success=True
        )
        
        return response
    
    def form_invalid(self, form):
        """Show error message on failed registration"""
        messages.error(
            self.request,
            'There were errors in your registration. Please check the form and try again.'
        )
        return super().form_invalid(form)


class LoginView(View):
    """
    Custom login view supporting username or phone authentication
    Tracks login attempts for security monitoring
    Implements remember-me functionality
    """
    template_name = 'accounts/login.html'
    form_class = CustomAuthenticationForm
    
    def dispatch(self, request, *args, **kwargs):
        """Redirect authenticated users to dashboard"""
        if request.user.is_authenticated:
            return redirect('accounts:dashboard')
        return super().dispatch(request, *args, **kwargs)
    
    def get(self, request):
        """Display login form"""
        form = self.form_class()
        return render(request, self.template_name, {'form': form})
    
    def post(self, request):
        """
        Handle login submission
        Authenticate user and track attempt
        """
        form = self.form_class(request, data=request.POST)
        
        # Get username for logging (before authentication)
        username = request.POST.get('username', '')
        
        if form.is_valid():
            user = form.get_user()
            
            # Check if account is verified (students only)
            if user.role == User.STUDENT and not user.is_verified:
                messages.warning(
                    request,
                    'Your account is pending verification by a dean. '
                    'Please contact your local training centre.'
                )
                
                # Log failed attempt due to unverified status
                LoginAttempt.objects.create(
                    username=username,
                    phone_number=getattr(user, 'phone_number', ''),
                    ip_address=get_client_ip(request),
                    user_agent=request.META.get('HTTP_USER_AGENT', ''),
                    success=False
                )
                
                return render(request, self.template_name, {'form': form})
            
            # Check if account is active
            if not user.is_active:
                messages.error(
                    request,
                    'Your account has been deactivated. Please contact an administrator.'
                )
                
                LoginAttempt.objects.create(
                    username=username,
                    phone_number=getattr(user, 'phone_number', ''),
                    ip_address=get_client_ip(request),
                    user_agent=request.META.get('HTTP_USER_AGENT', ''),
                    success=False
                )
                
                return render(request, self.template_name, {'form': form})
            
            # Log user in
            login(request, user)
            
            # Handle remember-me checkbox
            if not form.cleaned_data.get('remember_me'):
                # Session expires when browser closes
                request.session.set_expiry(0)
            else:
                # Session lasts 2 weeks
                request.session.set_expiry(1209600)
            
            # Log successful attempt
            LoginAttempt.objects.create(
                username=user.username,
                phone_number=user.phone_number,
                ip_address=get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
                success=True
            )
            
            messages.success(request, f'Welcome back, {user.get_full_name()}!')
            
            # Redirect to next or dashboard
            next_url = request.GET.get('next', 'accounts:dashboard')
            return redirect(next_url)
        
        else:
            # Log failed attempt
            LoginAttempt.objects.create(
                username=username,
                phone_number='',
                ip_address=get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
                success=False
            )
            
            messages.error(request, 'Invalid username/phone or password. Please try again.')
        
        return render(request, self.template_name, {'form': form})


@login_required
def logout_view(request):
    """
    Log user out and redirect to login
    Simple and straightforward
    """
    logout(request)
    messages.info(request, 'You have been logged out successfully.')
    return redirect('accounts:login')


@login_required
def dashboard_view(request):
    """
    Role-based dashboard routing
    Directs users to appropriate dashboard based on their role
    """
    user = request.user
    
    if user.role == User.ADMIN:
        return redirect('accounts:admin_dashboard')
    elif user.role == User.DEAN:
        return redirect('accounts:dean_dashboard')
    else:  # STUDENT
        return redirect('accounts:student_dashboard')


@login_required
def student_dashboard_view(request):
    """
    Dashboard for students (pastors)
    Shows course progress, profile completion status, available materials
    """
    # Only students can access this view
    if request.user.role != User.STUDENT:
        raise PermissionDenied
    
    user = request.user
    profile = user.profile
    
    # Check if profile is complete
    profile_complete = profile.is_profile_complete()
    
    # Get enrolled courses (StudentEnrollments)
    from accounts.models import StudentEnrollment
    
    enrolled_courses = StudentEnrollment.objects.filter(
        user=user,
        is_active=True
    ).select_related('product').prefetch_related(
        'product__bundle_items__course__category'
    ).order_by('-created_at')
    
    # Get certificates (placeholder - implement later)
    certificates = []  # TODO: Implement certificate model
    
    # Build recent activity from enrollments
    recent_activity = []
    for enrollment in enrolled_courses[:5]:
        if enrollment.created_at:
            recent_activity.append(
                f"Enrolled in {enrollment.product.name} on "
                f"{enrollment.created_at.strftime('%B %d, %Y')}"
            )
    
    context = {
        'user': user,
        'profile': profile,
        'profile_complete': profile_complete,
        'enrolled_courses': enrolled_courses,
        'certificates': certificates,
        'recent_activity': recent_activity,
    }
    
    # Show profile completion prompt if needed
    if not profile_complete:
        messages.info(
            request,
            'Please complete your profile to access all features.'
        )
    
    return render(request, 'accounts/dashboards/student.html', context)


@login_required
def dean_dashboard_view(request):
    """
    Dashboard for deans (facilitators)
    Shows students under supervision, pending verifications, regional analytics
    """
    # Only deans can access this view
    if request.user.role != User.DEAN:
        raise PermissionDenied
    
    user = request.user
    
    # Get students in dean's region
    students = user.get_students()
    
    # Get pending verifications in region
    pending_verifications = students.filter(is_verified=False)
    
    # Basic statistics
    stats = {
        'total_students': students.count(),
        'verified_students': students.filter(is_verified=True).count(),
        'pending_verifications': pending_verifications.count(),
        'active_students': students.filter(is_active=True, is_verified=True).count(),
    }
    
    context = {
        'user': user,
        'students': students[:10],  # Show recent 10
        'pending_verifications': pending_verifications[:5],
        'stats': stats,
    }
    
    return render(request, 'accounts/dashboards/dean.html', context)


@login_required
def admin_dashboard_view(request):
    """
    Dashboard for system administrators
    Shows system-wide statistics, recent registrations, security alerts
    """
    # Only admins can access this view
    if request.user.role != User.ADMIN:
        raise PermissionDenied
    
    # System-wide statistics
    stats = {
        'total_users': User.objects.count(),
        'total_students': User.objects.filter(role=User.STUDENT).count(),
        'total_deans': User.objects.filter(role=User.DEAN).count(),
        'pending_verifications': User.objects.filter(
            role=User.STUDENT, 
            is_verified=False
        ).count(),
        'verified_students': User.objects.filter(
            role=User.STUDENT, 
            is_verified=True
        ).count(),
    }
    
    # Recent registrations
    recent_users = User.objects.select_related('profile').order_by('-created_at')[:10]
    
    # Recent login attempts (failed)
    recent_failed_logins = LoginAttempt.objects.filter(
        success=False
    ).order_by('-timestamp')[:10]
    
    # Geographic distribution
    countries = UserProfile.objects.values('country').annotate(
        count=Count('country')
    ).order_by('-count')[:5]

    
    context = {
        'stats': stats,
        'recent_users': recent_users,
        'recent_failed_logins': recent_failed_logins,
        'countries': countries,
    }
    
    return render(request, 'accounts/dashboards/admin.html', context)


class ProfileDetailView(LoginRequiredMixin, DetailView):
    """
    View user's own profile
    Shows profile completion status and provides edit link
    """
    model = UserProfile
    template_name = 'accounts/profile_detail.html'
    context_object_name = 'profile'
    
    def get_object(self):
        """Always return current user's profile"""
        return self.request.user.profile
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['profile_complete'] = self.object.is_profile_complete()
        return context


class ProfileUpdateView(LoginRequiredMixin, UpdateView):
    """
    Edit user profile
    Handles both User model fields and UserProfile fields
    Uses two forms in single view
    """
    model = UserProfile
    form_class = UserProfileForm
    template_name = 'accounts/profile_edit.html'
    success_url = reverse_lazy('accounts:profile')
    
    def get_object(self):
        """Always edit current user's profile"""
        return self.request.user.profile
    
    def get_context_data(self, **kwargs):
        """Add user form to context"""
        context = super().get_context_data(**kwargs)
        if 'user_form' not in context:
            context['user_form'] = UserUpdateForm(instance=self.request.user)
        return context
    
    def post(self, request, *args, **kwargs):
        """
        Handle both user and profile forms
        Update both models in single transaction
        """
        self.object = self.get_object()
        profile_form = self.get_form()
        user_form = UserUpdateForm(request.POST, instance=request.user)
        
        if profile_form.is_valid() and user_form.is_valid():
            return self.form_valid(profile_form, user_form)
        else:
            return self.form_invalid(profile_form, user_form)
    
    def form_valid(self, profile_form, user_form):
        """Save both forms in transaction"""
        with transaction.atomic():
            user_form.save()
            profile_form.save()
        
        messages.success(self.request, 'Your profile has been updated successfully.')
        return redirect(self.success_url)
    
    def form_invalid(self, profile_form, user_form):
        """Show errors from both forms"""
        messages.error(
            self.request,
            'There were errors updating your profile. Please check the form.'
        )
        return self.render_to_response(
            self.get_context_data(form=profile_form, user_form=user_form)
        )


@login_required
def profile_complete_view(request):
    """
    Guided profile completion flow
    Shows which fields are missing and why they matter
    """
    user = request.user
    profile = user.profile
    
    if request.method == 'POST':
        profile_form = UserProfileForm(request.POST, request.FILES, instance=profile)
        user_form = UserUpdateForm(request.POST, instance=user)
        
        if profile_form.is_valid() and user_form.is_valid():
            with transaction.atomic():
                user_form.save()
                profile_form.save()
            
            messages.success(request, 'Profile completed! You can now access all features.')
            return redirect('accounts:dashboard')
    else:
        profile_form = UserProfileForm(instance=profile)
        user_form = UserUpdateForm(instance=user)
    
    # Calculate completion percentage
    required_fields = [
        profile.country,
        profile.region,
        profile.church_name,
        profile.education_level,
        user.first_name,
        user.last_name,
    ]
    completed_fields = sum(1 for field in required_fields if field)
    completion_percentage = (completed_fields / len(required_fields)) * 100
    
    context = {
        'profile_form': profile_form,
        'user_form': user_form,
        'completion_percentage': completion_percentage,
        'profile': profile,
    }
    
    return render(request, 'accounts/profile_complete.html', context)


class StudentListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """
    List students for deans/admins
    Deans see only their region, admins see all
    Includes filtering and search capabilities
    """
    model = User
    template_name = 'accounts/student_list.html'
    context_object_name = 'students'
    paginate_by = 25
    
    def test_func(self):
        """Only deans and admins can access"""
        return self.request.user.role in [User.DEAN, User.ADMIN]
    
    def get_queryset(self):
        """
        Filter students based on user role
        Apply search and filters from GET parameters
        """
        queryset = self.request.user.get_students()
        
        # Search functionality
        search = self.request.GET.get('search', '')
        if search:
            queryset = queryset.filter(
                models.Q(username__icontains=search) |
                models.Q(first_name__icontains=search) |
                models.Q(last_name__icontains=search) |
                models.Q(phone_number__icontains=search) |
                models.Q(profile__church_name__icontains=search)
            )
        
        # Filter by verification status
        verified = self.request.GET.get('verified', '')
        if verified == 'yes':
            queryset = queryset.filter(is_verified=True)
        elif verified == 'no':
            queryset = queryset.filter(is_verified=False)
        
        # Filter by country (admins only)
        if self.request.user.role == User.ADMIN:
            country = self.request.GET.get('country', '')
            if country:
                queryset = queryset.filter(profile__country=country)
        
        return queryset.select_related('profile').order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        context['verified'] = self.request.GET.get('verified', '')
        
        # Add countries for admin filter
        if self.request.user.role == User.ADMIN:
            from django.db import models
            context['countries'] = UserProfile.objects.values_list(
                'country', flat=True
            ).distinct().order_by('country')
        
        return context


@login_required
def verify_student_view(request, user_id):
    """
    Verify a student account
    Only accessible by deans and admins
    Creates audit trail of who verified when
    """
    # Check permissions
    if not request.user.can_verify_users():
        raise PermissionDenied
    
    student = get_object_or_404(User, id=user_id, role=User.STUDENT)
    
    # Deans can only verify students in their region
    if request.user.role == User.DEAN:
        if student.profile.region != request.user.profile.region:
            raise PermissionDenied
    
    if request.method == 'POST':
        student.is_verified = True
        student.verified_by = request.user
        student.verified_at = timezone.now()
        student.save()
        
        messages.success(
            request,
            f'{student.get_full_name()} has been verified and can now access courses.'
        )
        
        return redirect('accounts:student_list')
    
    context = {
        'student': student,
        'profile': student.profile,
    }
    
    return render(request, 'accounts/verify_student.html', context)


@login_required
def unverify_student_view(request, user_id):
    """
    Unverify a student account
    Used when verification was given in error or account needs review
    """
    # Check permissions
    if not request.user.can_verify_users():
        raise PermissionDenied
    
    student = get_object_or_404(User, id=user_id, role=User.STUDENT)
    
    # Deans can only unverify students in their region
    if request.user.role == User.DEAN:
        if student.profile.region != request.user.profile.region:
            raise PermissionDenied
    
    if request.method == 'POST':
        student.is_verified = False
        student.verified_by = None
        student.verified_at = None
        student.save()
        
        messages.warning(
            request,
            f'{student.get_full_name()} verification has been removed.'
        )
        
        return redirect('accounts:student_list')
    
    context = {
        'student': student,
    }
    
    return render(request, 'accounts/unverify_student.html', context)


@login_required
def student_detail_view(request, user_id):
    """
    View detailed information about a student
    Available to deans (for their region) and admins
    """
    # Check permissions
    if request.user.role not in [User.DEAN, User.ADMIN]:
        raise PermissionDenied
    
    student = get_object_or_404(
        User.objects.select_related('profile'),
        id=user_id,
        role=User.STUDENT
    )
    
    # Deans can only view students in their region
    if request.user.role == User.DEAN:
        if student.profile.region != request.user.profile.region:
            raise PermissionDenied
    
    context = {
        'student': student,
        'profile': student.profile,
        # These will be populated when LMS is built
        'courses': [],
        'progress': {},
        'certificates': [],
    }
    
    return render(request, 'accounts/student_detail.html', context)


@login_required
def analytics_view(request):
    """
    System analytics dashboard
    Shows demographic breakdowns, geographic distribution, trends
    Admin-only access
    """
    if request.user.role != User.ADMIN:
        raise PermissionDenied
    
    from django.db import models
    from django.db.models import Count, Q
    from datetime import timedelta
    
    # Basic counts
    total_users = User.objects.count()
    total_students = User.objects.filter(role=User.STUDENT).count()
    verified_students = User.objects.filter(role=User.STUDENT, is_verified=True).count()
    
    # Geographic distribution
    countries = UserProfile.objects.values('country').annotate(
        count=Count('country')
    ).order_by('-count')
    
    regions = UserProfile.objects.values('region', 'country').annotate(
        count=Count('region')
    ).order_by('-count')[:20]
    
    # Education levels
    education = UserProfile.objects.exclude(
        education_level=''
    ).values('education_level').annotate(
        count=Count('education_level')
    ).order_by('-count')
    
    # Church sizes
    church_sizes = UserProfile.objects.exclude(
        church_size=''
    ).values('church_size').annotate(
        count=Count('church_size')
    ).order_by('-count')
    
    # Registration trends (last 30 days)
    thirty_days_ago = timezone.now() - timedelta(days=30)
    recent_registrations = User.objects.filter(
        created_at__gte=thirty_days_ago
    ).extra(
        select={'day': 'date(created_at)'}
    ).values('day').annotate(
        count=Count('id')
    ).order_by('day')
    
    context = {
        'total_users': total_users,
        'total_students': total_students,
        'verified_students': verified_students,
        'verification_rate': (verified_students / total_students * 100) if total_students > 0 else 0,
        'countries': countries,
        'regions': regions,
        'education': education,
        'church_sizes': church_sizes,
        'recent_registrations': list(recent_registrations),
    }
    
    return render(request, 'accounts/analytics.html', context)