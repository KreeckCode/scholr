"""
Public-facing enrollment views.

All views check enrollment_enabled() first.
None of these views require authentication — applications are submitted
by members of the public (parents/guardians).
"""
import logging

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from scholr.features import enrollment_enabled

from ..forms.application import ApplicationForm, DocumentUploadForm, StatusCheckForm
from ..models.application import (
    Application,
    ApplicationDocument,
    ApplicationDocumentRequirement,
    ApplicationTemplate,
)
from ..services.application_service import ApplicationService

logger = logging.getLogger(__name__)


def _get_client_ip(request):
    """Extract the real IP address, respecting X-Forwarded-For from Nginx."""
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


# ==================== LANDING PAGE ====================

class EnrollmentLandingView(View):
    """
    Public landing page listing all open ApplicationTemplates.
    When no templates are active, shows a closed message.
    This is the entry point at /enroll/.
    """

    template_name = 'enrollment/landing.html'

    def get(self, request):
        if not enrollment_enabled():
            raise Http404

        from ..models.application import EnrollmentConfig
        config = EnrollmentConfig.get()

        # Only show active templates — let is_open handle window gating
        templates = ApplicationTemplate.objects.filter(
            is_active=True,
        ).select_related('entry_grade').order_by(
            'enrollment_year', 'entry_grade__grade_number'
        )

        open_templates    = [t for t in templates if t.is_open]
        closed_templates  = [t for t in templates if not t.is_open]

        return render(request, self.template_name, {
            'open_templates':   open_templates,
            'closed_templates': closed_templates,
            'config':           config,
            'any_open':         bool(open_templates),
        })


# ==================== APPLICATION FORM ====================

class ApplyView(View):
    """
    Multi-section application form for a specific ApplicationTemplate.

    GET  — renders the form with sections shown/hidden via Alpine.js
    POST — validates, creates the Application, redirects to document upload
    """

    template_name = 'enrollment/apply.html'

    def _get_template(self, template_pk):
        if not enrollment_enabled():
            raise Http404
        tmpl = get_object_or_404(ApplicationTemplate, pk=template_pk, is_active=True)
        return tmpl

    def get(self, request, template_pk):
        tmpl = self._get_template(template_pk)

        if not tmpl.is_open:
            return render(request, self.template_name, {
                'template': tmpl,
                'closed': True,
            })

        form = ApplicationForm()

        # Make essay required if the template needs it
        if tmpl.show_essay_section and tmpl.essay_question:
            form.fields['essay_answer'].required = True
            form.fields['essay_answer'].label = tmpl.essay_question

        # Get document requirements for the progress checklist
        doc_requirements = tmpl.document_requirements.all().order_by(
            '-is_required', 'document_type'
        )

        return render(request, self.template_name, {
            'form':             form,
            'template':         tmpl,
            'doc_requirements': doc_requirements,
            'show_sections': {
                'previous_school': tmpl.show_previous_school_section,
                'medical':         tmpl.show_medical_section,
                'essay':           tmpl.show_essay_section,
            },
        })

    def post(self, request, template_pk):
        tmpl = self._get_template(template_pk)

        if not tmpl.is_open:
            raise Http404('This enrollment window is closed.')

        form = ApplicationForm(request.POST)

        if tmpl.show_essay_section and tmpl.essay_question:
            form.fields['essay_answer'].required = True
            form.fields['essay_answer'].label = tmpl.essay_question

        # Remove validation for hidden sections
        if not tmpl.show_previous_school_section:
            for field in ['previous_school_name', 'previous_school_type',
                          'previous_grade_completed', 'reason_for_leaving']:
                form.fields[field].required = False

        if not tmpl.show_medical_section:
            for field in ['medical_aid_provider', 'medical_aid_number']:
                form.fields[field].required = False

        doc_requirements = tmpl.document_requirements.all().order_by(
            '-is_required', 'document_type'
        )

        if not form.is_valid():
            return render(request, self.template_name, {
                'form':             form,
                'template':         tmpl,
                'doc_requirements': doc_requirements,
                'show_sections': {
                    'previous_school': tmpl.show_previous_school_section,
                    'medical':         tmpl.show_medical_section,
                    'essay':           tmpl.show_essay_section,
                },
                'has_errors': True,
            })

        service = ApplicationService()
        try:
            application = service.create_application(
                template=tmpl,
                form_data=form.cleaned_data,
                ip_address=_get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return render(request, self.template_name, {
                'form':             form,
                'template':         tmpl,
                'doc_requirements': doc_requirements,
                'show_sections': {
                    'previous_school': tmpl.show_previous_school_section,
                    'medical':         tmpl.show_medical_section,
                    'essay':           tmpl.show_essay_section,
                },
            })
        except Exception as exc:
            logger.error(f'Application creation failed: {exc}')
            messages.error(
                request,
                'An unexpected error occurred. Please try again or contact the school directly.',
            )
            return render(request, self.template_name, {
                'form':             form,
                'template':         tmpl,
                'doc_requirements': doc_requirements,
                'show_sections': {
                    'previous_school': tmpl.show_previous_school_section,
                    'medical':         tmpl.show_medical_section,
                    'essay':           tmpl.show_essay_section,
                },
            })

        return redirect('enrollment:success', reference=application.reference_number)


# ==================== SUCCESS PAGE ====================

class ApplicationSuccessView(View):
    """
    Shown immediately after a successful application submission.
    Displays reference number, next steps, and link to document upload.
    """

    template_name = 'enrollment/success.html'

    def get(self, request, reference):
        if not enrollment_enabled():
            raise Http404

        application = get_object_or_404(Application, reference_number=reference)

        doc_requirements = ApplicationDocumentRequirement.objects.filter(
            template=application.template
        ).order_by('-is_required', 'document_type')

        uploaded_types = set(
            ApplicationDocument.objects.filter(
                application=application,
            ).values_list('document_type', flat=True)
        )

        return render(request, self.template_name, {
            'application':     application,
            'doc_requirements': doc_requirements,
            'uploaded_types':  uploaded_types,
            'is_waitlisted':   application.status == Application.STATUS_WAITLISTED,
            'is_duplicate':    application.status == Application.STATUS_POSSIBLE_DUPLICATE,
        })


# ==================== DOCUMENT UPLOAD ====================

class DocumentUploadView(View):
    """
    Allows the guardian to upload required documents after submission.
    Accessible via the reference number — no login required.
    Documents are stored in a private path and served only through
    authenticated admin views.
    """

    template_name = 'enrollment/documents.html'

    def _get_application(self, reference):
        if not enrollment_enabled():
            raise Http404
        return get_object_or_404(Application, reference_number=reference)

    def _can_upload(self, application) -> bool:
        """Documents can only be uploaded while the application is in progress."""
        return application.status in (
            Application.STATUS_SUBMITTED,
            Application.STATUS_DOCUMENTS_PENDING,
            Application.STATUS_UNDER_REVIEW,
            Application.STATUS_DOCS_REQUESTED,
            Application.STATUS_POSSIBLE_DUPLICATE,
        )

    def get(self, request, reference):
        application = self._get_application(reference)

        doc_requirements = ApplicationDocumentRequirement.objects.filter(
            template=application.template
        ).order_by('-is_required', 'document_type')

        # Current uploads grouped by document type for status display
        uploads_by_type = {}
        for doc in ApplicationDocument.objects.filter(
            application=application
        ).order_by('-version'):
            if doc.document_type not in uploads_by_type:
                uploads_by_type[doc.document_type] = doc

        return render(request, self.template_name, {
            'application':     application,
            'doc_requirements': doc_requirements,
            'uploads_by_type': uploads_by_type,
            'can_upload':      self._can_upload(application),
            'form':            DocumentUploadForm(),
        })

    def post(self, request, reference):
        application = self._get_application(reference)

        if not self._can_upload(application):
            messages.error(
                request,
                'Documents cannot be uploaded at this stage of your application.',
            )
            return redirect('enrollment:documents', reference=reference)

        form = DocumentUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, 'Please correct the errors below.')

            doc_requirements = ApplicationDocumentRequirement.objects.filter(
                template=application.template
            ).order_by('-is_required', 'document_type')
            uploads_by_type = {}
            for doc in ApplicationDocument.objects.filter(application=application).order_by('-version'):
                if doc.document_type not in uploads_by_type:
                    uploads_by_type[doc.document_type] = doc

            return render(request, self.template_name, {
                'application':     application,
                'doc_requirements': doc_requirements,
                'uploads_by_type': uploads_by_type,
                'can_upload':      True,
                'form':            form,
            })

        document_type = form.cleaned_data['document_type']
        uploaded_file = form.cleaned_data['upload']

        # Supersede any previous version of this document type
        ApplicationDocument.objects.filter(
            application=application,
            document_type=document_type,
            status__in=[
                ApplicationDocument.STATUS_PENDING,
                ApplicationDocument.STATUS_REJECTED,
            ],
        ).update(status=ApplicationDocument.STATUS_SUPERSEDED)

        # Calculate next version number
        last_version = (
            ApplicationDocument.objects
            .filter(application=application, document_type=document_type)
            .order_by('-version')
            .values_list('version', flat=True)
            .first()
        ) or 0

        ApplicationDocument.objects.create(
            application=application,
            document_type=document_type,
            upload=uploaded_file,
            original_filename=uploaded_file.name,
            file_size_bytes=uploaded_file.size,
            version=last_version + 1,
            status=ApplicationDocument.STATUS_PENDING,
        )

        # If application was docs_pending or docs_requested,
        # move to under_review once at least one required doc is uploaded
        self._maybe_advance_status(application)

        messages.success(
            request,
            f'"{form.fields["document_type"].choices}" uploaded successfully. '
            f'Our admissions team will review it shortly.',
        )
        return redirect('enrollment:documents', reference=reference)

    def _maybe_advance_status(self, application):
        """
        Move application to STATUS_UNDER_REVIEW if all required documents
        have been uploaded (at least one approved or pending version each).
        """
        required_types = set(
            ApplicationDocumentRequirement.objects.filter(
                template=application.template,
                is_required=True,
            ).values_list('document_type', flat=True)
        )

        uploaded_types = set(
            ApplicationDocument.objects.filter(
                application=application,
                status__in=[
                    ApplicationDocument.STATUS_PENDING,
                    ApplicationDocument.STATUS_APPROVED,
                ],
            ).values_list('document_type', flat=True)
        )

        if required_types and required_types.issubset(uploaded_types):
            if application.status in (
                Application.STATUS_DOCUMENTS_PENDING,
                Application.STATUS_DOCS_REQUESTED,
            ):
                application.transition_status(
                    Application.STATUS_UNDER_REVIEW,
                    notes='All required documents uploaded. Ready for admin review.',
                )
                application.save(update_fields=['status'])


# ==================== PUBLIC STATUS CHECK ====================

class ApplicationStatusView(View):
    """
    Public status check page. No login required.
    Guardian enters reference number + email to see current status and timeline.
    Dramatically reduces inbound phone calls to the school office.
    """

    template_name = 'enrollment/status.html'

    def get(self, request):
        if not enrollment_enabled():
            raise Http404
        return render(request, self.template_name, {'form': StatusCheckForm()})

    def post(self, request):
        if not enrollment_enabled():
            raise Http404

        form = StatusCheckForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form})

        ref   = form.cleaned_data['reference_number'].strip().upper()
        email = form.cleaned_data['guardian_email'].strip().lower()

        application = Application.objects.filter(
            reference_number=ref,
            guardian_email__iexact=email,
        ).first()

        if not application:
            form.add_error(
                None,
                'No application found with that reference number and email address. '
                'Please check and try again.',
            )
            return render(request, self.template_name, {'form': form})

        status_history = application.status_history.all().order_by('changed_at')

        doc_requirements = ApplicationDocumentRequirement.objects.filter(
            template=application.template
        ).order_by('-is_required', 'document_type')

        uploads_by_type = {}
        for doc in ApplicationDocument.objects.filter(application=application).order_by('-version'):
            if doc.document_type not in uploads_by_type:
                uploads_by_type[doc.document_type] = doc

        return render(request, self.template_name, {
            'form':            StatusCheckForm(),
            'application':     application,
            'status_history':  status_history,
            'doc_requirements': doc_requirements,
            'uploads_by_type': uploads_by_type,
            'can_upload': application.status in (
                Application.STATUS_DOCUMENTS_PENDING,
                Application.STATUS_DOCS_REQUESTED,
                Application.STATUS_UNDER_REVIEW,
            ),
        })