"""
Parent re-enrollment portal views.

Magic link authentication — no password required.
Token in URL grants access to specific Reenrollment record.
"""
from datetime import timezone
import logging

from django.contrib import messages
from django.core.signing import BadSignature, SignatureExpired
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from ..forms.reenrollment import (
    FeeAgreementConsentForm,
    GuardianContactUpdateForm,
    LearnerDetailsConfirmationForm,
    SubjectSelectionForm,
)
from ..models.reenrollment import Reenrollment
from ..services.reenrollment_service import ReenrollmentService

logger = logging.getLogger(__name__)


# ==================== HELPER MIXIN ====================

class ReenrollmentAccessMixin:
    """
    Verify token and load reenrollment for all portal views.
    Sets self.reenrollment and self.service on the view instance.
    """

    def dispatch(self, request, *args, **kwargs):
        token = request.GET.get('token', '')
        
        if not token:
            return render(request, 'enrollment/reenrollment/error.html', {
                'error': 'Missing access token',
                'message': 'Please use the link sent to your email to access re-enrollment.',
            })

        service = ReenrollmentService()

        try:
            reenrollment = service.verify_access_token(token)
        except (BadSignature, SignatureExpired):
            return render(request, 'enrollment/reenrollment/error.html', {
                'error': 'Invalid or expired link',
                'message': 'This re-enrollment link has expired or is invalid. Please contact the school.',
            })

        # Check expiry
        if reenrollment.is_expired():
            return render(request, 'enrollment/reenrollment/error.html', {
                'error': 'Re-enrollment period expired',
                'message': f'The deadline was {reenrollment.expiry_date}. Please contact the school.',
            })

        # Store on view instance
        self.reenrollment = reenrollment
        self.service = service
        self.token = token

        return super().dispatch(request, *args, **kwargs)


# ==================== LANDING ====================

class ReenrollmentLandingView(ReenrollmentAccessMixin, View):
    """
    Landing page for re-enrollment.
    Shows learner info and progress through steps.
    """

    template_name = 'enrollment/reenrollment/landing.html'

    def get(self, request):
        status = self.service.check_completion_status(self.reenrollment)

        # Auto-redirect if already complete
        if status['all_complete']:
            return redirect(f'/enroll/reenroll/complete/?token={self.token}')

        # Auto-redirect to next incomplete step
        if status['next_step'] == 'confirm_details':
            return redirect(f'/enroll/reenroll/details/?token={self.token}')
        elif status['next_step'] == 'select_subjects':
            return redirect(f'/enroll/reenroll/subjects/?token={self.token}')
        elif status['next_step'] == 'sign_agreement':
            return redirect(f'/enroll/reenroll/agreement/?token={self.token}')
        elif status['next_step'] == 'payment':
            return redirect(f'/enroll/reenroll/payment/?token={self.token}')

        return render(request, self.template_name, {
            'reenrollment': self.reenrollment,
            'status': status,
            'token': self.token,
        })


# ==================== STEP 1: CONFIRM DETAILS ====================

class ConfirmDetailsView(ReenrollmentAccessMixin, View):
    """
    Step 1: Confirm and update learner details.
    Also allows guardian to update their own contact info.
    """

    template_name = 'enrollment/reenrollment/confirm_details.html'

    def get(self, request):
        learner = self.reenrollment.learner
        
        learner_form = LearnerDetailsConfirmationForm(instance=learner)

        # Get primary guardian for contact update form
        from academics.models.learner import LearnerGuardian

        guardian_rel = LearnerGuardian.objects.filter(
            learner=learner,
            is_primary_contact=True,  # CORRECT
        ).select_related('guardian').first()

        guardian_form = None
        if guardian_rel:
            guardian = guardian_rel.guardian
            guardian_form = GuardianContactUpdateForm(initial={
                'email': guardian.email,
                'cell_phone': guardian.phone_primary,  # Correct field name
                'work_phone': guardian.phone_secondary,  # Correct field name
                'physical_address': guardian.street_address,  # Correct field name
            })

        return render(request, self.template_name, {
            'reenrollment': self.reenrollment,
            'learner_form': learner_form,
            'guardian_form': guardian_form,
            'token': self.token,
        })

    def post(self, request):
        learner = self.reenrollment.learner
        
        learner_form = LearnerDetailsConfirmationForm(request.POST, instance=learner)
        guardian_form = GuardianContactUpdateForm(request.POST)

        if not learner_form.is_valid() or not guardian_form.is_valid():
            return render(request, self.template_name, {
                'reenrollment': self.reenrollment,
                'learner_form': learner_form,
                'guardian_form': guardian_form,
                'token': self.token,
            })

        # Save via service
        learner_data = {
            'home_language': learner_form.cleaned_data['home_language'],
            'allergies': learner_form.cleaned_data['allergies'],
            'chronic_conditions': learner_form.cleaned_data['chronic_conditions'],
            'current_medication': learner_form.cleaned_data['current_medication'],
        }

        guardian_data = guardian_form.cleaned_data

        self.service.confirm_details(
            self.reenrollment,
            learner_data=learner_data,
            guardian_data=guardian_data,
        )

        messages.success(request, 'Details confirmed successfully.')

        # Redirect to next step
        status = self.service.check_completion_status(self.reenrollment)
        if status['next_step'] == 'select_subjects':
            return redirect(f'/enroll/reenroll/subjects/?token={self.token}')
        elif status['next_step'] == 'sign_agreement':
            return redirect(f'/enroll/reenroll/agreement/?token={self.token}')
        else:
            return redirect(f'/enroll/reenroll/?token={self.token}')


# ==================== STEP 2: SELECT SUBJECTS ====================

class SubjectSelectionView(ReenrollmentAccessMixin, View):
    """
    Step 2: Select subjects for the new academic year.
    Only shown if subject_selection_required is True.
    """

    template_name = 'enrollment/reenrollment/select_subjects.html'

    def get(self, request):
        if not self.reenrollment.subject_selection_required:
            messages.info(request, 'Subject selection not required.')
            return redirect(f'/enroll/reenroll/agreement/?token={self.token}')

        form = SubjectSelectionForm(
            grade=self.reenrollment.to_grade,
            required_count=7,  # Configurable
        )

        return render(request, self.template_name, {
            'reenrollment': self.reenrollment,
            'form': form,
            'token': self.token,
        })

    def post(self, request):
        if not self.reenrollment.subject_selection_required:
            return redirect(f'/enroll/reenroll/agreement/?token={self.token}')

        form = SubjectSelectionForm(
            request.POST,
            grade=self.reenrollment.to_grade,
            academic_year=self.reenrollment.to_academic_year,
            required_count=7,
        )

        if not form.is_valid():
            return render(request, self.template_name, {
                'reenrollment': self.reenrollment,
                'form': form,
                'token': self.token,
            })

        # Save selected subjects
        self.service.save_subject_selection(
            self.reenrollment,
            selected_offerings=form.selected_offerings,
        )

        messages.success(request, 'Subject selection saved successfully.')
        return redirect(f'/enroll/reenroll/agreement/?token={self.token}')


# ==================== STEP 3: FEE AGREEMENT ====================

class FeeAgreementView(ReenrollmentAccessMixin, View):
    """
    Step 3: Review and sign fee agreement.
    Records ECTA-compliant digital consent.
    """

    template_name = 'enrollment/reenrollment/fee_agreement.html'

    def get(self, request):
        form = FeeAgreementConsentForm()

        return render(request, self.template_name, {
            'reenrollment': self.reenrollment,
            'form': form,
            'token': self.token,
        })

    def post(self, request):
        form = FeeAgreementConsentForm(request.POST)

        if not form.is_valid():
            return render(request, self.template_name, {
                'reenrollment': self.reenrollment,
                'form': form,
                'token': self.token,
            })

        # Record consent
        self.service.sign_fee_agreement(
            self.reenrollment,
            signature_name=form.cleaned_data['signature_name'],
            ip_address=self._get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )

        messages.success(request, 'Fee agreement signed successfully.')

        # Check if payment required
        if self.reenrollment.deposit_required:
            return redirect(f'/enroll/reenroll/payment/?token={self.token}')
        else:
            # Complete immediately
            return redirect(f'/enroll/reenroll/complete/?token={self.token}')

    def _get_client_ip(self, request):
        """Extract client IP from request."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '')


# ==================== STEP 4: PAYMENT (PLACEHOLDER) ====================

class PaymentView(ReenrollmentAccessMixin, View):
    """
    Step 4: Pay enrollment deposit.
    Integration with Paystack (Stage 6).
    For now, just a placeholder that marks payment as complete.
    """

    template_name = 'enrollment/reenrollment/payment.html'

    def get(self, request):
        if not self.reenrollment.deposit_required:
            return redirect(f'/enroll/reenroll/complete/?token={self.token}')

        return render(request, self.template_name, {
            'reenrollment': self.reenrollment,
            'token': self.token,
        })

    def post(self, request):
        # TODO: Stage 6 — integrate Paystack
        # For now, just mark as complete
        self.reenrollment.payment_complete = True
        self.reenrollment.payment_date = timezone.now()
        self.reenrollment.fee_order_id = 1  # Placeholder
        self.reenrollment.save(update_fields=['fee_order_id', 'updated_at'])

        messages.success(request, 'Payment received.')
        return redirect(f'/enroll/reenroll/complete/?token={self.token}')


# ==================== COMPLETION ====================

class ReenrollmentCompleteView(ReenrollmentAccessMixin, View):
    """
    Confirmation page shown when re-enrollment is complete.
    """

    template_name = 'enrollment/reenrollment/complete.html'

    def get(self, request):
        status = self.service.check_completion_status(self.reenrollment)

        if not status['all_complete']:
            messages.warning(request, 'Please complete all required steps.')
            return redirect(f'/enroll/reenroll/?token={self.token}')

        return render(request, self.template_name, {
            'reenrollment': self.reenrollment,
            'token': self.token,
        })