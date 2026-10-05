import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from scholr.features import (
    academics_enabled,
    bridge_enabled,
    enrollment_enabled,
    lms_enabled,
    self_registration_enabled,
)

from .forms import (
    CustomAuthenticationForm,
    PastorRegistrationForm,
    UserProfileForm,
    UserUpdateForm,
)
from .models import LoginAttempt, User, UserProfile

logger = logging.getLogger(__name__)

SCHOOL_STAFF_ROLES = ('teacher', 'hod', 'principal', 'admin_clerk')
SESSION_REMEMBER_SECONDS = 1209600


def get_client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def record_login_attempt(request, username, phone_number='', success=False):
    LoginAttempt.objects.create(
        username=username or '',
        phone_number=phone_number or '',
        ip_address=get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', ''),
        success=success,
    )


def get_or_create_profile(user):
    """A missing profile should never 500 a dashboard."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def safe_redirect_target(request, fallback='accounts:dashboard'):
    """Reject off-site values in next= so login cannot be used as an open redirect."""
    target = request.POST.get('next') or request.GET.get('next')
    if target and url_has_allowed_host_and_scheme(
        url=target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return fallback


# ==================== REGISTRATION ====================

class RegisterView(CreateView):
    """Public sign-up, available only where self-registration is switched on."""

    model = User
    form_class = PastorRegistrationForm
    template_name = 'accounts/register.html'
    success_url = reverse_lazy('accounts:profile_complete')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('accounts:dashboard')

        if not self_registration_enabled():
            if enrollment_enabled():
                messages.info(
                    request,
                    'Accounts here are created by the school. Start with an application.',
                )
                return redirect('enrollment:landing')
            messages.info(request, 'Accounts are created by an administrator.')
            return redirect('accounts:login')

        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        login(
            self.request,
            self.object,
            backend='django.contrib.auth.backends.ModelBackend',
        )
        messages.success(
            self.request,
            f'Welcome {self.object.get_full_name()}. Please complete your profile.',
        )
        return response

    def form_invalid(self, form):
        messages.error(self.request, 'Please correct the errors below and try again.')
        return super().form_invalid(form)


# ==================== AUTHENTICATION ====================

class LoginView(View):
    """Username or phone sign-in, with attempt logging and remember-me."""

    template_name = 'accounts/login.html'
    form_class = CustomAuthenticationForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('accounts:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return self.render_form(request, self.form_class())

    def post(self, request):
        form = self.form_class(request, data=request.POST)
        submitted_username = request.POST.get('username', '')

        if not form.is_valid():
            record_login_attempt(request, submitted_username, success=False)
            messages.error(request, 'Invalid username, phone number or password.')
            return self.render_form(request, form)

        user = form.get_user()
        phone = getattr(user, 'phone_number', '') or ''

        if not user.is_active:
            record_login_attempt(request, submitted_username, phone, success=False)
            messages.error(
                request,
                'This account has been deactivated. Please contact an administrator.',
            )
            return self.render_form(request, form)

        if user.role == User.STUDENT and not user.is_verified:
            record_login_attempt(request, submitted_username, phone, success=False)
            messages.warning(
                request,
                'Your account is awaiting verification. '
                'Please contact your local training centre.',
            )
            return self.render_form(request, form)

        login(request, user)

        if form.cleaned_data.get('remember_me'):
            request.session.set_expiry(SESSION_REMEMBER_SECONDS)
        else:
            request.session.set_expiry(0)

        record_login_attempt(request, user.username, phone, success=True)
        messages.success(request, f'Welcome back, {user.get_full_name()}.')

        return redirect(safe_redirect_target(request))

    def render_form(self, request, form):
        return render(request, self.template_name, {
            'form': form,
            'next': request.POST.get('next') or request.GET.get('next', ''),
        })


@require_POST
@login_required
def logout_view(request):
    logout(request)
    messages.info(request, 'You have been signed out.')
    return redirect('accounts:login')


# ==================== DASHBOARD ROUTING ====================

def resolve_dashboard(user):
    """Return the URL name this user's role should land on."""
    if user.role == User.ADMIN:
        return 'accounts:admin_dashboard'

    if user.role == User.DEAN:
        return 'accounts:dean_dashboard'

    if user.role == 'parent':
        return 'academics:portal_dashboard' if academics_enabled() else 'accounts:profile'

    if user.role in SCHOOL_STAFF_ROLES:
        if not academics_enabled():
            return 'accounts:profile'
        if user.role == 'principal':
            return 'academics:analytics_dashboard'
        if user.role == 'admin_clerk' and enrollment_enabled():
            return 'enrollment:admin_queue'
        return 'academics:learner_list'

    if user.role == 'learner':
        if bridge_enabled():
            return 'academics:learner_lms_dashboard'
        if academics_enabled():
            return 'academics:portal_dashboard'
        return 'accounts:profile'

    if user.role == User.STUDENT:
        return 'accounts:student_dashboard'

    return 'accounts:profile'


@login_required
def dashboard_view(request):
    target = resolve_dashboard(request.user)
    if target == 'accounts:profile':
        messages.info(request, 'No dashboard is configured for your role yet.')
    return redirect(target)


@login_required
def student_dashboard_view(request):
    """Commercial LMS learner: enrolled products and profile status."""
    if request.user.role != User.STUDENT:
        raise PermissionDenied

    from accounts.models import StudentEnrollment

    user = request.user
    profile = get_or_create_profile(user)
    profile_complete = profile.is_profile_complete()

    enrolled_courses = (
        StudentEnrollment.objects
        .filter(user=user, is_active=True)
        .select_related('product')
        .prefetch_related('product__bundle_items__course__category')
        .order_by('-created_at')
    )

    recent_activity = [
        f'Enrolled in {enrollment.product.name} on '
        f'{enrollment.created_at.strftime("%d %B %Y")}'
        for enrollment in enrolled_courses[:5]
        if enrollment.created_at
    ]

    if not profile_complete:
        messages.info(request, 'Complete your profile to access all features.')

    return render(request, 'accounts/dashboards/student.html', {
        'profile': profile,
        'profile_complete': profile_complete,
        'enrolled_courses': enrolled_courses,
        'certificates': [],
        'recent_activity': recent_activity,
    })


@login_required
def dean_dashboard_view(request):
    """Regional dean: learners under supervision and pending verifications."""
    if request.user.role != User.DEAN:
        raise PermissionDenied

    students = request.user.get_students().select_related('profile')
    pending = students.filter(is_verified=False)

    stats = {
        'total_students': students.count(),
        'verified_students': students.filter(is_verified=True).count(),
        'pending_verifications': pending.count(),
        'active_students': students.filter(is_active=True, is_verified=True).count(),
    }

    return render(request, 'accounts/dashboards/dean.html', {
        'students': students[:10],
        'pending_verifications': pending[:5],
        'stats': stats,
    })


@login_required
def admin_dashboard_view(request):
    """System-wide counts, recent sign-ups and failed sign-in attempts."""
    if request.user.role != User.ADMIN:
        raise PermissionDenied

    student_counts = User.objects.filter(role=User.STUDENT).aggregate(
        total=Count('id'),
        verified=Count('id', filter=Q(is_verified=True)),
        pending=Count('id', filter=Q(is_verified=False)),
    )

    stats = {
        'total_users': User.objects.count(),
        'total_students': student_counts['total'],
        'total_deans': User.objects.filter(role=User.DEAN).count(),
        'verified_students': student_counts['verified'],
        'pending_verifications': student_counts['pending'],
    }

    countries = (
        UserProfile.objects
        .exclude(country='')
        .values('country')
        .annotate(count=Count('user'))
        .order_by('-count')[:5]
    )

    return render(request, 'accounts/dashboards/admin.html', {
        'stats': stats,
        'recent_users': User.objects.select_related('profile').order_by('-created_at')[:10],
        'recent_failed_logins': LoginAttempt.objects.filter(success=False).order_by('-timestamp')[:10],
        'countries': countries,
    })


# ==================== PROFILE ====================

class ProfileDetailView(LoginRequiredMixin, DetailView):
    model = UserProfile
    template_name = 'accounts/profile_detail.html'
    context_object_name = 'profile'

    def get_object(self, queryset=None):
        return get_or_create_profile(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['profile_complete'] = self.object.is_profile_complete()
        return context


class ProfileUpdateView(LoginRequiredMixin, UpdateView):
    """Edits the User and UserProfile records together."""

    model = UserProfile
    form_class = UserProfileForm
    template_name = 'accounts/profile_edit.html'
    success_url = reverse_lazy('accounts:profile')

    def get_object(self, queryset=None):
        return get_or_create_profile(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault('user_form', UserUpdateForm(instance=self.request.user))
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        profile_form = self.get_form()
        user_form = UserUpdateForm(request.POST, request.FILES, instance=request.user)

        if profile_form.is_valid() and user_form.is_valid():
            return self.forms_valid(profile_form, user_form)
        return self.forms_invalid(profile_form, user_form)

    def forms_valid(self, profile_form, user_form):
        with transaction.atomic():
            user_form.save()
            profile_form.save()
        messages.success(self.request, 'Your profile has been updated.')
        return redirect(self.success_url)

    def forms_invalid(self, profile_form, user_form):
        messages.error(self.request, 'Please correct the errors below.')
        return self.render_to_response(
            self.get_context_data(form=profile_form, user_form=user_form)
        )


@login_required
def profile_complete_view(request):
    """Guided completion of the fields required before full access."""
    user = request.user
    profile = get_or_create_profile(user)

    if request.method == 'POST':
        profile_form = UserProfileForm(request.POST, request.FILES, instance=profile)
        user_form = UserUpdateForm(request.POST, request.FILES, instance=user)

        if profile_form.is_valid() and user_form.is_valid():
            with transaction.atomic():
                user_form.save()
                profile_form.save()
            messages.success(request, 'Profile complete. You now have full access.')
            return redirect('accounts:dashboard')

        messages.error(request, 'Please correct the errors below.')
    else:
        profile_form = UserProfileForm(instance=profile)
        user_form = UserUpdateForm(instance=user)

    required_fields = [
        profile.country,
        profile.region,
        profile.church_name,
        profile.education_level,
        user.first_name,
        user.last_name,
    ]
    completed = sum(1 for value in required_fields if value)

    return render(request, 'accounts/profile_complete.html', {
        'profile_form': profile_form,
        'user_form': user_form,
        'profile': profile,
        'completion_percentage': round(completed / len(required_fields) * 100),
    })


# ==================== LEARNER MANAGEMENT ====================

class StudentListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """Deans see their own region, administrators see everyone."""

    model = User
    template_name = 'accounts/student_list.html'
    context_object_name = 'students'
    paginate_by = 25

    def test_func(self):
        return self.request.user.role in (User.DEAN, User.ADMIN)

    def get_queryset(self):
        queryset = self.request.user.get_students().select_related('profile')

        search = self.request.GET.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(username__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(phone_number__icontains=search)
                | Q(profile__church_name__icontains=search)
            )

        verified = self.request.GET.get('verified', '')
        if verified == 'yes':
            queryset = queryset.filter(is_verified=True)
        elif verified == 'no':
            queryset = queryset.filter(is_verified=False)

        if self.request.user.role == User.ADMIN:
            country = self.request.GET.get('country', '').strip()
            if country:
                queryset = queryset.filter(profile__country=country)

        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        context['verified'] = self.request.GET.get('verified', '')
        context['country'] = self.request.GET.get('country', '')

        if self.request.user.role == User.ADMIN:
            context['countries'] = (
                UserProfile.objects
                .exclude(country='')
                .values_list('country', flat=True)
                .distinct()
                .order_by('country')
            )
        return context


def assert_can_manage_student(request, student):
    """Deans are limited to their own region; administrators are not."""
    if not request.user.can_verify_users():
        raise PermissionDenied

    if request.user.role == User.DEAN:
        dean_region = getattr(getattr(request.user, 'profile', None), 'region', None)
        student_region = getattr(getattr(student, 'profile', None), 'region', None)
        if not dean_region or dean_region != student_region:
            raise PermissionDenied


def queue_post_verification_work(student, verified_by):
    """
    Push the Moodle account and any paid-but-unprocessed orders onto the queue.
    Verification itself must not fail if the task layer is unavailable, so the
    caller is told what did not happen instead of losing the verification.
    """
    if not lms_enabled():
        return 0, None

    try:
        from accounts.models import Order
        from shop.tasks import process_order_enrollment_task, sync_moodle_users
    except ImportError as exc:
        logger.error('Post-verification tasks unavailable: %s', exc)
        return 0, 'Moodle sync could not be queued.'

    transaction.on_commit(
        lambda: sync_moodle_users.delay(
            user_ids=[student.id], initiated_by_id=verified_by.id,
        )
    )

    paid_statuses = [
        value for value in (
            getattr(Order, 'COMPLETED', None),
            getattr(Order, 'FREE', None),
        ) if value is not None
    ]

    if not paid_statuses:
        logger.error('Order status constants COMPLETED/FREE not found.')
        return 0, 'Pending orders could not be checked.'

    order_ids = list(
        Order.objects
        .filter(user=student, status__in=paid_statuses, enrollment_processed=False)
        .values_list('id', flat=True)
    )

    for order_id in order_ids:
        transaction.on_commit(
            lambda oid=order_id: process_order_enrollment_task.delay(oid)
        )

    return len(order_ids), None


@login_required
def verify_student_view(request, user_id):
    """Verify a learner, then queue their Moodle sync and any waiting enrolments."""
    student = get_object_or_404(
        User.objects.select_related('profile'), id=user_id, role=User.STUDENT,
    )
    assert_can_manage_student(request, student)

    if request.method != 'POST':
        return render(request, 'accounts/verify_student.html', {
            'student': student,
            'profile': getattr(student, 'profile', None),
        })

    if student.is_verified:
        messages.info(request, f'{student.get_full_name()} is already verified.')
        return redirect('accounts:student_detail', user_id=student.id)

    with transaction.atomic():
        student.is_verified = True
        student.verified_by = request.user
        student.verified_at = timezone.now()
        student.save(update_fields=['is_verified', 'verified_by', 'verified_at'])

        queued_orders, warning = queue_post_verification_work(student, request.user)

    if warning:
        messages.warning(
            request,
            f'{student.get_full_name()} was verified, but {warning} '
            f'Run the sync manually from the integration dashboard.',
        )
    elif queued_orders:
        messages.success(
            request,
            f'{student.get_full_name()} verified. '
            f'{queued_orders} order(s) queued for enrolment.',
        )
    else:
        messages.success(request, f'{student.get_full_name()} has been verified.')

    return redirect('accounts:student_detail', user_id=student.id)


@require_POST
@login_required
def unverify_student_view(request, user_id):
    """Remove verification. The previous verifier is kept as an audit trail."""
    student = get_object_or_404(User, id=user_id, role=User.STUDENT)
    assert_can_manage_student(request, student)

    student.is_verified = False
    student.save(update_fields=['is_verified'])

    messages.warning(request, f'Verification removed for {student.get_full_name()}.')
    return redirect('accounts:student_detail', user_id=student.id)


@login_required
def student_detail_view(request, user_id):
    if request.user.role not in (User.DEAN, User.ADMIN):
        raise PermissionDenied

    student = get_object_or_404(
        User.objects.select_related('profile'), id=user_id, role=User.STUDENT,
    )

    if request.user.role == User.DEAN:
        assert_can_manage_student(request, student)

    from accounts.models import StudentEnrollment

    enrollments = (
        StudentEnrollment.objects
        .filter(user=student)
        .select_related('product')
        .order_by('-created_at')
    )

    return render(request, 'accounts/student_detail.html', {
        'student': student,
        'profile': getattr(student, 'profile', None),
        'enrollments': enrollments,
        'courses': enrollments,
        'progress': {},
        'certificates': [],
    })


# ==================== ANALYTICS ====================

@login_required
def analytics_view(request):
    if request.user.role != User.ADMIN:
        raise PermissionDenied

    student_counts = User.objects.filter(role=User.STUDENT).aggregate(
        total=Count('id'),
        verified=Count('id', filter=Q(is_verified=True)),
    )
    total_students = student_counts['total']
    verified_students = student_counts['verified']

    recent_registrations = (
        User.objects
        .filter(created_at__gte=timezone.now() - timedelta(days=30))
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(count=Count('id'))
        .order_by('day')
    )

    countries = (
        UserProfile.objects
        .exclude(country='')
        .values('country')
        .annotate(count=Count('user'))
        .order_by('-count')
    )

    regions = (
        UserProfile.objects
        .exclude(region='')
        .values('region', 'country')
        .annotate(count=Count('user'))
        .order_by('-count')[:20]
    )

    education = (
        UserProfile.objects
        .exclude(education_level='')
        .values('education_level')
        .annotate(count=Count('user'))
        .order_by('-count')
    )

    church_sizes = (
        UserProfile.objects
        .exclude(church_size='')
        .values('church_size')
        .annotate(count=Count('user'))
        .order_by('-count')
    )

    return render(request, 'accounts/analytics.html', {
        'total_users': User.objects.count(),
        'total_students': total_students,
        'verified_students': verified_students,
        'verification_rate': (verified_students / total_students * 100) if total_students else 0,
        'countries': countries,
        'regions': regions,
        'education': education,
        'church_sizes': church_sizes,
        'recent_registrations': list(recent_registrations),
    })