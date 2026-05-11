from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, UpdateView
from scholr.features import bridge_enabled as _bridge_enabled
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
                learner_ids = (
                    SubjectEnrollment.objects
                    .filter(stream__in=streams, academic_year=current_year)
                    .values_list("learner_id", flat=True)
                    .distinct()
                )
                analyses = analyses.filter(learner_id__in=learner_ids)

        # Grade filter
        grade_id = request.GET.get("grade", "")
        if grade_id:
            from ..models.learner import LearnerEnrollment
            learner_ids = LearnerEnrollment.objects.filter(
                grade_id=grade_id,
                academic_year=current_year,
                is_active=True,
            ).values_list("learner_id", flat=True)
            analyses = analyses.filter(learner_id__in=learner_ids)

        # Risk level filter
        risk_filter = request.GET.get("risk", "")
        if risk_filter:
            analyses = analyses.filter(risk_level=risk_filter)

        # Summary counts (before filtering, after teacher scoping)
        all_analyses = (
            PerformanceAnalysis.objects.filter(academic_year=current_year)
            if current_year
            else PerformanceAnalysis.objects.none()
        )
        summary = {
            "total":    all_analyses.count(),
            "critical": all_analyses.filter(risk_level=PerformanceAnalysis.RISK_CRITICAL).count(),
            "high":     all_analyses.filter(risk_level=PerformanceAnalysis.RISK_HIGH).count(),
            "medium":   all_analyses.filter(risk_level=PerformanceAnalysis.RISK_MEDIUM).count(),
            "low":      all_analyses.filter(risk_level=PerformanceAnalysis.RISK_LOW).count(),
        }

        # Active intervention counts per learner
        active_interventions = {}
        if current_year:
            for li in LearnerIntervention.objects.filter(
                academic_year=current_year,
                status=LearnerIntervention.STATUS_ACTIVE,
            ):
                active_interventions[li.learner_id] = (
                    active_interventions.get(li.learner_id, 0) + 1
                )

        analyses_list = list(analyses.order_by("-risk_score", "learner__last_name"))
        for a in analyses_list:
            a.intervention_count = active_interventions.get(a.learner_id, 0)

        # Online enrollment status per learner (LMS Bridge)
        online_enrolled_ids = set()
        if _bridge_enabled() and current_year:
            from ..models.lms_link import OnlineEnrollment
            online_enrolled_ids = set(
                OnlineEnrollment.objects.filter(
                    academic_year=current_year,
                    status=OnlineEnrollment.STATUS_ACTIVE,
                ).values_list("learner_id", flat=True)
            )

        return render(request, self.template_name, {
            "analyses":          analyses_list,
            "summary":           summary,
            "current_year":      current_year,
            "grades":            Grade.objects.filter(is_active=True).order_by("grade_number"),
            "risk_choices":      PerformanceAnalysis.RISK_CHOICES,
            "selected_grade":    grade_id,
            "selected_risk":     risk_filter,
            "can_run_analysis":  user.role in {"admin", "principal", "hod"},
            "can_enroll_online": user.role in {"admin", "principal", "admin_clerk", "hod"},
            # Bridge
            "bridge_enabled":      _bridge_enabled(),
            "online_enrolled_ids": online_enrolled_ids,
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
    Shows per-subject performance, risk flags, interventions, notes,
    and (when the LMS Bridge is enabled) online learning status.
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

        # Performance analysis — compute if not yet run
        analysis = None
        subject_results = []
        if current_year:
            analysis = PerformanceAnalysis.objects.filter(
                learner=learner, academic_year=current_year
            ).first()

            analyzer = PerformanceAnalyzerService()
            if not analysis:
                analysis, subject_results = analyzer.analyze_learner(
                    learner, current_year, current_term
                )
            else:
                # Rebuild subject_results for display without re-persisting
                _, subject_results = analyzer.analyze_learner(
                    learner, current_year, current_term
                )

        interventions = []
        notes = []
        if current_year:
            interventions = (
                LearnerIntervention.objects
                .filter(learner=learner, academic_year=current_year)
                .select_related("program", "assigned_to")
                .order_by("-created_at")
            )
            notes = (
                ProgressNote.objects
                .filter(learner=learner, academic_year=current_year)
                .select_related("created_by", "subject_offering__subject")
                .order_by("-created_at")
            )

        context = {
            "learner":         learner,
            "analysis":        analysis,
            "subject_results": subject_results,
            "interventions":   interventions,
            "notes":           notes,
            "current_year":    current_year,
            "can_manage":      request.user.role in {
                "admin", "principal", "hod", "teacher"
            },
        }

        # ---- Online Learning (LMS Bridge) ----
        context["bridge_enabled"] = _bridge_enabled()

        if _bridge_enabled():
            from ..models.lms_link import OnlineEnrollment, LearnerLMSAccount
            from ..services.lms_bridge import LMSBridgeService

            # LMS account
            lms_account = None
            try:
                lms_account = learner.lms_account
            except LearnerLMSAccount.DoesNotExist:
                pass
            context["lms_account"] = lms_account

            # Active online enrollments this year
            context["online_enrollments"] = (
                OnlineEnrollment.objects.filter(
                    learner=learner,
                    academic_year=current_year,
                    status=OnlineEnrollment.STATUS_ACTIVE,
                ).select_related("course__category", "subject")
                if current_year else []
            )

            # Suggested available courses (only when account is synced)
            suggested = []
            if lms_account and lms_account.is_synced_to_moodle():
                try:
                    service = LMSBridgeService()
                    suggested = list(
                        service.get_available_courses(learner, current_year)[:6]
                    )
                except Exception:
                    pass
            context["suggested_courses"] = suggested

            context["can_enroll_online"] = request.user.role in {
                "admin", "principal", "admin_clerk", "hod"
            }
            context["can_manage_lms"] = request.user.role in {
                "admin", "principal", "admin_clerk"
            }

        return render(request, self.template_name, context)

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
        form.instance.learner = self._get_learner()
        form.instance.academic_year = AcademicYear.get_current()
        form.instance.created_by = self.request.user
        response = super().form_valid(form)

        # Optional: simultaneously enroll in an online course if staff selected one
        course_id = self.request.POST.get('enroll_course_id', '').strip()
        if course_id and _bridge_enabled():
            try:
                from integration.models import Course as LMSCourse
                from ..services.lms_bridge import LMSBridgeService

                course = LMSCourse.objects.filter(
                    pk=course_id, sync_status='synced',
                ).first()

                if course:
                    service = LMSBridgeService()
                    service.enroll_in_course(
                        learner=form.instance.learner,
                        course=course,
                        enrolled_by=self.request.user,
                        reason=f'Linked to intervention: {form.instance.program.name}',
                        intervention=form.instance,
                        academic_year=form.instance.academic_year,
                    )
                    messages.success(
                        self.request,
                        f'Also enrolled in "{course.title}" online.',
                    )
            except Exception as exc:
                messages.warning(
                    self.request,
                    f'Intervention created but online enrollment failed: {exc}',
                )

        return response

    def get_success_url(self):
        return reverse_lazy(
            "academics:learner_analysis",
            kwargs={"learner_pk": self.kwargs["learner_pk"]},
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        learner = self._get_learner()
        ctx['learner'] = learner
        ctx["title"] = "Add Intervention"
        ctx["submit_label"] = "Add Intervention"

        # Online learning context for optional course enrollment
        from scholr.features import bridge_enabled as _bridge_enabled
        ctx['bridge_enabled'] = _bridge_enabled()

        if _bridge_enabled():
            from ..models.lms_link import LearnerLMSAccount
            from ..services.lms_bridge import LMSBridgeService
            from ..models.school import AcademicYear

            lms_account = None
            try:
                lms_account = learner.lms_account
            except LearnerLMSAccount.DoesNotExist:
                pass

            ctx['lms_account'] = lms_account
            ctx['available_courses'] = []

            if lms_account and lms_account.is_synced_to_moodle():
                try:
                    current_year = AcademicYear.get_current()
                    service = LMSBridgeService()
                    ctx['available_courses'] = list(
                        service.get_available_courses(learner, current_year)
                    )
                except Exception:
                    pass

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