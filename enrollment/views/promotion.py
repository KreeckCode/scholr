"""
Grade promotion views for staff.

Handles bulk promotion batches at year-end.
Individual promotions can be triggered from the learner detail page
in the academics module (Stage 5).
"""
import logging

from django.contrib import messages
from django.db.models import Q
from django.forms import formset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from academics.models.learner import Learner, LearnerEnrollment
from academics.models.structure import Grade

from ..forms.promotion import CreateBulkPromotionForm, PromotionDecisionForm
from ..models.promotion import BulkPromotionBatch
from ..permissions import enrollment_admin_required
from ..services.promotion_service import PromotionService

logger = logging.getLogger(__name__)


# ==================== PROMOTION DASHBOARD ====================

class PromotionDashboardView(View):
    """
    Landing page for grade promotion.
    Shows recent batches and quick stats.
    """

    template_name = 'enrollment/promotion/dashboard.html'

    @enrollment_admin_required
    def get(self, request):
        recent_batches = BulkPromotionBatch.objects.all().select_related(
            'from_academic_year',
            'to_academic_year',
            'created_by',
        ).order_by('-created_at')[:10]

        return render(request, self.template_name, {
            'recent_batches': recent_batches,
        })


# ==================== CREATE BATCH ====================

class CreateBulkPromotionView(View):
    """
    Step 1: Select which learners to promote (year/grade/stream).
    Shows a form to configure the batch parameters.
    """

    template_name = 'enrollment/promotion/create_batch.html'

    @enrollment_admin_required
    def get(self, request):
        form = CreateBulkPromotionForm()
        return render(request, self.template_name, {'form': form})

    @enrollment_admin_required
    def post(self, request):
        form = CreateBulkPromotionForm(request.POST)

        if not form.is_valid():
            return render(request, self.template_name, {'form': form})

        # Store form data in session and redirect to configure step
        request.session['promotion_batch_config'] = {
            'label':            form.cleaned_data['label'],
            'from_year_pk':     form.cleaned_data['from_academic_year'].pk,
            'to_year_pk':       form.cleaned_data['to_academic_year'].pk,
            'from_grade_pk':    form.cleaned_data['from_grade'].pk,
            'from_stream_pk':   form.cleaned_data['from_stream'].pk if form.cleaned_data['from_stream'] else None,
            'notes':            form.cleaned_data.get('notes', ''),
        }

        return redirect('enrollment:promotion_configure_batch')


# ==================== CONFIGURE BATCH ====================

class ConfigureBulkPromotionView(View):
    """
    Step 2: For each learner, choose promote/retain/graduate/withdraw.
    Shows a formset with one row per learner.
    """

    template_name = 'enrollment/promotion/configure_batch.html'

    @enrollment_admin_required
    def get(self, request):
        config = request.session.get('promotion_batch_config')
        if not config:
            messages.error(request, 'Session expired. Please start again.')
            return redirect('enrollment:promotion_create_batch')

        from academics.models.school import AcademicYear

        from_year  = AcademicYear.objects.get(pk=config['from_year_pk'])
        from_grade = Grade.objects.get(pk=config['from_grade_pk'])
        from_stream_pk = config.get('from_stream_pk')

        # Get learners
        enrollments = LearnerEnrollment.objects.filter(
            academic_year=from_year,
            grade=from_grade,
            is_active=True,
        ).select_related('learner', 'stream')

        if from_stream_pk:
            enrollments = enrollments.filter(stream_id=from_stream_pk)

        enrollments = enrollments.order_by('learner__last_name', 'learner__first_name')

        if not enrollments.exists():
            messages.warning(
                request,
                f'No active learners found in {from_grade.name} for {from_year}.',
            )
            return redirect('enrollment:promotion_dashboard')

        # Create formset
        PromotionFormSet = formset_factory(
            PromotionDecisionForm,
            extra=0,
        )

        initial_data = []
        for enrollment in enrollments:
            initial_data.append({
                'status': 'promoted',
                'to_grade': Grade.objects.filter(
                    grade_number=from_grade.grade_number + 1
                ).first(),
                'to_stream': enrollment.stream,
            })

        formset = PromotionFormSet(initial=initial_data)

        # Zip learners with forms
        learner_forms = zip(enrollments, formset)

        return render(request, self.template_name, {
            'config':        config,
            'from_year':     from_year,
            'from_grade':    from_grade,
            'enrollments':   enrollments,
            'formset':       formset,
            'learner_forms': learner_forms,
        })

    @enrollment_admin_required
    def post(self, request):
        config = request.session.get('promotion_batch_config')
        if not config:
            messages.error(request, 'Session expired. Please start again.')
            return redirect('enrollment:promotion_create_batch')

        from academics.models.school import AcademicYear

        from_year    = AcademicYear.objects.get(pk=config['from_year_pk'])
        to_year      = AcademicYear.objects.get(pk=config['to_year_pk'])
        from_grade   = Grade.objects.get(pk=config['from_grade_pk'])
        from_stream_pk = config.get('from_stream_pk')

        enrollments = LearnerEnrollment.objects.filter(
            academic_year=from_year,
            grade=from_grade,
            is_active=True,
        ).select_related('learner')

        if from_stream_pk:
            enrollments = enrollments.filter(stream_id=from_stream_pk)

        enrollments = enrollments.order_by('learner__last_name')

        PromotionFormSet = formset_factory(PromotionDecisionForm, extra=0)
        formset = PromotionFormSet(request.POST)

        if not formset.is_valid():
            learner_forms = zip(enrollments, formset)
            return render(request, self.template_name, {
                'config':        config,
                'from_year':     from_year,
                'from_grade':    from_grade,
                'formset':       formset,
                'learner_forms': learner_forms,
            })

        # Build promotion decisions
        promotion_decisions = []
        for enrollment, form in zip(enrollments, formset):
            promotion_decisions.append({
                'learner':   enrollment.learner,
                'status':    form.cleaned_data['status'],
                'to_grade':  form.cleaned_data.get('to_grade'),
                'to_stream': form.cleaned_data.get('to_stream'),
                'notes':     form.cleaned_data.get('notes', ''),
            })

        # Create the batch
        service = PromotionService()
        batch = service.create_bulk_batch(
            label=config['label'],
            from_academic_year=from_year,
            to_academic_year=to_year,
            promotion_decisions=promotion_decisions,
            created_by=request.user,
            notes=config.get('notes', ''),
        )

        # Clear session
        del request.session['promotion_batch_config']

        messages.success(
            request,
            f'Promotion batch created with {batch.total_count} learners.',
        )
        return redirect('enrollment:promotion_batch_detail', pk=batch.pk)


# ==================== BATCH DETAIL ====================

class BatchDetailView(View):
    """
    Review a promotion batch before execution.
    Shows all promotion decisions and an Execute button.
    """

    template_name = 'enrollment/promotion/batch_detail.html'

    @enrollment_admin_required
    def get(self, request, pk):
        batch = get_object_or_404(
            BulkPromotionBatch.objects.select_related(
                'from_academic_year',
                'to_academic_year',
                'created_by',
            ),
            pk=pk,
        )

        promotions = batch.promotions.all().select_related(
            'learner',
            'from_grade',
            'to_grade',
            'from_stream',
            'to_stream',
        ).order_by('learner__last_name')

        return render(request, self.template_name, {
            'batch':      batch,
            'promotions': promotions,
        })


# ==================== EXECUTE BATCH ====================

class ExecuteBatchView(View):
    """
    Execute a promotion batch.
    Creates all LearnerEnrollments for the new year.
    """

    template_name = 'enrollment/promotion/execute_batch.html'

    @enrollment_admin_required
    def get(self, request, pk):
        batch = get_object_or_404(BulkPromotionBatch, pk=pk)

        if batch.executed:
            messages.warning(request, 'This batch has already been executed.')
            return redirect('enrollment:promotion_batch_detail', pk=pk)

        return render(request, self.template_name, {'batch': batch})

    @enrollment_admin_required
    def post(self, request, pk):
        batch = get_object_or_404(BulkPromotionBatch, pk=pk)

        if batch.executed:
            messages.error(request, 'This batch has already been executed.')
            return redirect('enrollment:promotion_batch_detail', pk=pk)

        service = PromotionService()
        result = service.execute_batch(batch, request.user)

        messages.success(
            request,
            f'Batch executed: {result["success_count"]} success, '
            f'{result["failure_count"]} failed.',
        )
        return redirect('enrollment:promotion_batch_detail', pk=pk)