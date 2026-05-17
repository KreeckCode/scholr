"""
Enrollment admin views for staff.

All views require staff authentication and check enrollment_enabled().
These views provide the enrollment officer interface for reviewing
applications, managing documents, and making admission decisions.
"""
import logging

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from ..forms.admin import (
    AssignOfficerForm,
    DocumentReviewForm,
    InternalNotesForm,
    IssueOfferForm,
    RejectApplicationForm,
)
from ..models.application import (
    Application,
    ApplicationDocument,
    ApplicationDocumentRequirement,
    ApplicationTemplate,
)
from ..permissions import enrollment_admin_required
from ..services.review_service import ReviewService

logger = logging.getLogger(__name__)


# ==================== QUEUE ====================

class AdminQueueView(View):
    """
    Application review queue dashboard.
    Shows all applications grouped by status with filters and search.
    """

    template_name = 'enrollment/admin/queue.html'

    @enrollment_admin_required
    def get(self, request):
        # Filters
        status_filter = request.GET.get('status', '')
        grade_filter  = request.GET.get('grade', '')
        year_filter   = request.GET.get('year', '')
        priority_filter = request.GET.get('priority', '')
        search_query  = request.GET.get('q', '').strip()

        applications = Application.objects.select_related(
            'template',
            'target_grade',
            'assigned_to',
        ).order_by('-submitted_at')

        # Apply filters
        if status_filter:
            applications = applications.filter(status=status_filter)
        if grade_filter:
            applications = applications.filter(target_grade_id=grade_filter)
        if year_filter:
            applications = applications.filter(enrollment_year=year_filter)
        if priority_filter:
            applications = applications.filter(priority=priority_filter)
        if search_query:
            applications = applications.filter(
                Q(reference_number__icontains=search_query)
                | Q(first_name__icontains=search_query)
                | Q(last_name__icontains=search_query)
                | Q(id_number__icontains=search_query)
                | Q(guardian_email__icontains=search_query)
            )

        # Pagination
        paginator = Paginator(applications, 25)
        page_num  = request.GET.get('page', 1)
        page      = paginator.get_page(page_num)

        # Stats for dashboard cards
        stats = {
            'submitted': Application.objects.filter(status=Application.STATUS_SUBMITTED).count(),
            'docs_pending': Application.objects.filter(status=Application.STATUS_DOCUMENTS_PENDING).count(),
            'under_review': Application.objects.filter(status=Application.STATUS_UNDER_REVIEW).count(),
            'duplicates': Application.objects.filter(status=Application.STATUS_POSSIBLE_DUPLICATE).count(),
            'offers': Application.objects.filter(status=Application.STATUS_OFFER_ISSUED).count(),
            'enrolled': Application.objects.filter(status=Application.STATUS_ENROLLED).count(),
        }

        # Filter choices
        from academics.models.structure import Grade
        grades = Grade.objects.all().order_by('grade_number')
        years  = Application.objects.values_list('enrollment_year', flat=True).distinct().order_by('-enrollment_year')

        return render(request, self.template_name, {
            'page':           page,
            'stats':          stats,
            'status_filter':  status_filter,
            'grade_filter':   grade_filter,
            'year_filter':    year_filter,
            'priority_filter': priority_filter,
            'search_query':   search_query,
            'grades':         grades,
            'years':          years,
            'status_choices': Application.STATUS_CHOICES,
        })


# ==================== APPLICATION DETAIL ====================

class ApplicationDetailView(View):
    """
    Detailed view of a single application.
    Shows all application data, documents, status history, and action buttons.
    """

    template_name = 'enrollment/admin/application_detail.html'

    @enrollment_admin_required
    def get(self, request, pk):
        application = get_object_or_404(
            Application.objects.select_related(
                'template',
                'target_grade',
                'assigned_to',
                'suspected_duplicate_of',
                'learner',
            ),
            pk=pk,
        )

        # Documents
        doc_requirements = ApplicationDocumentRequirement.objects.filter(
            template=application.template
        ).order_by('-is_required', 'document_type')

        documents = ApplicationDocument.objects.filter(
            application=application
        ).order_by('document_type', '-version')

        # Group documents by type (latest version per type)
        docs_by_type = {}
        for doc in documents:
            if doc.document_type not in docs_by_type:
                docs_by_type[doc.document_type] = doc

        # Status history
        history = application.status_history.all().select_related('changed_by').order_by('-changed_at')

        # Forms
        assign_form = AssignOfficerForm(initial={'assigned_to': application.assigned_to})
        notes_form  = InternalNotesForm(initial={'internal_notes': application.internal_notes})

        return render(request, self.template_name, {
            'application':     application,
            'doc_requirements': doc_requirements,
            'docs_by_type':    docs_by_type,
            'all_documents':   documents,
            'history':         history,
            'assign_form':     assign_form,
            'notes_form':      notes_form,
        })

    @enrollment_admin_required
    def post(self, request, pk):
        """Handle assignment and notes updates."""
        application = get_object_or_404(Application, pk=pk)
        action = request.POST.get('action')

        if action == 'assign':
            form = AssignOfficerForm(request.POST)
            if form.is_valid():
                application.assigned_to = form.cleaned_data['assigned_to']
                application.save(update_fields=['assigned_to'])
                messages.success(request, 'Application assignment updated.')
        
        elif action == 'update_notes':
            form = InternalNotesForm(request.POST)
            if form.is_valid():
                application.internal_notes = form.cleaned_data['internal_notes']
                application.save(update_fields=['internal_notes'])
                messages.success(request, 'Internal notes updated.')

        return redirect('enrollment:admin_application_detail', pk=pk)


# ==================== DOCUMENT REVIEW ====================

class DocumentReviewView(View):
    """
    Approve or reject a specific document.
    Rejection fires an email to the guardian requesting re-upload.
    """

    @enrollment_admin_required
    def post(self, request, document_pk):
        document = get_object_or_404(
            ApplicationDocument.objects.select_related('application'),
            pk=document_pk,
        )
        form = DocumentReviewForm(request.POST)

        if not form.is_valid():
            messages.error(request, 'Invalid form submission.')
            return redirect('enrollment:admin_application_detail', pk=document.application.pk)

        service = ReviewService()
        action  = form.cleaned_data['action']

        if action == 'approve':
            service.approve_document(document, request.user)
            messages.success(
                request,
                f'{document.get_document_type_display()} approved.',
            )
        else:
            reason = form.cleaned_data['rejection_reason']
            service.reject_document(document, request.user, reason)
            messages.success(
                request,
                f'{document.get_document_type_display()} rejected. '
                f'Guardian has been notified to re-upload.',
            )

        return redirect('enrollment:admin_application_detail', pk=document.application.pk)


# ==================== DECISION ACTIONS ====================

class IssueOfferView(View):
    """Issue a conditional offer to an applicant."""

    template_name = 'enrollment/admin/issue_offer.html'

    @enrollment_admin_required
    def get(self, request, pk):
        application = get_object_or_404(Application, pk=pk)
        form = IssueOfferForm()
        return render(request, self.template_name, {
            'application': application,
            'form': form,
        })

    @enrollment_admin_required
    def post(self, request, pk):
        application = get_object_or_404(Application, pk=pk)
        form = IssueOfferForm(request.POST)

        if not form.is_valid():
            return render(request, self.template_name, {
                'application': application,
                'form': form,
            })

        service = ReviewService()
        service.issue_offer(
            application=application,
            issued_by=request.user,
            notes=form.cleaned_data.get('notes', ''),
        )

        messages.success(
            request,
            f'Conditional offer issued for {application.reference_number}. '
            f'Guardian has been notified by email.',
        )
        return redirect('enrollment:admin_application_detail', pk=pk)


class ApproveApplicationView(View):
    """Final approval — mark application as enrolled."""

    @enrollment_admin_required
    def post(self, request, pk):
        application = get_object_or_404(Application, pk=pk)
        service = ReviewService()
        service.approve_application(application, request.user)

        messages.success(
            request,
            f'{application.reference_number} approved and enrolled. '
            f'Welcome email sent to guardian.',
        )
        return redirect('enrollment:admin_application_detail', pk=pk)


class RejectApplicationView(View):
    """Reject an application with a reason."""

    template_name = 'enrollment/admin/reject_application.html'

    @enrollment_admin_required
    def get(self, request, pk):
        application = get_object_or_404(Application, pk=pk)
        form = RejectApplicationForm()
        return render(request, self.template_name, {
            'application': application,
            'form': form,
        })

    @enrollment_admin_required
    def post(self, request, pk):
        application = get_object_or_404(Application, pk=pk)
        form = RejectApplicationForm(request.POST)

        if not form.is_valid():
            return render(request, self.template_name, {
                'application': application,
                'form': form,
            })

        service = ReviewService()
        service.reject_application(
            application=application,
            rejected_by=request.user,
            reason=form.cleaned_data['rejection_reason'],
            internal_notes=form.cleaned_data.get('internal_notes', ''),
        )

        messages.success(
            request,
            f'{application.reference_number} rejected. '
            f'Guardian has been notified by email.',
        )
        return redirect('enrollment:admin_queue')


class ClearDuplicateView(View):
    """Clear the duplicate flag on an application."""

    @enrollment_admin_required
    def post(self, request, pk):
        application = get_object_or_404(Application, pk=pk)
        service = ReviewService()
        service.clear_duplicate(application, request.user)

        messages.success(
            request,
            f'Duplicate flag cleared for {application.reference_number}. '
            f'Application moved to under review.',
        )
        return redirect('enrollment:admin_application_detail', pk=pk)


# ==================== WAITLIST MANAGEMENT ====================

class WaitlistView(View):
    """Waitlist management dashboard."""

    template_name = 'enrollment/admin/waitlist.html'

    @enrollment_admin_required
    def get(self, request):
        template_filter = request.GET.get('template', '')

        waitlisted = Application.objects.filter(
            status=Application.STATUS_WAITLISTED,
        ).select_related('template', 'target_grade').order_by(
            'priority', 'waitlist_position'
        )

        if template_filter:
            waitlisted = waitlisted.filter(template_id=template_filter)

        templates = ApplicationTemplate.objects.filter(
            is_active=True
        ).order_by('-enrollment_year', 'entry_grade__grade_number')

        return render(request, self.template_name, {
            'waitlisted':      waitlisted,
            'templates':       templates,
            'template_filter': template_filter,
        })


class PromoteWaitlistView(View):
    """Promote a waitlisted applicant to offered status."""

    @enrollment_admin_required
    def post(self, request, pk):
        application = get_object_or_404(Application, pk=pk)

        if application.status != Application.STATUS_WAITLISTED:
            messages.error(request, 'This application is not on the waitlist.')
            return redirect('enrollment:admin_waitlist')

        service = ReviewService()
        service.promote_from_waitlist(application, request.user)

        messages.success(
            request,
            f'{application.reference_number} promoted from waitlist. '
            f'Offer email sent with 48-hour acceptance deadline.',
        )
        return redirect('enrollment:admin_waitlist')