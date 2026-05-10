from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, UpdateView

from ..forms.intervention import LearnerInterventionForm, ProgressNoteForm
from ..mixins import HODOrSeniorMixin, StaffMixin
from ..models.intervention import (
    LearnerIntervention,
    PerformanceAnalysis,
    ProgressNote,
)
from ..models.learner import Learner, LearnerGuardian
from ..models.school import AcademicYear, Term
from ..models.structure import Grade, GradeStream
from ..services.performance_analyzer import PerformanceAnalyzerService


class AtRiskDashboardView(StaffMixin, View):
    """
    Overview of at-risk learners for the current academic year.
    Teachers see only their homeroom stream's learners.
    HODs, Principals, and Admins see all.
    """

    template_name = "academics/intervention/at_risk.html"

    def get(self, request):
        current_year = AcademicYear.get_current()
        user = request.user

        analyses = PerformanceAnalysis.objects.none()
        if current_year:
            analyses = PerformanceAnalysis.objects.filter(
                academic_year=current_year
            ).select_related("learner")

            # Teachers: scope to their homeroom learners
            if user.role == "teacher":
                streams = GradeStream.objects.filter(
                    homeroom_teacher=user,
                    academic_year=current_year,
                    is_active=True,
                )
                from ..models.marks import SubjectEnrollment
                learner_ids = SubjectEnrollment.objects.filter(
                    stream__in=streams,
                    academic_year=current_year,
                ).values_list("learner_id", flat=True).distinct()
                analyses = analyses.filter(learner_id__in=learner_ids)

        # Filter by grade
        grade_id = request.GET.get("grade", "")
        if grade_id:
            from ..models.learner import LearnerEnrollment
            learner_ids = LearnerEnrollment.objects.filter(
                grade_id=grade_id,
                academic_year=current_year,
                is_active=True,
            ).values_list("learner_id", flat=True)
            analyses = analyses.filter(learner_id__in=learner_ids)

        # Filter by risk level
        risk_filter = request.GET.get("risk", "")
        if risk_filter:
            analyses = analyses.filter(risk_level=risk_filter)

        # Summary counts (before filtering, but after teacher scoping)
        all_analyses = PerformanceAnalysis.objects.filter(
            academic_year=current_year
        ) if current_year else PerformanceAnalysis.objects.none()

        summary = {
            "total": all_analyses.count(),
            "critical": all_analyses.filter(risk_level=PerformanceAnalysis.RISK_CRITICAL).count(),
            "high": all_analyses.filter(risk_level=PerformanceAnalysis.RISK_HIGH).count(),
            "medium": all_analyses.filter(risk_level=PerformanceAnalysis.RISK_MEDIUM).count(),
            "low": all_analyses.filter(risk_level=PerformanceAnalysis.RISK_LOW).count(),
        }

        # Add active intervention counts per learner
        if current_year:
            active_interventions = {
                li.learner_id: 0
                for li in LearnerIntervention.objects.filter(
                    academic_year=current_year,
                    status=LearnerIntervention.STATUS_ACTIVE,
                )
            }
            for li in LearnerIntervention.objects.filter(
                academic_year=current_year,
                status=LearnerIntervention.STATUS_ACTIVE,
            ):
                active_interventions[li.learner_id] = (
                    active_interventions.get(li.learner_id, 0) + 1
                )
        else:
            active_interventions = {}

        # Attach intervention count to analyses for the template
        analyses_list = list(analyses.order_by("-risk_score", "learner__last_name"))
        for a in analyses_list:
            a.intervention_count = active_interventions.get(a.learner_id, 0)

        return render(request, self.template_name, {
            "analyses": analyses_list,
            "summary": summary,
            "current_year": current_year,
            "grades": Grade.objects.filter(is_active=True).order_by("grade_number"),
            "risk_choices": PerformanceAnalysis.RISK_CHOICES,
            "selected_grade": grade_id,
            "selected_risk": risk_filter,
            "can_run_analysis": user.role in {"admin", "principal", "hod"},
        })


class RunAnalysisView(HODOrSeniorMixin, View):
    """
    Trigger performance analysis.
    POST with learner_pk: analyse that learner only.
    POST without: analyse all learners in the current academic year.
    """

    def post(self, request, learner_pk=None):
        current_year = AcademicYear.get_current()
        if not current_year:
            messages.error(request, "No current academic year is set.")
            return redirect("academics:at_risk")

        analyzer = PerformanceAnalyzerService()

        if learner_pk:
            learner = get_object_or_404(Learner, pk=learner_pk, is_active=True)
            with transaction.atomic():
                analyzer.analyze_learner(learner, current_year, run_by=request.user)
            messages.success(
                request,
                f"Analysis updated for {learner.get_full_name()}.",
            )
            return redirect("academics:learner_analysis", learner_pk=learner_pk)

        # Analyse all learners in current year
        with transaction.atomic():
            count = analyzer.analyze_all_for_year(current_year, run_by=request.user)

        messages.success(request, f"Analysis complete. {count} learners analysed.")
        return redirect("academics:at_risk")


class LearnerAnalysisView(StaffMixin, View):
    """
    Detailed performance analysis for one learner.
    Shows per-subject performance, risk flags, interventions, and notes.
    Access is scoped the same way as LearnerDetailView.
    """

    template_name = "academics/intervention/learner_analysis.html"

    def _get_learner(self, pk):
        return get_object_or_404(Learner, pk=pk, is_active=True)

    def _assert_access(self, user, learner):
        if user.role in {"admin", "principal", "admin_clerk"}:
            return
        if user.role in {"teacher", "hod"}:
            current_year = AcademicYear.get_current()
            if current_year:
                from ..models.marks import SubjectEnrollment
                streams = GradeStream.objects.filter(
                    homeroom_teacher=user,
                    academic_year=current_year,
                    is_active=True,
                )
                enrolled = SubjectEnrollment.objects.filter(
                    learner=learner,
                    stream__in=streams,
                    academic_year=current_year,
                ).exists()
                if enrolled:
                    return
            raise PermissionDenied
        if user.role == "parent":
            if not LearnerGuardian.objects.filter(
                guardian__user=user, learner=learner
            ).exists():
                raise PermissionDenied
            return
        raise PermissionDenied

    def get(self, request, learner_pk):
        learner = self._get_learner(learner_pk)
        self._assert_access(request.user, learner)

        current_year = AcademicYear.get_current()
        current_term = Term.get_current()

        # Get or compute analysis
        analysis = None
        subject_results = []
        if current_year:
            analysis = PerformanceAnalysis.objects.filter(
                learner=learner, academic_year=current_year
            ).first()

            # If no analysis exists, run one now
            if not analysis:
                analyzer = PerformanceAnalyzerService()
                analysis, subject_results = analyzer.analyze_learner(
                    learner, current_year, current_term
                )
            else:
                # Re-build subject_results from existing data for display
                analyzer = PerformanceAnalyzerService()
                _, subject_results = analyzer.analyze_learner(
                    learner, current_year, current_term
                )

        interventions = []
        notes = []
        if current_year:
            interventions = LearnerIntervention.objects.filter(
                learner=learner, academic_year=current_year
            ).select_related("program", "assigned_to").order_by("-created_at")

            notes = ProgressNote.objects.filter(
                learner=learner, academic_year=current_year
            ).select_related("created_by", "subject_offering__subject").order_by("-created_at")

        return render(request, self.template_name, {
            "learner": learner,
            "analysis": analysis,
            "subject_results": subject_results,
            "interventions": interventions,
            "notes": notes,
            "current_year": current_year,
            "can_manage": request.user.role in {"admin", "principal", "hod", "teacher"},
        })


class InterventionCreateView(HODOrSeniorMixin, SuccessMessageMixin, CreateView):
    """Add an intervention for a learner."""

    model = LearnerIntervention
    form_class = LearnerInterventionForm
    template_name = "academics/intervention/intervention_form.html"
    success_message = "Intervention added."

    def _get_learner(self):
        return get_object_or_404(Learner, pk=self.kwargs["learner_pk"], is_active=True)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["learner"] = self._get_learner()
        return kwargs

    def form_valid(self, form):
        current_year = AcademicYear.get_current()
        form.instance.learner = self._get_learner()
        form.instance.academic_year = current_year
        form.instance.created_by = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy(
            "academics:learner_analysis",
            kwargs={"learner_pk": self.kwargs["learner_pk"]},
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["learner"] = self._get_learner()
        ctx["title"] = "Add Intervention"
        ctx["submit_label"] = "Add Intervention"
        return ctx


class InterventionUpdateView(HODOrSeniorMixin, SuccessMessageMixin, UpdateView):
    """Update an existing intervention's status or outcome."""

    model = LearnerIntervention
    form_class = LearnerInterventionForm
    template_name = "academics/intervention/intervention_form.html"
    success_message = "Intervention updated."

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["learner"] = self.object.learner
        return kwargs

    def get_success_url(self):
        return reverse_lazy(
            "academics:learner_analysis",
            kwargs={"learner_pk": self.object.learner_id},
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["learner"] = self.object.learner
        ctx["title"] = f"Edit Intervention: {self.object.program.name}"
        ctx["submit_label"] = "Save Changes"
        return ctx


class ProgressNoteCreateView(StaffMixin, SuccessMessageMixin, CreateView):
    """Add a progress note for a learner."""

    model = ProgressNote
    form_class = ProgressNoteForm
    template_name = "academics/intervention/note_form.html"
    success_message = "Note added."

    def _get_learner(self):
        return get_object_or_404(Learner, pk=self.kwargs["learner_pk"], is_active=True)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["learner"] = self._get_learner()
        return kwargs

    def form_valid(self, form):
        form.instance.learner = self._get_learner()
        form.instance.academic_year = AcademicYear.get_current()
        form.instance.created_by = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy(
            "academics:learner_analysis",
            kwargs={"learner_pk": self.kwargs["learner_pk"]},
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["learner"] = self._get_learner()
        return ctx