from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from ..mixins import SeniorManagementMixin
from ..models.lurits import LURITSExport, LURITSValidationIssue
from ..models.school import AcademicYear, Term
from ..services.lurits_exporter import LURITSExporterService
from ..utils.lurits_validation import run_all_validations


class LURITSDashboardView(SeniorManagementMixin, View):
    """
    LURITS compliance dashboard.
    Shows data quality summary, validation status, and export history.
    Only accessible to Principal and Admin.
    """

    template_name = "academics/lurits/dashboard.html"

    def get(self, request):
        current_year = AcademicYear.get_current()
        current_term = Term.get_current()

        # Validation issue counts for current year
        errors = warnings = 0
        if current_year:
            errors = LURITSValidationIssue.objects.filter(
                academic_year=current_year,
                severity=LURITSValidationIssue.SEVERITY_ERROR,
            ).count()
            warnings = LURITSValidationIssue.objects.filter(
                academic_year=current_year,
                severity=LURITSValidationIssue.SEVERITY_WARNING,
            ).count()

        # Recent exports
        recent_exports = LURITSExport.objects.filter(
            academic_year=current_year
        ).order_by("-created_at")[:10] if current_year else []

        # All academic years for the export selector
        all_years = AcademicYear.objects.order_by("-year")

        return render(request, self.template_name, {
            "current_year": current_year,
            "current_term": current_term,
            "error_count": errors,
            "warning_count": warnings,
            "validation_run": errors + warnings > 0,
            "can_export": errors == 0,
            "recent_exports": recent_exports,
            "all_years": all_years,
            "export_types": LURITSExport.TYPE_CHOICES,
        })


class RunValidationView(SeniorManagementMixin, View):
    """
    Trigger a full data validation run for the selected academic year.
    POST only — results shown on the dashboard.
    """

    def post(self, request):
        year_pk = request.POST.get("academic_year_pk")
        if year_pk:
            academic_year = get_object_or_404(AcademicYear, pk=year_pk)
        else:
            academic_year = AcademicYear.get_current()

        if not academic_year:
            messages.error(request, "No academic year selected.")
            return redirect("academics:lurits_dashboard")

        errors, warnings = run_all_validations(academic_year)

        if errors == 0 and warnings == 0:
            messages.success(request, f"Validation passed. No issues found for {academic_year.year}.")
        else:
            messages.warning(
                request,
                f"Validation complete: {errors} error(s), {warnings} warning(s) for {academic_year.year}.",
            )

        return redirect("academics:lurits_validation")


class ValidationResultsView(SeniorManagementMixin, View):
    """Show all validation issues for the current academic year."""

    template_name = "academics/lurits/validation.html"

    def get(self, request):
        current_year = AcademicYear.get_current()

        all_issues = LURITSValidationIssue.objects.filter(
            academic_year=current_year
        ).select_related("learner").order_by(
            "severity", "category", "learner__last_name"
        ) if current_year else []

        errors = [i for i in all_issues if i.severity == LURITSValidationIssue.SEVERITY_ERROR]
        warnings = [i for i in all_issues if i.severity == LURITSValidationIssue.SEVERITY_WARNING]

        return render(request, self.template_name, {
            "current_year": current_year,
            "errors": errors,
            "warnings": warnings,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "can_export": len(errors) == 0,
            "category_choices": LURITSValidationIssue.CATEGORY_CHOICES,
        })


class ExportDataView(SeniorManagementMixin, View):
    """
    Generate a LURITS export file and serve it as a download.
    POST parameters: export_type, academic_year_pk, term_number (if needed).
    """

    def post(self, request):
        export_type = request.POST.get("export_type", "")
        year_pk = request.POST.get("academic_year_pk")
        term_number = int(request.POST.get("term_number", 1))

        academic_year = get_object_or_404(AcademicYear, pk=year_pk) if year_pk else AcademicYear.get_current()

        if not academic_year:
            messages.error(request, "No academic year configured.")
            return redirect("academics:lurits_dashboard")

        # Block export if there are errors (can be overridden with force param)
        error_count = LURITSValidationIssue.objects.filter(
            academic_year=academic_year,
            severity=LURITSValidationIssue.SEVERITY_ERROR,
        ).count()

        force = request.POST.get("force_export") == "1"
        if error_count > 0 and not force:
            messages.error(
                request,
                f"Cannot export: {error_count} validation error(s) must be resolved first. "
                f"Fix the errors or use the override option.",
            )
            return redirect("academics:lurits_validation")

        service = LURITSExporterService()

        try:
            if export_type == LURITSExport.TYPE_LEARNERS:
                export = service.export_learners(academic_year)
            elif export_type == LURITSExport.TYPE_ENROLLMENT:
                export = service.export_enrollment(academic_year)
            elif export_type == LURITSExport.TYPE_RESULTS:
                export = service.export_results(academic_year, term_number)
            elif export_type == LURITSExport.TYPE_ATTENDANCE:
                export = service.export_attendance(academic_year, term_number)
            elif export_type == LURITSExport.TYPE_FULL:
                export = service.export_full_zip(academic_year, term_number)
            else:
                messages.error(request, f"Unknown export type: {export_type}")
                return redirect("academics:lurits_dashboard")

            export.created_by = request.user
            export.errors_found = error_count
            export.warnings_found = LURITSValidationIssue.objects.filter(
                academic_year=academic_year,
                severity=LURITSValidationIssue.SEVERITY_WARNING,
            ).count()
            export.save(update_fields=["created_by", "errors_found", "warnings_found"])

        except Exception as exc:
            messages.error(request, f"Export failed: {exc}")
            return redirect("academics:lurits_dashboard")

        messages.success(request, f"{export.get_export_type_display()} generated.")
        return redirect(reverse("academics:lurits_download", kwargs={"pk": export.pk}))


class ExportDownloadView(SeniorManagementMixin, View):
    """Serve the generated export file as a download."""

    def get(self, request, pk):
        export = get_object_or_404(LURITSExport, pk=pk, status=LURITSExport.STATUS_READY)

        if not export.file:
            raise Http404("Export file not found.")

        content_type = "application/zip" if export.filename.endswith(".zip") else "text/csv"

        try:
            return FileResponse(
                export.file.open("rb"),
                content_type=content_type,
                as_attachment=True,
                filename=export.filename,
            )
        except (OSError, FileNotFoundError):
            raise Http404("Export file not found on disk.")