from django.conf import settings
from django.db import models

from .learner import Learner
from .school import AcademicYear


class LURITSExport(models.Model):
    """
    Tracks each LURITS data export job.
    One record per export run - stores the generated file and metadata.
    """

    TYPE_LEARNERS = "learners"
    TYPE_ENROLLMENT = "enrollment"
    TYPE_RESULTS = "results"
    TYPE_ATTENDANCE = "attendance"
    TYPE_FULL = "full"

    TYPE_CHOICES = [
        (TYPE_LEARNERS, "Learner Demographics"),
        (TYPE_ENROLLMENT, "Grade Enrollment"),
        (TYPE_RESULTS, "Assessment Results"),
        (TYPE_ATTENDANCE, "Attendance Data"),
        (TYPE_FULL, "Full Export (ZIP)"),
    ]

    STATUS_PENDING = "pending"
    STATUS_READY = "ready"
    STATUS_ERROR = "error"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Generating"),
        (STATUS_READY, "Ready for Download"),
        (STATUS_ERROR, "Generation Failed"),
    ]

    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="lurits_exports",
    )
    term_number = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Required for results and attendance exports",
    )
    export_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING
    )

    file = models.FileField(
        upload_to="lurits_exports/%Y/",
        null=True,
        blank=True,
    )
    filename = models.CharField(max_length=200, blank=True)

    # Stats
    total_records = models.IntegerField(default=0)
    errors_found = models.IntegerField(
        default=0,
        help_text="Validation errors at time of export",
    )
    warnings_found = models.IntegerField(default=0)

    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="lurits_exports",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "lurits_exports"
        verbose_name = "LURITS Export"
        verbose_name_plural = "LURITS Exports"
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"{self.get_export_type_display()} - "
            f"{self.academic_year.year} "
            f"({self.created_at.strftime('%d %b %Y')})"
        )


class LURITSValidationIssue(models.Model):
    """
    A single validation problem found during pre-export data checking.
    Errors block export. Warnings can be overridden.
    """

    SEVERITY_ERROR = "error"
    SEVERITY_WARNING = "warning"

    SEVERITY_CHOICES = [
        (SEVERITY_ERROR, "Error — Must Fix Before Export"),
        (SEVERITY_WARNING, "Warning — Review Recommended"),
    ]

    CATEGORY_LEARNER = "learner"
    CATEGORY_ENROLLMENT = "enrollment"
    CATEGORY_RESULTS = "results"
    CATEGORY_ATTENDANCE = "attendance"
    CATEGORY_SCHOOL = "school"

    CATEGORY_CHOICES = [
        (CATEGORY_LEARNER, "Learner Data"),
        (CATEGORY_ENROLLMENT, "Enrollment"),
        (CATEGORY_RESULTS, "Assessment Results"),
        (CATEGORY_ATTENDANCE, "Attendance"),
        (CATEGORY_SCHOOL, "School Profile"),
    ]

    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name="lurits_issues",
    )
    severity = models.CharField(
        max_length=10, choices=SEVERITY_CHOICES, db_index=True
    )
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    field = models.CharField(max_length=100, blank=True)
    message = models.CharField(max_length=500)

    # Affected learner - null for school-level issues
    learner = models.ForeignKey(
        Learner,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="lurits_issues",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "lurits_validation_issues"
        verbose_name = "LURITS Validation Issue"
        verbose_name_plural = "LURITS Validation Issues"
        ordering = ["severity", "category", "learner__last_name"]
        indexes = [
            models.Index(
                fields=["academic_year", "severity"],
                name="idx_lurits_year_severity",
            ),
        ]

    def __str__(self):
        who = self.learner.get_full_name() if self.learner else "School"
        return f"[{self.severity.upper()}] {who}: {self.message}"

    def is_error(self):
        return self.severity == self.SEVERITY_ERROR