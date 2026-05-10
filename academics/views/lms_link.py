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
from ..mixins import ManagementMixin, SeniorManagementMixin
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

        # Stage 12B will replace this with actual OnlineEnrollment query
        online_enrollments = []

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