from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from ..forms.curriculum import (
    AssessmentTaskForm,
    SchoolSubjectForm,
    SubjectOfferingForm,
    SubjectOfferingUpdateForm,
)
from ..mixins import HODOrSeniorMixin, SeniorManagementMixin, StaffMixin
from ..models.curriculum import AssessmentTask, Curriculum, SchoolSubject, SubjectOffering
from ..models.structure import Grade


class SubjectListView(StaffMixin, ListView):
    """
    All school subjects, filterable by type and curriculum.
    Read access for all staff. Management and HOD can see edit actions.
    """

    model = SchoolSubject
    template_name = "academics/curriculum/subject_list.html"
    context_object_name = "subjects"

    def get_queryset(self):
        qs = (
            SchoolSubject.objects.filter(is_active=True)
            .select_related("curriculum", "department")
            .order_by("sort_order", "name")
        )

        subject_type = self.request.GET.get("type", "")
        if subject_type:
            qs = qs.filter(subject_type=subject_type)

        curriculum_id = self.request.GET.get("curriculum", "")
        if curriculum_id:
            qs = qs.filter(curriculum_id=curriculum_id)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["type_choices"] = SchoolSubject.TYPE_CHOICES
        ctx["curricula"] = Curriculum.objects.filter(is_active=True)
        ctx["selected_type"] = self.request.GET.get("type", "")
        ctx["selected_curriculum"] = self.request.GET.get("curriculum", "")
        ctx["can_manage"] = self.request.user.role in {"admin", "principal"}
        return ctx


class SubjectCreateView(SeniorManagementMixin, SuccessMessageMixin, CreateView):
    """Create a new school subject. Admin and Principal only."""

    model = SchoolSubject
    form_class = SchoolSubjectForm
    template_name = "academics/curriculum/subject_form.html"
    success_url = reverse_lazy("academics:subject_list")
    success_message = "Subject '%(name)s' created."

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = "Add Subject"
        ctx["submit_label"] = "Create Subject"
        return ctx


class SubjectUpdateView(SeniorManagementMixin, SuccessMessageMixin, UpdateView):
    """Edit a school subject."""

    model = SchoolSubject
    form_class = SchoolSubjectForm
    template_name = "academics/curriculum/subject_form.html"
    success_url = reverse_lazy("academics:subject_list")
    success_message = "Subject updated."

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = f"Edit: {self.object.name}"
        ctx["submit_label"] = "Save Changes"
        return ctx


class OfferingMatrixView(StaffMixin, ListView):
    """
    2D matrix of active grades (rows) vs active subjects (columns).
    A filled cell means that subject is offered at that grade.
    Gives the school admin a complete picture of the curriculum structure.
    The matrix is built in Python because Django templates cannot
    do dictionary lookups with variable keys.
    """

    model = Grade
    template_name = "academics/curriculum/offering_matrix.html"

    def get_queryset(self):
        return Grade.objects.filter(is_active=True).order_by("grade_number")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        grades = self.get_queryset()
        subjects = SchoolSubject.objects.filter(is_active=True).order_by("sort_order", "name")

        # Build a (grade_id, subject_id) → offering lookup in one query
        offerings = SubjectOffering.objects.filter(is_active=True).select_related(
            "subject", "grade"
        )
        offering_map = {(o.grade_id, o.subject_id): o for o in offerings}

        # Build the matrix as a list of row dicts
        matrix = []
        for grade in grades:
            row = {"grade": grade, "cells": []}
            for subject in subjects:
                offering = offering_map.get((grade.pk, subject.pk))
                row["cells"].append({"subject": subject, "offering": offering})
            matrix.append(row)

        ctx["matrix"] = matrix
        ctx["subjects"] = subjects
        ctx["can_manage"] = self.request.user.role in {"admin", "principal"}
        return ctx


class GradeSubjectsView(SeniorManagementMixin, ListView):
    """
    Manage which subjects are offered at a specific grade.
    Shows current offerings with their weighting, plus the option to add more.
    """

    model = SubjectOffering
    template_name = "academics/curriculum/grade_subjects.html"
    context_object_name = "offerings"

    def _get_grade(self):
        return get_object_or_404(Grade, pk=self.kwargs["pk"], is_active=True)

    def get_queryset(self):
        return (
            SubjectOffering.objects.filter(grade=self._get_grade(), is_active=True)
            .select_related("subject", "subject__department")
            .order_by("subject__sort_order", "subject__name")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        grade = self._get_grade()
        ctx["grade"] = grade
        ctx["task_counts"] = {
            o.pk: o.tasks.filter(is_active=True).count()
            for o in ctx["offerings"]
        }
        return ctx


class SubjectOfferingCreateView(SeniorManagementMixin, SuccessMessageMixin, CreateView):
    """Add a subject to a grade."""

    model = SubjectOffering
    form_class = SubjectOfferingForm
    template_name = "academics/curriculum/offering_form.html"
    
    def get_success_message(self, cleaned_data):
        return f"{cleaned_data.get('subject')} added to {self._get_grade().name}."

    def _get_grade(self):
        return get_object_or_404(Grade, pk=self.kwargs["grade_pk"], is_active=True)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["grade"] = self._get_grade()
        return kwargs

    def form_valid(self, form):
        form.instance.grade = self._get_grade()
        # I'm pre-populating weighting from the subject's defaults if not provided
        if not form.instance.sba_weighting:
            form.instance.sba_weighting = form.instance.subject.default_sba_weighting
            form.instance.exam_weighting = form.instance.subject.default_exam_weighting
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("academics:grade_subjects", kwargs={"pk": self.kwargs["grade_pk"]})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["grade"] = self._get_grade()
        ctx["title"] = f"Add Subject to {self._get_grade().name}"
        ctx["submit_label"] = "Add Subject"
        return ctx


class SubjectOfferingUpdateView(SeniorManagementMixin, SuccessMessageMixin, UpdateView):
    """Edit the weighting or pass mark for an existing subject offering."""

    model = SubjectOffering
    form_class = SubjectOfferingUpdateForm
    template_name = "academics/curriculum/offering_form.html"
    success_message = "Offering updated."

    def get_success_url(self):
        return reverse_lazy(
            "academics:grade_subjects",
            kwargs={"pk": self.object.grade_id},
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["grade"] = self.object.grade
        ctx["title"] = f"Edit: {self.object.subject.name} - {self.object.grade.name}"
        ctx["submit_label"] = "Save Changes"
        return ctx


class AssessmentTaskListView(HODOrSeniorMixin, ListView):
    """
    Assessment tasks for a subject offering.
    HODs can only see tasks for subjects in their department.
    """

    model = AssessmentTask
    template_name = "academics/curriculum/task_list.html"
    context_object_name = "tasks"

    def _get_offering(self):
        return get_object_or_404(
            SubjectOffering.objects.select_related("subject", "grade"),
            pk=self.kwargs["pk"],
        )

    def _assert_hod_access(self, offering):
        user = self.request.user
        if user.role in {"admin", "principal"}:
            return
        if user.role == "hod":
            staff = getattr(user, "staff_profile", None)
            if not staff or offering.subject.department_id != staff.department_id:
                raise PermissionDenied(
                    "HODs can only manage assessment tasks for subjects in their department."
                )

    def get_queryset(self):
        offering = self._get_offering()
        self._assert_hod_access(offering)
        return offering.tasks.filter(is_active=True).order_by("term_number", "sequence")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        offering = self._get_offering()
        ctx["offering"] = offering

        # Group tasks by term for the template
        tasks_by_term = {1: [], 2: [], 3: [], 4: []}
        for task in ctx["tasks"]:
            tasks_by_term[task.term_number].append(task)
        ctx["tasks_by_term"] = tasks_by_term

        ctx["total_sba_marks"] = offering.total_sba_marks()
        ctx["total_exam_marks"] = offering.total_exam_marks()
        ctx["can_manage"] = self.request.user.role in {"admin", "principal", "hod"}
        return ctx


class AssessmentTaskCreateView(HODOrSeniorMixin, SuccessMessageMixin, CreateView):
    """
    Add an assessment task to a subject offering.
    HODs are restricted to subjects in their department.
    """

    model = AssessmentTask
    form_class = AssessmentTaskForm
    template_name = "academics/curriculum/task_form.html"
    success_message = "Task '%(name)s' added."

    def _get_offering(self):
        return get_object_or_404(
            SubjectOffering.objects.select_related("subject", "grade"),
            pk=self.kwargs["offering_pk"],
        )

    def _assert_hod_access(self, offering):
        user = self.request.user
        if user.role in {"admin", "principal"}:
            return
        if user.role == "hod":
            staff = getattr(user, "staff_profile", None)
            if not staff or offering.subject.department_id != staff.department_id:
                raise PermissionDenied

    def dispatch(self, request, *args, **kwargs):
        self._assert_hod_access(self._get_offering())
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.subject_offering = self._get_offering()
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy(
            "academics:task_list",
            kwargs={"pk": self.kwargs["offering_pk"]},
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["offering"] = self._get_offering()
        ctx["title"] = f"Add Task: {self._get_offering()}"
        ctx["submit_label"] = "Add Task"
        return ctx


class AssessmentTaskUpdateView(HODOrSeniorMixin, SuccessMessageMixin, UpdateView):
    """Edit an assessment task. HODs restricted to their department."""

    model = AssessmentTask
    form_class = AssessmentTaskForm
    template_name = "academics/curriculum/task_form.html"
    success_message = "Task updated."

    def _assert_hod_access(self):
        user = self.request.user
        if user.role in {"admin", "principal"}:
            return
        if user.role == "hod":
            staff = getattr(user, "staff_profile", None)
            dept_id = self.get_object().subject_offering.subject.department_id
            if not staff or dept_id != staff.department_id:
                raise PermissionDenied

    def dispatch(self, request, *args, **kwargs):
        self._assert_hod_access()
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return reverse_lazy(
            "academics:task_list",
            kwargs={"pk": self.object.subject_offering_id},
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["offering"] = self.object.subject_offering
        ctx["title"] = f"Edit Task: {self.object.name}"
        ctx["submit_label"] = "Save Changes"
        return ctx


class AssessmentTaskDeactivateView(SeniorManagementMixin, View):
    """
    Remove (soft-deactivate) an assessment task.
    Senior management only - deactivating a task affects all linked marks records.
    """

    def post(self, request, pk):
        task = get_object_or_404(AssessmentTask, pk=pk)
        offering_pk = task.subject_offering_id
        task.is_active = False
        task.save(update_fields=["is_active", "updated_at"])
        messages.success(request, f"Task '{task.name}' removed.")
        return redirect("academics:task_list", pk=offering_pk)