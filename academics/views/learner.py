import os

from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    UpdateView,
)

from ..forms.learner import (
    GuardianForm,
    LearnerEnrollmentForm,
    LearnerForm,
    LearnerGuardianForm,
)
from ..mixins import ManagementMixin, SeniorManagementMixin, StaffMixin
from ..models.learner import (
    Guardian,
    Learner,
    LearnerDocument,
    LearnerEnrollment,
    LearnerGuardian,
)
from ..models.school import AcademicYear
from ..models.structure import GradeStream, Grade


class LearnerListView(StaffMixin, ListView):
    """
    Paginated, searchable learner list.
    Scoping rules:
      - Admin / Principal / Admin Clerk: all active learners
      - HOD / Teacher: only learners in their homeroom stream(s)
      - Parent / Learner: blocked at mixin level (not in StaffMixin allowed_roles)
    """

    model = Learner
    template_name = "academics/learner/list.html"
    context_object_name = "learners"
    paginate_by = 30

    def get_queryset(self):
        user = self.request.user
        qs = (
            Learner.objects.filter(is_active=True)
            .select_related("user")
            .prefetch_related("enrollments__grade", "enrollments__stream")
            .order_by("last_name", "first_name")
        )

        # Teachers and HODs see only learners in their homeroom streams
        if user.role in {"teacher", "hod"}:
            current_year = AcademicYear.get_current()
            if not current_year:
                return Learner.objects.none()
            streams = GradeStream.objects.filter(
                homeroom_teacher=user,
                academic_year=current_year,
                is_active=True,
            )
            qs = qs.filter(
                enrollments__stream__in=streams,
                enrollments__academic_year=current_year,
                enrollments__is_active=True,
            ).distinct()

        # Search across name, admission number, SA ID
        query = self.request.GET.get("q", "").strip()
        if query:
            qs = qs.filter(
                Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(admission_number__icontains=query)
                | Q(sa_id_number__icontains=query)
            )

        # Grade filter
        grade_id = self.request.GET.get("grade", "")
        if grade_id:
            current_year = AcademicYear.get_current()
            qs = qs.filter(
                enrollments__grade_id=grade_id,
                enrollments__academic_year=current_year,
                enrollments__is_active=True,
            ).distinct()

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["grades"] = Grade.objects.filter(is_active=True).order_by("grade_number")
        ctx["search_query"] = self.request.GET.get("q", "")
        ctx["selected_grade"] = self.request.GET.get("grade", "")
        ctx["current_year"] = AcademicYear.get_current()
        ctx["can_add"] = self.request.user.role in {"admin", "principal", "admin_clerk"}
        return ctx


class LearnerDetailView(StaffMixin, DetailView):
    """
    Full learner profile.
    Access is re-checked beyond the role mixin - teachers can only see
    learners in their stream; parents only their own child.
    """

    model = Learner
    template_name = "academics/learner/detail.html"
    context_object_name = "learner"

    def get_object(self, queryset=None):
        learner = get_object_or_404(
            Learner.objects.select_related("user", "created_by").prefetch_related(
                "guardian_relationships__guardian",
                "enrollments__grade",
                "enrollments__stream__homeroom_teacher",
                "documents",
            ),
            pk=self.kwargs["pk"],
        )
        self._assert_can_view(learner)
        return learner

    def _assert_can_view(self, learner):
        user = self.request.user

        # Management roles: full access
        if user.role in {"admin", "principal", "admin_clerk"}:
            return

        if user.role in {"teacher", "hod"}:
            # Must be the homeroom teacher for this learner's current stream
            current_year = AcademicYear.get_current()
            if not current_year:
                raise PermissionDenied
            teaches = GradeStream.objects.filter(
                homeroom_teacher=user,
                academic_year=current_year,
                enrollments__learner=learner,
                enrollments__is_active=True,
            ).exists()
            if not teaches:
                raise PermissionDenied

        elif user.role == "parent":
            if not LearnerGuardian.objects.filter(
                guardian__user=user, learner=learner
            ).exists():
                raise PermissionDenied

        elif user.role == "learner":
            if not (hasattr(learner, "user") and learner.user == user):
                raise PermissionDenied

        else:
            raise PermissionDenied

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        ctx["can_edit"] = user.role in {"admin", "principal", "admin_clerk"}
        ctx["can_deactivate"] = user.role in {"admin", "principal"}
        ctx["can_view_medical"] = user.role in {"admin", "principal", "admin_clerk", "teacher", "hod"}
        ctx["can_upload_docs"] = user.role in {"admin", "principal", "admin_clerk"}
        ctx["current_enrollment"] = self.object.get_current_enrollment()
        ctx["enrollment_history"] = self.object.enrollments.order_by(
            "-academic_year__year"
        ).select_related("grade", "stream", "academic_year")
        return ctx


class LearnerCreateView(ManagementMixin, SuccessMessageMixin, CreateView):
    """
    Register a new learner. Admission number is auto-generated.
    SA ID validation runs in the form - DOB and gender are auto-populated.
    """

    model = Learner
    form_class = LearnerForm
    template_name = "academics/learner/form.html"
    success_message = "%(first_name)s %(last_name)s has been registered."

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user_role"] = self.request.user.role
        return kwargs

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        if not form.instance.admission_number:
            form.instance.admission_number = self._next_admission_number()
        return super().form_valid(form)

    def _next_admission_number(self):
        """Generate a sequential yearly admission number e.g. 20260042."""
        year = timezone.now().year
        last = (
            Learner.objects.filter(admission_number__startswith=str(year))
            .order_by("-admission_number")
            .first()
        )
        if last:
            try:
                seq = int(last.admission_number[4:]) + 1
            except (ValueError, IndexError):
                seq = 1
        else:
            seq = 1
        return f"{year}{seq:04d}"

    def get_success_url(self):
        return reverse_lazy("academics:learner_detail", kwargs={"pk": self.object.pk})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = "Register New Learner"
        ctx["submit_label"] = "Register Learner"
        return ctx


class LearnerUpdateView(ManagementMixin, SuccessMessageMixin, UpdateView):
    """
    Edit an existing learner record.
    Admin Clerks can edit demographics and medical but not
    admission number or portal account (enforced in the form).
    """

    model = Learner
    form_class = LearnerForm
    template_name = "academics/learner/form.html"
    success_message = "Learner record updated."

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user_role"] = self.request.user.role
        return kwargs

    def get_success_url(self):
        return reverse_lazy("academics:learner_detail", kwargs={"pk": self.object.pk})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = f"Edit: {self.object.get_full_name()}"
        ctx["submit_label"] = "Save Changes"
        return ctx


class LearnerDeactivateView(SeniorManagementMixin, View):
    """
    Soft-delete a learner record - marks is_active=False.
    Restricted to Principal and Admin. Admin Clerks cannot do this.
    Records are NEVER hard-deleted - they're required for historical reporting.
    """

    def post(self, request, pk):
        learner = get_object_or_404(Learner, pk=pk)

        if not learner.is_active:
            messages.info(request, f"{learner.get_full_name()} is already inactive.")
            return redirect("academics:learner_list")

        learner.is_active = False
        learner.save(update_fields=["is_active", "updated_at"])

        # Deactivate the current year enrollment as well
        LearnerEnrollment.objects.filter(
            learner=learner, is_active=True
        ).update(is_active=False)

        messages.success(
            request,
            f"{learner.get_full_name()} has been deactivated. "
            f"Their record is retained for historical reporting.",
        )
        return redirect("academics:learner_list")


class LearnerEnrollView(ManagementMixin, SuccessMessageMixin, CreateView):
    """Enroll a learner in a grade for a given academic year."""

    model = LearnerEnrollment
    form_class = LearnerEnrollmentForm
    template_name = "academics/learner/enroll.html"
    success_message = "Learner enrolled in %(grade)s."

    def _get_learner(self):
        return get_object_or_404(Learner, pk=self.kwargs["pk"], is_active=True)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["learner"] = self._get_learner()
        return kwargs

    def form_valid(self, form):
        form.instance.learner = self._get_learner()
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("academics:learner_detail", kwargs={"pk": self.kwargs["pk"]})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["learner"] = self._get_learner()
        return ctx


class DocumentServeView(StaffMixin, View):
    """
    Serve a learner document through Django - never directly from disk.
    This ensures role checks run before any file bytes are sent.
    Parents can view their own child's documents.
    Learners can view their own documents.
    """

    def get(self, request, pk, doc_pk):
        learner = get_object_or_404(Learner, pk=pk)
        document = get_object_or_404(LearnerDocument, pk=doc_pk, learner=learner)

        # Re-check beyond the StaffMixin for parent and learner roles
        if request.user.role == "parent":
            if not LearnerGuardian.objects.filter(
                guardian__user=request.user, learner=learner
            ).exists():
                raise PermissionDenied

        elif request.user.role == "learner":
            if not (hasattr(learner, "user") and learner.user == request.user):
                raise PermissionDenied

        try:
            return FileResponse(
                document.file.open("rb"),
                as_attachment=False,
                filename=os.path.basename(document.file.name),
            )
        except (OSError, FileNotFoundError):
            raise Http404("Document file not found on disk.")


class DocumentUploadView(ManagementMixin, SuccessMessageMixin, CreateView):
    """Upload a sensitive document against a learner record."""

    model = LearnerDocument
    fields = ["document_type", "name", "file", "notes"]
    template_name = "academics/learner/document_upload.html"
    success_message = "Document uploaded."

    def _get_learner(self):
        return get_object_or_404(Learner, pk=self.kwargs["pk"], is_active=True)

    def form_valid(self, form):
        form.instance.learner = self._get_learner()
        form.instance.uploaded_by = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("academics:learner_detail", kwargs={"pk": self.kwargs["pk"]})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["learner"] = self._get_learner()
        return ctx