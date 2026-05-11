"""
LMS Bridge views — Stage 12A: Account Management + Learner Dashboard.

Two audiences:
  1. Staff (admin, principal, admin_clerk) — manage learner LMS accounts
  2. School learners — their own dashboard when logged in

Stage 12B adds enrollment views to this file.
"""

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from scholr.features import bridge_enabled, lms_enabled
from ..mixins import ManagementMixin, SeniorManagementMixin, StaffMixin
from ..models.learner import Learner, LearnerEnrollment
from ..models.lms_link import LearnerLMSAccount
from ..models.school import AcademicYear, Term
from ..services.lms_bridge import LMSBridgeService

logger = logging.getLogger(__name__)


# ==================== STAFF VIEWS — ACCOUNT MANAGEMENT ====================

class LMSAccountsView(SeniorManagementMixin, View):
    """
    Admin overview of all learner LMS accounts.
    Shows accounts with/without Moodle access, sync status, and quick actions.
    """

    template_name = 'academics/lms/accounts.html'

    def get(self, request):
        if not bridge_enabled():
            messages.error(request, 'Online Learning is not enabled for this deployment.')
            return redirect('academics:learner_list')

        current_year = AcademicYear.get_current()

        # Learners with LMS accounts
        with_account = LearnerLMSAccount.objects.filter(
            is_active=True,
        ).select_related(
            'learner', 'user', 'user__moodle_mapping', 'created_by',
        ).order_by('learner__last_name', 'learner__first_name')

        deactivated = LearnerLMSAccount.objects.filter(
            is_active=False,
        ).select_related('learner', 'user').order_by('-deactivated_at')[:20]

        # Learners without LMS accounts (current year only)
        enrolled_learner_ids = LearnerEnrollment.objects.filter(
            academic_year=current_year, is_active=True,
        ).values_list('learner_id', flat=True) if current_year else []

        has_account_ids = LearnerLMSAccount.objects.values_list('learner_id', flat=True)

        without_account = Learner.objects.filter(
            pk__in=enrolled_learner_ids,
            is_active=True,
        ).exclude(
            pk__in=has_account_ids,
        ).select_related().order_by('last_name', 'first_name')

        # Filter: grade
        grade_filter = request.GET.get('grade', '')
        if grade_filter and grade_filter.isdigit() and current_year:
            enrolled_in_grade = LearnerEnrollment.objects.filter(
                academic_year=current_year,
                grade__pk=grade_filter,
                is_active=True,
            ).values_list('learner_id', flat=True)
            without_account = without_account.filter(pk__in=enrolled_in_grade)

        # Stats
        synced_count = sum(
            1 for acc in with_account if acc.is_synced_to_moodle()
        )

        return render(request, self.template_name, {
            'with_account': with_account,
            'without_account': without_account,
            'deactivated': deactivated,
            'total_with': with_account.count(),
            'total_without': without_account.count(),
            'synced_count': synced_count,
            'current_year': current_year,
            'grade_filter': grade_filter,
        })


class CreateLMSAccountView(SeniorManagementMixin, View):
    """
    Create a User + Moodle account for a learner.
    On success, redirects to credentials view where the password is shown once.
    """

    template_name = 'academics/lms/create_account.html'

    def get(self, request, learner_pk):
        learner = get_object_or_404(Learner, pk=learner_pk, is_active=True)

        # Already has an account?
        try:
            existing = learner.lms_account
            messages.info(
                request,
                f'{learner.get_full_name()} already has a portal account '
                f'(username: {existing.user.username}).',
            )
            return redirect('academics:lms_accounts')
        except LearnerLMSAccount.DoesNotExist:
            pass

        current_year = AcademicYear.get_current()
        enrollment = None
        if current_year:
            enrollment = LearnerEnrollment.objects.filter(
                learner=learner, academic_year=current_year, is_active=True,
            ).select_related('grade', 'stream').first()

        return render(request, self.template_name, {
            'learner': learner,
            'enrollment': enrollment,
        })

    def post(self, request, learner_pk):
        learner = get_object_or_404(Learner, pk=learner_pk, is_active=True)
        notes = request.POST.get('notes', '').strip()

        service = LMSBridgeService()

        try:
            result = service.create_lms_account(
                learner=learner,
                created_by=request.user,
                notes=notes,
            )
        except Exception as exc:
            logger.error(f"LMS account creation failed for {learner}: {exc}")
            messages.error(request, f'Account creation failed: {exc}')
            return redirect('academics:lms_accounts')

        if not result.is_new_account:
            messages.info(
                request,
                f'{learner.get_full_name()} already has an LMS account. '
                f'Use Reset Password if credentials are lost.',
            )
            return redirect('academics:lms_accounts')

        # Store the temp password in session — shown ONCE on credentials page
        request.session[f'lms_temp_pwd_{result.lms_account.pk}'] = result.temporary_password
        return redirect('academics:lms_account_credentials', pk=result.lms_account.pk)


class LMSAccountCredentialsView(SeniorManagementMixin, View):
    """
    Shows the temporary password ONCE after account creation or password reset.
    After this page is viewed, the session key is deleted.
    If the admin navigates back, the password is gone — they must reset it.
    """

    template_name = 'academics/lms/credentials.html'

    def get(self, request, pk):
        lms_account = get_object_or_404(
            LearnerLMSAccount.objects.select_related('learner', 'user'),
            pk=pk,
        )

        # Pop from session — this is the only chance to see it
        session_key = f'lms_temp_pwd_{lms_account.pk}'
        temp_password = request.session.pop(session_key, None)

        if not temp_password:
            messages.warning(
                request,
                'Credentials have already been viewed or the session expired. '
                'Use Reset Password to generate new credentials.',
            )
            return redirect('academics:lms_accounts')

        return render(request, self.template_name, {
            'lms_account': lms_account,
            'username': lms_account.user.username,
            'temp_password': temp_password,
            'is_synced': lms_account.is_synced_to_moodle(),
        })


class ResetLMSPasswordView(SeniorManagementMixin, View):
    """Reset the learner's LMS password and show the new one once."""

    def post(self, request, pk):
        lms_account = get_object_or_404(LearnerLMSAccount, pk=pk, is_active=True)

        service = LMSBridgeService()
        new_password = service.reset_password(lms_account, reset_by=request.user)

        request.session[f'lms_temp_pwd_{lms_account.pk}'] = new_password
        messages.success(
            request,
            f'Password reset for {lms_account.learner.get_full_name()}.',
        )
        return redirect('academics:lms_account_credentials', pk=lms_account.pk)


class DeactivateLMSAccountView(SeniorManagementMixin, View):
    """Suspend Moodle account and deactivate Django User. POST only."""

    def post(self, request, pk):
        lms_account = get_object_or_404(LearnerLMSAccount, pk=pk)

        service = LMSBridgeService()
        service.deactivate_lms_account(lms_account, deactivated_by=request.user)

        messages.success(
            request,
            f'Online access deactivated for {lms_account.learner.get_full_name()}.',
        )
        return redirect('academics:lms_accounts')


class ReactivateLMSAccountView(SeniorManagementMixin, View):
    """Reactivate a previously deactivated account. POST only."""

    def post(self, request, pk):
        lms_account = get_object_or_404(LearnerLMSAccount, pk=pk, is_active=False)

        service = LMSBridgeService()
        service.reactivate_lms_account(lms_account, reactivated_by=request.user)

        messages.success(
            request,
            f'Online access reactivated for {lms_account.learner.get_full_name()}.',
        )
        return redirect('academics:lms_accounts')


class RetryMoodleSyncView(SeniorManagementMixin, View):
    """Retry Moodle user sync for accounts that failed on creation. POST only."""

    def post(self, request, pk):
        lms_account = get_object_or_404(LearnerLMSAccount, pk=pk)

        service = LMSBridgeService()
        success = service.retry_moodle_sync(lms_account)

        if success:
            messages.success(
                request,
                f'{lms_account.learner.get_full_name()} successfully synced to Moodle.',
            )
        else:
            messages.error(
                request,
                'Moodle sync failed. Check the Moodle connection status and try again.',
            )

        return redirect('academics:lms_accounts')


# ==================== LEARNER-FACING VIEW ====================

class LearnerLMSDashboardView(LoginRequiredMixin, View):
    """
    Dashboard for school learners who have an LMS account.

    This is NOT the commercial LMS student dashboard (shop:my_courses).
    School learners land here after login. The accounts dashboard routing
    detects the learner_lms_account reverse relation and redirects here.

    Stage 12A: shows account status and SSO access.
    Stage 12B: will add online course enrollments and progress.
    """

    template_name = 'academics/lms/dashboard.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        # Only school learners with an LMS account can access this view
        if not hasattr(request.user, 'learner_lms_account'):
            raise PermissionDenied(
                'This dashboard is for school learners only.'
            )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        lms_account = request.user.learner_lms_account
        learner = lms_account.learner
        current_year = AcademicYear.get_current()
        current_term = Term.get_current()

        # Fetch school enrollment for grade/class display
        enrollment = None
        if current_year:
            enrollment = LearnerEnrollment.objects.filter(
                learner=learner,
                academic_year=current_year,
                is_active=True,
            ).select_related('grade', 'stream').first()

        # Generate SSO URL if synced to Moodle
        sso_url = None
        service = LMSBridgeService()
        if lms_account.is_synced_to_moodle():
            try:
                sso_url = service.generate_sso_url(lms_account)
            except Exception as exc:
                logger.warning(f"SSO URL failed for {request.user.username}: {exc}")

        # Now populated from the real model
        from academics.models.lms_link import OnlineEnrollment as _OE
        online_enrollments = _OE.objects.filter(
            learner=learner,
            academic_year=current_year,
            status=_OE.STATUS_ACTIVE,
        ).select_related('course__category', 'subject') if current_year else []

        return render(request, self.template_name, {
            'lms_account': lms_account,
            'learner': learner,
            'enrollment': enrollment,
            'current_year': current_year,
            'current_term': current_term,
            'sso_url': sso_url,
            'is_synced': lms_account.is_synced_to_moodle(),
            'sync_status': lms_account.moodle_sync_status(),
            'online_enrollments': online_enrollments,
        })
    


# ==================== STAFF VIEWS — ONLINE ENROLLMENT ====================

class OnlineLearningDashboardView(SeniorManagementMixin, View):
    """
    School-wide overview of online learning.
    Shows LMS account counts, enrollment counts, progress distribution.
    """

    template_name = 'academics/lms/online_dashboard.html'

    def get(self, request):
        if not bridge_enabled():
            return redirect('academics:learner_list')

        current_year = AcademicYear.get_current()
        current_term = Term.get_current()

        from academics.models.lms_link import OnlineEnrollment
        from integration.models import Course

        total_accounts = LearnerLMSAccount.objects.filter(is_active=True).count()
        synced_accounts = sum(
            1 for acc in LearnerLMSAccount.objects.filter(is_active=True)
            if acc.is_synced_to_moodle()
        )

        active_enrollments = OnlineEnrollment.objects.filter(
            academic_year=current_year,
            status=OnlineEnrollment.STATUS_ACTIVE,
        ).count() if current_year else 0

        # Average progress across all active enrollments
        from django.db.models import Avg
        avg_progress = OnlineEnrollment.objects.filter(
            academic_year=current_year,
            status=OnlineEnrollment.STATUS_ACTIVE,
        ).aggregate(avg=Avg('progress_percentage'))['avg'] if current_year else None

        if avg_progress:
            avg_progress = round(float(avg_progress), 1)

        # Per-learner enrollment rows (with progress)
        recent = OnlineEnrollment.objects.filter(
            academic_year=current_year,
            status=OnlineEnrollment.STATUS_ACTIVE,
        ).select_related(
            'learner', 'course', 'subject', 'enrolled_by'
        ).order_by('-enrolled_at')[:20] if current_year else []

        # Available courses summary
        available_courses = Course.objects.filter(
            sync_status='synced', visible=True, moodle_course_id__isnull=False
        ).count()

        # Learners with LMS accounts but no current enrollments
        enrolled_learner_ids = OnlineEnrollment.objects.filter(
            academic_year=current_year,
            status=OnlineEnrollment.STATUS_ACTIVE,
        ).values_list('learner_id', flat=True) if current_year else []

        accounts_without_enrollment = LearnerLMSAccount.objects.filter(
            is_active=True,
        ).exclude(
            learner_id__in=enrolled_learner_ids
        ).select_related('learner').count()

        return render(request, self.template_name, {
            'current_year': current_year,
            'current_term': current_term,
            'total_accounts': total_accounts,
            'synced_accounts': synced_accounts,
            'active_enrollments': active_enrollments,
            'avg_progress': avg_progress,
            'recent': recent,
            'available_courses': available_courses,
            'accounts_without_enrollment': accounts_without_enrollment,
        })


class LearnerOnlineCoursesView(StaffMixin, View):
    """
    All online enrollments for a specific learner.
    Shows progress bars, status badges, and withdraw/enroll actions.
    Linked from the Online Learning tab on the learner detail page.
    """

    template_name = 'academics/lms/learner_online.html'

    def get(self, request, learner_pk):
        learner = get_object_or_404(Learner, pk=learner_pk, is_active=True)
        current_year = AcademicYear.get_current()

        from academics.models.lms_link import OnlineEnrollment

        active_enrollments = OnlineEnrollment.objects.filter(
            learner=learner,
            academic_year=current_year,
            status=OnlineEnrollment.STATUS_ACTIVE,
        ).select_related(
            'course__category', 'subject', 'enrolled_by', 'course_enrollment'
        ).order_by('-enrolled_at') if current_year else []

        past_enrollments = OnlineEnrollment.objects.filter(
            learner=learner,
            status__in=[
                OnlineEnrollment.STATUS_WITHDRAWN,
                OnlineEnrollment.STATUS_COMPLETED,
            ],
        ).select_related('course', 'withdrawn_by').order_by('-withdrawn_at')[:10]

        # LMS account info
        lms_account = None
        try:
            lms_account = learner.lms_account
        except LearnerLMSAccount.DoesNotExist:
            pass

        return render(request, self.template_name, {
            'learner': learner,
            'lms_account': lms_account,
            'active_enrollments': active_enrollments,
            'past_enrollments': past_enrollments,
            'current_year': current_year,
            'can_enroll': request.user.role in {
                'admin', 'principal', 'admin_clerk', 'hod'
            },
            'can_withdraw': request.user.role in {'admin', 'principal'},
        })


class AvailableCoursesView(ManagementMixin, View):
    """
    Browse available online courses to enroll a learner in.
    Shows all synced+visible courses with enrollment status for the learner.
    """

    template_name = 'academics/lms/available_courses.html'

    def get(self, request, learner_pk):
        learner = get_object_or_404(Learner, pk=learner_pk, is_active=True)
        current_year = AcademicYear.get_current()

        from integration.models import Course as LMSCourse
        from academics.models.lms_link import OnlineEnrollment

        # Already enrolled course IDs for this learner this year
        enrolled_ids = set(
            OnlineEnrollment.objects.filter(
                learner=learner,
                academic_year=current_year,
                status=OnlineEnrollment.STATUS_ACTIVE,
            ).values_list('course_id', flat=True)
        ) if current_year else set()

        # All available courses
        courses_qs = LMSCourse.objects.filter(
            sync_status='synced',
            visible=True,
            moodle_course_id__isnull=False,
        ).select_related('category').order_by('category__sort_order', 'title')

        # Search
        search = request.GET.get('q', '').strip()
        if search:
            from django.db.models import Q
            courses_qs = courses_qs.filter(
                Q(title__icontains=search) |
                Q(summary__icontains=search) |
                Q(short_name__icontains=search)
            )

        # Category filter
        category_id = request.GET.get('category', '')
        if category_id.isdigit():
            courses_qs = courses_qs.filter(category_id=category_id)

        # Annotate with enrollment count for capacity display
        from django.db.models import Count
        courses_qs = courses_qs.annotate(
            enrolled_count=Count('enrollments', filter=__import__(
                'django.db.models', fromlist=['Q']
            ).Q(enrollments__is_active=True))
        )

        # Build course rows with "already enrolled" flag
        course_rows = [
            {
                'course': c,
                'already_enrolled': c.pk in enrolled_ids,
                'is_full': bool(
                    c.enrollment_capacity
                    and c.enrolled_count >= c.enrollment_capacity
                ),
            }
            for c in courses_qs
        ]

        from integration.models import CourseCategory
        categories = CourseCategory.objects.filter(is_active=True).order_by('sort_order')

        return render(request, self.template_name, {
            'learner': learner,
            'course_rows': course_rows,
            'current_year': current_year,
            'search': search,
            'categories': categories,
            'selected_category': category_id,
        })


class EnrollConfirmView(ManagementMixin, View):
    """
    Enrollment confirmation form for a specific learner + course combination.
    GET: shows course details + reason/subject form.
    POST: calls LMSBridgeService.enroll_in_course().
    """

    template_name = 'academics/lms/enroll_confirm.html'

    def _get_objects(self, learner_pk, course_pk):
        from integration.models import Course as LMSCourse
        learner = get_object_or_404(Learner, pk=learner_pk, is_active=True)
        course = get_object_or_404(LMSCourse, pk=course_pk)
        return learner, course

    def get(self, request, learner_pk, course_pk):
        learner, course = self._get_objects(learner_pk, course_pk)
        current_year = AcademicYear.get_current()

        from academics.models.curriculum import SchoolSubject
        subjects = SchoolSubject.objects.filter(is_active=True).order_by('sort_order', 'name')

        # LMS account status
        lms_account = None
        try:
            lms_account = learner.lms_account
        except LearnerLMSAccount.DoesNotExist:
            pass

        return render(request, self.template_name, {
            'learner': learner,
            'course': course,
            'lms_account': lms_account,
            'current_year': current_year,
            'subjects': subjects,
        })

    def post(self, request, learner_pk, course_pk):
        learner, course = self._get_objects(learner_pk, course_pk)
        current_year = AcademicYear.get_current()

        reason = request.POST.get('enrollment_reason', '').strip()
        subject_id = request.POST.get('subject_id', '').strip()

        subject = None
        if subject_id.isdigit():
            from academics.models.curriculum import SchoolSubject
            subject = SchoolSubject.objects.filter(pk=subject_id).first()

        service = LMSBridgeService()

        try:
            online_enrollment = service.enroll_in_course(
                learner=learner,
                course=course,
                enrolled_by=request.user,
                reason=reason,
                subject=subject,
                academic_year=current_year,
            )
            messages.success(
                request,
                f'{learner.get_full_name()} enrolled in "{course.title}" successfully.',
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(
                'academics:enroll_confirm',
                learner_pk=learner_pk,
                course_pk=course_pk,
            )
        except Exception as exc:
            logger.error(f'Enrollment error: {exc}')
            messages.error(request, f'Enrollment failed: {exc}')
            return redirect(
                'academics:enroll_confirm',
                learner_pk=learner_pk,
                course_pk=course_pk,
            )

        return redirect('academics:learner_online_courses', learner_pk=learner_pk)


class WithdrawEnrollmentView(SeniorManagementMixin, View):
    """Withdraw a learner from an online course. POST only."""

    def post(self, request, enrollment_pk):
        from academics.models.lms_link import OnlineEnrollment
        enrollment = get_object_or_404(
            OnlineEnrollment.objects.select_related('learner', 'course'),
            pk=enrollment_pk,
        )

        reason = request.POST.get('reason', '').strip()
        service = LMSBridgeService()

        try:
            service.withdraw_enrollment(enrollment, withdrawn_by=request.user, reason=reason)
            messages.success(
                request,
                f'{enrollment.learner.get_full_name()} withdrawn from '
                f'"{enrollment.course.title}".',
            )
        except Exception as exc:
            messages.error(request, f'Withdrawal failed: {exc}')

        return redirect(
            'academics:learner_online_courses',
            learner_pk=enrollment.learner_id,
        )


class SyncProgressView(ManagementMixin, View):
    """
    Manually trigger progress sync from Moodle for a specific learner.
    POST only — shows updated progress on redirect.
    """

    def post(self, request, learner_pk):
        learner = get_object_or_404(Learner, pk=learner_pk, is_active=True)

        service = LMSBridgeService()
        result = service.sync_progress_for_learner(learner)

        if result.get('success'):
            synced = result.get('online_enrollments_updated', 0)
            messages.success(
                request,
                f'Progress synced for {learner.get_full_name()}. '
                f'{synced} course(s) updated.',
            )
        else:
            messages.warning(
                request,
                f"Progress sync issue: {result.get('error', 'Unknown error')}",
            )

        return redirect('academics:learner_online_courses', learner_pk=learner_pk)
    




class OnlineLearningReportView(SeniorManagementMixin, View):
    """
    School-wide online learning progress report.

    Provides:
      - Summary stats (accounts, enrollments, average progress, completions)
      - Progress band distribution (visual breakdown of how far learners have progressed)
      - Per-learner enrollment rows with grade filter
      - Course breakdown (enrollment count, completion count, avg progress per course)

    Senior management only — same access level as the analytics dashboard.
    """

    template_name = 'academics/lms/report.html'

    def get(self, request):
        if not bridge_enabled():
            return redirect('academics:analytics_dashboard')

        current_year = AcademicYear.get_current()
        if not current_year:
            return render(request, self.template_name, {'no_year': True})

        from academics.models.lms_link import OnlineEnrollment
        from django.db.models import Avg, Count

        # ---- Summary stats ----
        total_accounts = LearnerLMSAccount.objects.filter(is_active=True).count()

        _active_oe = OnlineEnrollment.objects.filter(
            academic_year=current_year,
            status=OnlineEnrollment.STATUS_ACTIVE,
        )
        total_enrolled = _active_oe.count()
        avg_progress = _active_oe.aggregate(
            avg=Avg('progress_percentage')
        )['avg']
        avg_progress = round(float(avg_progress), 1) if avg_progress else 0

        completed = OnlineEnrollment.objects.filter(
            academic_year=current_year,
            status=OnlineEnrollment.STATUS_COMPLETED,
        ).count()

        # ---- Progress band distribution ----
        # Each band gets a count + a percentage-of-total for the visual bar.
        # The "Not started" bucket catches 0% exactly.
        band_definitions = [
            ('Not started', 0,   0),
            ('1–25%',       1,   25),
            ('26–50%',      26,  50),
            ('51–75%',      51,  75),
            ('76–99%',      76,  99),
            ('Complete',    100, 100),
        ]
        bands = []
        for label, lo, hi in band_definitions:
            if lo == hi:
                count = _active_oe.filter(progress_percentage=lo).count()
            else:
                count = _active_oe.filter(
                    progress_percentage__gte=lo,
                    progress_percentage__lte=hi,
                ).count()
            bands.append({
                'label':        label,
                'count':        count,
                'pct_of_total': round(count / total_enrolled * 100) if total_enrolled else 0,
            })

        # ---- Grade filter ----
        grade_id = request.GET.get('grade', '')
        learner_rows = OnlineEnrollment.objects.filter(
            academic_year=current_year,
        ).select_related(
            'learner', 'course__category', 'subject',
        ).order_by('learner__last_name', 'learner__first_name', 'course__title')

        if grade_id.isdigit():
            _grade_learner_ids = LearnerEnrollment.objects.filter(
                academic_year=current_year,
                grade_id=grade_id,
                is_active=True,
            ).values_list('learner_id', flat=True)
            learner_rows = learner_rows.filter(learner_id__in=_grade_learner_ids)

        # ---- Course breakdown ----
        # Aggregated per course: total, active, completed, withdrawn, avg progress.
        course_breakdown = OnlineEnrollment.objects.filter(
            academic_year=current_year,
        ).values(
            'course__pk', 'course__title', 'course__short_name',
        ).annotate(
            enrollment_count=Count('id'),
            avg_progress=Avg('progress_percentage'),
            active_count=Count(
                'id', filter=Q(status=OnlineEnrollment.STATUS_ACTIVE)
            ),
            completed_count=Count(
                'id', filter=Q(status=OnlineEnrollment.STATUS_COMPLETED)
            ),
            withdrawn_count=Count(
                'id', filter=Q(status=OnlineEnrollment.STATUS_WITHDRAWN)
            ),
        ).order_by('-enrollment_count')

        from ..models.structure import Grade
        grades = Grade.objects.filter(is_active=True).order_by('grade_number')

        return render(request, self.template_name, {
            'current_year':     current_year,
            # Summary
            'total_accounts':   total_accounts,
            'total_enrolled':   total_enrolled,
            'avg_progress':     avg_progress,
            'completed':        completed,
            # Distribution
            'bands':            bands,
            # Tables
            'learner_rows':     learner_rows,
            'course_breakdown': course_breakdown,
            # Filters
            'grades':           grades,
            'selected_grade':   grade_id,
        })


class ClassOnlineStatusView(StaffMixin, View):
    """
    Homeroom class x online learning status grid.

    Shows every learner in the selected stream alongside:
      - Whether they have an LMS account (and if it is Moodle-synced)
      - Number of active online course enrollments
      - Average progress across those courses

    This gives homeroom teachers a quick support triage view:
    who is enrolled, who has started, who is stuck at 0%.

    Access:
      - Teachers: locked to their own homeroom stream.
      - Admin / HOD / Principal: dropdown to select any stream.
    """

    template_name = 'academics/lms/class_online_status.html'

    def get(self, request):
        if not bridge_enabled():
            return redirect('academics:learner_list')

        current_year = AcademicYear.get_current()
        user = request.user

        from ..models.structure import GradeStream
        from academics.models.lms_link import OnlineEnrollment
        from django.db.models import Avg, Count

        # ---- Determine available streams ----
        if user.role == 'teacher':
            # Teachers see only their homeroom; no stream selector rendered.
            streams = GradeStream.objects.filter(
                homeroom_teacher=user,
                academic_year=current_year,
                is_active=True,
            ).select_related('grade')
            selected_stream = streams.first()
        else:
            streams = GradeStream.objects.filter(
                academic_year=current_year,
                is_active=True,
            ).select_related('grade').order_by(
                'grade__grade_number', 'stream_name'
            )
            stream_id = request.GET.get('stream', '')
            if stream_id.isdigit():
                selected_stream = streams.filter(pk=stream_id).first()
            else:
                # Default to first stream so the page renders something useful.
                selected_stream = streams.first()

        # ---- Learner rows ----
        learner_rows = []
        if selected_stream and current_year:
            from ..models.marks import SubjectEnrollment

            # All distinct learners enrolled in any subject in this stream
            learner_ids = list(
                SubjectEnrollment.objects.filter(
                    stream=selected_stream,
                    academic_year=current_year,
                    is_active=True,
                ).values_list('learner_id', flat=True).distinct()
            )

            learners = Learner.objects.filter(
                pk__in=learner_ids, is_active=True,
            ).order_by('last_name', 'first_name')

            # Set of learner PKs that have an active LMS account
            has_account_ids = set(
                LearnerLMSAccount.objects.filter(
                    learner_id__in=learner_ids,
                    is_active=True,
                ).values_list('learner_id', flat=True)
            )

            # Set of learner PKs whose account is synced to Moodle.
            # is_synced_to_moodle() inspects the moodle_mapping relation,
            # so we load accounts once and evaluate in Python.
            synced_ids = set(
                acc.learner_id
                for acc in LearnerLMSAccount.objects.filter(
                    learner_id__in=learner_ids,
                    is_active=True,
                ).select_related('user__moodle_mapping')
                if acc.is_synced_to_moodle()
            )

            # Aggregate active online enrollment data per learner (single query)
            enrollment_agg = {
                row['learner_id']: row
                for row in OnlineEnrollment.objects.filter(
                    learner_id__in=learner_ids,
                    academic_year=current_year,
                    status=OnlineEnrollment.STATUS_ACTIVE,
                ).values('learner_id').annotate(
                    course_count=Count('id'),
                    avg_progress=Avg('progress_percentage'),
                )
            }

            for learner in learners:
                agg = enrollment_agg.get(learner.pk, {})
                learner_rows.append({
                    'learner':      learner,
                    'has_account':  learner.pk in has_account_ids,
                    'is_synced':    learner.pk in synced_ids,
                    'course_count': agg.get('course_count', 0),
                    'avg_progress': round(float(agg.get('avg_progress') or 0), 1),
                })

        return render(request, self.template_name, {
            'streams':          streams,
            'selected_stream':  selected_stream,
            'learner_rows':     learner_rows,
            'current_year':     current_year,
            'can_enroll':       user.role in {'admin', 'principal', 'admin_clerk', 'hod'},
            'can_create_account': user.role in {'admin', 'principal', 'admin_clerk'},
        })
    