from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import models

from .learner import Learner
from .curriculum import SubjectOffering, AssessmentTask
from .school import AcademicYear
from .structure import GradeStream


class SubjectEnrollment(models.Model):
    """
    Links a learner to a subject for a specific academic year.
    Auto-created when marks are first captured for a learner in a stream.
    This is the pivot around which all marks data is organised.
    """

    learner = models.ForeignKey(
        Learner,
        on_delete=models.PROTECT,
        related_name="subject_enrollments",
    )
    subject_offering = models.ForeignKey(
        SubjectOffering,
        on_delete=models.PROTECT,
        related_name="subject_enrollments",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="subject_enrollments",
    )
    stream = models.ForeignKey(
        GradeStream,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subject_enrollments",
    )

    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "subject_enrollments"
        verbose_name = "Subject Enrollment"
        verbose_name_plural = "Subject Enrollments"
        unique_together = [["learner", "subject_offering", "academic_year"]]
        ordering = ["learner__last_name", "learner__first_name"]
        indexes = [
            models.Index(
                fields=["subject_offering", "academic_year", "stream"],
                name="idx_senroll_offering_year_strm",
            ),
            models.Index(
                fields=["learner", "academic_year"],
                name="idx_senroll_learner_year",
            ),
        ]

    def __str__(self):
        return (
            f"{self.learner.get_full_name()} - "
            f"{self.subject_offering.subject.code} "
            f"({self.academic_year.year})"
        )


class AssessmentRecord(models.Model):
    """
    Individual mark for one learner on one assessment task.
    Raw mark is what the teacher enters. Moderated mark overrides it
    if set, but the raw mark is always preserved for audit purposes.
    The effective_mark property always returns the correct value to use
    in calculations.
    """

    subject_enrollment = models.ForeignKey(
        SubjectEnrollment,
        on_delete=models.CASCADE,
        related_name="records",
    )
    assessment_task = models.ForeignKey(
        AssessmentTask,
        on_delete=models.PROTECT,
        related_name="records",
    )

    # The mark as entered by the teacher
    raw_mark = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Mark as captured. Null means not yet entered.",
    )
    is_absent = models.BooleanField(
        default=False,
        help_text="Learner was absent. Counts as 0 in calculations.",
    )

    # Moderation - non-destructive, raw_mark is preserved
    moderated_mark = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="HOD/principal moderated mark. Overrides raw_mark in calculations.",
    )
    moderation_reason = models.TextField(blank=True)
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="moderated_records",
    )
    moderated_at = models.DateTimeField(null=True, blank=True)

    # Audit trail
    captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="captured_records",
    )
    captured_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assessment_records"
        verbose_name = "Assessment Record"
        verbose_name_plural = "Assessment Records"
        unique_together = [["subject_enrollment", "assessment_task"]]
        indexes = [
            models.Index(
                fields=["subject_enrollment", "assessment_task"],
                name="idx_record_enrollment_task",
            ),
        ]

    def __str__(self):
        return (
            f"{self.subject_enrollment.learner.get_full_name()} - "
            f"{self.assessment_task.name}: {self.effective_mark}"
        )

    @property
    def effective_mark(self):
        """
        The mark to use in all calculations.
        Priority: moderated_mark > raw_mark > 0 (if absent).
        """
        if self.is_absent:
            return Decimal("0")
        if self.moderated_mark is not None:
            return self.moderated_mark
        return self.raw_mark or Decimal("0")

    @property
    def percentage(self):
        """Mark as a percentage of max_marks."""
        max_m = self.assessment_task.max_marks
        if not max_m:
            return None
        return (self.effective_mark / Decimal(str(max_m)) * 100).quantize(
            Decimal("0.01")
        )

    def is_captured(self):
        return self.raw_mark is not None or self.is_absent


class TermMark(models.Model):
    """
    Calculated mark summary for a learner in a subject for one term.
    Recalculated by MarksCalculatorService whenever an AssessmentRecord
    in this term is saved. Stores the term SBA percentage and any
    exam marks that fall within this term (e.g. mid-year exam).
    """

    TERM_CHOICES = [
        (1, "Term 1"), (2, "Term 2"), (3, "Term 3"), (4, "Term 4"),
    ]

    subject_enrollment = models.ForeignKey(
        SubjectEnrollment,
        on_delete=models.CASCADE,
        related_name="term_marks",
    )
    term_number = models.PositiveSmallIntegerField(choices=TERM_CHOICES)

    # Percentage scores for each component present in this term
    sba_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Percentage of SBA tasks in this term",
    )
    exam_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Percentage of examination tasks in this term (e.g. mid-year exam)",
    )

    # Progress tracking
    tasks_total = models.PositiveIntegerField(default=0)
    tasks_captured = models.PositiveIntegerField(default=0)

    is_locked = models.BooleanField(
        default=False,
        help_text="Locked when the term closes. Prevents further edits.",
    )
    calculated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "term_marks"
        verbose_name = "Term Mark"
        verbose_name_plural = "Term Marks"
        unique_together = [["subject_enrollment", "term_number"]]
        ordering = ["term_number"]

    def __str__(self):
        return (
            f"{self.subject_enrollment.learner.get_full_name()} - "
            f"T{self.term_number} {self.subject_enrollment.subject_offering.subject.code}"
        )

    def is_complete(self):
        return self.tasks_total > 0 and self.tasks_captured >= self.tasks_total


class FinalMark(models.Model):
    """
    Year-end final mark for a learner in a subject.
    Calculated from all SBA tasks across the year + all exam tasks.
    Stores each component separately for transparent reporting.
    Published when the principal releases results.
    """

    RESULT_DISTINCTION = "distinction"
    RESULT_MERIT = "merit"
    RESULT_PASS = "pass"
    RESULT_FAIL = "fail"
    RESULT_INCOMPLETE = "incomplete"

    RESULT_CHOICES = [
        (RESULT_DISTINCTION, "Distinction (80%+)"),
        (RESULT_MERIT, "Merit (70-79%)"),
        (RESULT_PASS, "Pass"),
        (RESULT_FAIL, "Fail"),
        (RESULT_INCOMPLETE, "Incomplete"),
    ]

    subject_enrollment = models.OneToOneField(
        SubjectEnrollment,
        on_delete=models.CASCADE,
        related_name="final_mark",
    )

    # Component percentages
    year_sba_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Overall SBA percentage across all terms",
    )
    exam_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Overall exam percentage",
    )

    # Weighted contributions
    sba_contribution = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="SBA percentage × SBA weighting / 100",
    )
    exam_contribution = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Exam percentage × exam weighting / 100",
    )

    final_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    result = models.CharField(
        max_length=20,
        choices=RESULT_CHOICES,
        default=RESULT_INCOMPLETE,
        blank=True,
    )

    is_published = models.BooleanField(
        default=False,
        help_text="When True, visible on parent portal and report cards",
    )
    calculated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "final_marks"
        verbose_name = "Final Mark"
        verbose_name_plural = "Final Marks"

    def __str__(self):
        return (
            f"{self.subject_enrollment.learner.get_full_name()} - "
            f"{self.subject_enrollment.subject_offering.subject.code}: "
            f"{self.final_percentage}%"
        )

    def passed(self):
        return self.result in {self.RESULT_PASS, self.RESULT_MERIT, self.RESULT_DISTINCTION}