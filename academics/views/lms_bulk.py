"""
Bulk LMS operations for the academics module.

Two views:
  BulkCreateAccountsView — create LMS accounts for a whole grade or stream
  BulkEnrollView         — enrol a whole grade or stream in one online course

Both views follow the same pattern:
  GET (no params)         → form only
  GET (grade/stream set)  → form + preview of affected learners
  POST                    → fire Celery task → redirect

Celery handles the actual work so the HTTP request never times out,
even for cohorts of several hundred learners.
"""
import logging

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from scholr.features import bridge_enabled
from ..mixins import SeniorManagementMixin
from ..models.learner import Learner, LearnerEnrollment
from ..models.lms_link import LearnerLMSAccount
from ..models.school import AcademicYear

logger = logging.getLogger(__name__)


# ==================== BULK ACCOUNT CREATION ====================

class BulkCreateAccountsView(SeniorManagementMixin, View):
    """
    Create LMS accounts for every learner in a grade (or specific stream)
    who does not already have one.

    Credentials are intentionally not shown in bulk mode — learners use
    SSO on first access and are prompted to change their password by Moodle.

    The actual creation runs via Celery so large cohorts don't time out.
    """

    template_name = 'academics/lms/bulk_create.html'

    def _without_accounts(self, current_year, grade_id='', stream_id=''):
        """
        Return a Learner queryset: enrolled this year, active, no existing LMS account.
        Filtered by grade and optionally by stream.
        """
        enrolled_qs = LearnerEnrollment.objects.filter(
            academic_year=current_year, is_active=True,
        )
        if grade_id.isdigit():
            enrolled_qs = enrolled_qs.filter(grade_id=grade_id)
        if stream_id.isdigit():
            enrolled_qs = enrolled_qs.filter(stream_id=stream_id)

        enrolled_ids   = enrolled_qs.values_list('learner_id', flat=True)
        has_account_ids = LearnerLMSAccount.objects.values_list('learner_id', flat=True)

        return (
            Learner.objects.filter(pk__in=enrolled_ids, is_active=True)
            .exclude(pk__in=has_account_ids)
            .order_by('last_name', 'first_name')
        )

    def _streams_for_grade(self, current_year, grade_id):
        from ..models.structure import GradeStream
        if not grade_id.isdigit() or not current_year:
            return GradeStream.objects.none()
        return GradeStream.objects.filter(
            grade_id=grade_id, academic_year=current_year, is_active=True,
        ).order_by('stream_name')

    def get(self, request):
        if not bridge_enabled():
            return redirect('academics:lms_accounts')

        current_year = AcademicYear.get_current()
        from ..models.structure import Grade

        grade_id  = request.GET.get('grade', '')
        stream_id = request.GET.get('stream', '')

        # Preview only when a grade is selected
        preview = (
            self._without_accounts(current_year, grade_id, stream_id)
            if grade_id and current_year
            else Learner.objects.none()
        )

        return render(request, self.template_name, {
            'current_year':   current_year,
            'grades':         Grade.objects.filter(is_active=True).order_by('grade_number'),
            'streams':        self._streams_for_grade(current_year, grade_id),
            'grade_id':       grade_id,
            'stream_id':      stream_id,
            'preview':        preview,
            'preview_count':  preview.count(),
        })

    def post(self, request):
        if not bridge_enabled():
            return redirect('academics:lms_accounts')

        current_year = AcademicYear.get_current()
        if not current_year:
            messages.error(request, 'No current academic year is configured.')
            return redirect('academics:lms_accounts')

        grade_id  = request.POST.get('grade', '')
        stream_id = request.POST.get('stream', '')

        learner_ids = list(
            self._without_accounts(current_year, grade_id, stream_id)
            .values_list('pk', flat=True)
        )

        if not learner_ids:
            messages.warning(
                request,
                'No learners found without LMS accounts for that selection.',
            )
            return redirect('academics:bulk_create_accounts')

        from academics.tasks import bulk_create_lms_accounts_task
        task = bulk_create_lms_accounts_task.delay(
            learner_ids=learner_ids,
            created_by_id=request.user.pk,
        )

        messages.success(
            request,
            f'Creating accounts for {len(learner_ids)} learner(s) in the background. '
            f'Accounts will appear on this page as they are created. '
            f'(Task {task.id[:8]})',
        )
        return redirect('academics:lms_accounts')


# ==================== BULK ENROLLMENT ====================

class BulkEnrollView(SeniorManagementMixin, View):
    """
    Enrol every learner in a grade or stream in a single online course.

    The task skips learners who:
      - Have no LMS account
      - Have an account not yet synced to Moodle
      - Are already enrolled in the selected course

    The HTTP response never blocks on Moodle — Celery does the work.
    """

    template_name = 'academics/lms/bulk_enroll.html'

    def _eligible_learners(self, current_year, course, grade_id='', stream_id=''):
        """
        Learners enrolled this year who are NOT already in the given course.
        Does NOT filter on account status — the task handles that check.
        """
        from ..models.lms_link import OnlineEnrollment

        enrolled_qs = LearnerEnrollment.objects.filter(
            academic_year=current_year, is_active=True,
        )
        if grade_id.isdigit():
            enrolled_qs = enrolled_qs.filter(grade_id=grade_id)
        if stream_id.isdigit():
            enrolled_qs = enrolled_qs.filter(stream_id=stream_id)

        year_learner_ids = enrolled_qs.values_list('learner_id', flat=True)

        # Exclude anyone already active or pending in this course this year
        already_enrolled_ids = OnlineEnrollment.objects.filter(
            course=course,
            academic_year=current_year,
            status__in=[
                OnlineEnrollment.STATUS_ACTIVE,
                OnlineEnrollment.STATUS_PENDING,
            ],
        ).values_list('learner_id', flat=True)

        return (
            Learner.objects.filter(pk__in=year_learner_ids, is_active=True)
            .exclude(pk__in=already_enrolled_ids)
            .order_by('last_name', 'first_name')
        )

    def _streams_for_grade(self, current_year, grade_id):
        from ..models.structure import GradeStream
        if not grade_id.isdigit() or not current_year:
            return GradeStream.objects.none()
        return GradeStream.objects.filter(
            grade_id=grade_id, academic_year=current_year, is_active=True,
        ).order_by('stream_name')

    def get(self, request):
        if not bridge_enabled():
            return redirect('academics:online_learning_dashboard')

        current_year = AcademicYear.get_current()
        from ..models.structure import Grade
        from integration.models import Course as LMSCourse

        grade_id  = request.GET.get('grade', '')
        stream_id = request.GET.get('stream', '')
        course_id = request.GET.get('course', '')

        courses = LMSCourse.objects.filter(
            sync_status='synced',
            visible=True,
            moodle_course_id__isnull=False,
        ).select_related('category').order_by('category__sort_order', 'title')

        selected_course = None
        preview = Learner.objects.none()

        if grade_id and course_id and current_year:
            selected_course = LMSCourse.objects.filter(pk=course_id).first()
            if selected_course:
                preview = self._eligible_learners(
                    current_year, selected_course, grade_id, stream_id
                )

        # Count how many of the preview learners actually have a synced account
        # so admin can see the effective enrollment count up front.
        synced_ids = set(
            LearnerLMSAccount.objects.filter(
                learner_id__in=preview.values_list('pk', flat=True),
                is_active=True,
            ).select_related('user__moodle_mapping')
            # Evaluated below after queryset materialises in template, but count here
        ) if preview.exists() else set()

        # A simpler server-side count: learners with accounts at all
        has_account_ids = set(
            LearnerLMSAccount.objects.filter(
                learner_id__in=preview.values_list('pk', flat=True),
                is_active=True,
            ).values_list('learner_id', flat=True)
        )

        return render(request, self.template_name, {
            'current_year':     current_year,
            'grades':           Grade.objects.filter(is_active=True).order_by('grade_number'),
            'streams':          self._streams_for_grade(current_year, grade_id),
            'courses':          courses,
            'grade_id':         grade_id,
            'stream_id':        stream_id,
            'course_id':        course_id,
            'selected_course':  selected_course,
            'preview':          preview,
            'preview_count':    preview.count(),
            # How many in the preview actually have an LMS account
            'with_account_count': len(has_account_ids),
        })

    def post(self, request):
        if not bridge_enabled():
            return redirect('academics:online_learning_dashboard')

        current_year = AcademicYear.get_current()
        if not current_year:
            messages.error(request, 'No current academic year is configured.')
            return redirect('academics:online_learning_dashboard')

        from integration.models import Course as LMSCourse

        grade_id  = request.POST.get('grade', '')
        stream_id = request.POST.get('stream', '')
        course_id = request.POST.get('course', '')

        course = LMSCourse.objects.filter(
            pk=course_id, sync_status='synced',
        ).first()
        if not course:
            messages.error(
                request,
                'The selected course was not found or is not synced to Moodle.',
            )
            return redirect('academics:bulk_enroll')

        learner_ids = list(
            self._eligible_learners(current_year, course, grade_id, stream_id)
            .values_list('pk', flat=True)
        )

        if not learner_ids:
            messages.warning(
                request,
                f'No eligible learners for "{course.title}". '
                f'They may already be enrolled or the grade/stream selection returned nothing.',
            )
            return redirect('academics:bulk_enroll')

        from academics.tasks import bulk_enroll_in_course_task
        task = bulk_enroll_in_course_task.delay(
            learner_ids=learner_ids,
            course_id=course.pk,
            enrolled_by_id=request.user.pk,
            academic_year_id=current_year.pk,
        )

        messages.success(
            request,
            f'Enrolling {len(learner_ids)} learner(s) in "{course.title}" in the background. '
            f'Learners without synced accounts will be skipped automatically. '
            f'(Task {task.id[:8]})',
        )
        return redirect('academics:online_learning_dashboard')