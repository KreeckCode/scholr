from django.conf import settings
from django.db import models

from .learner import Learner
from .school import AcademicYear
from .structure import GradeStream
from .marks import SubjectEnrollment
from .curriculum import SubjectOffering


class ReportCardTemplate(models.Model):
    """
    Defines the structure and content of a report card.
    Boolean flags control which sections appear.
    One template is marked as default and used for all generation
    unless overridden.
    """

    TYPE_CAPS = "caps"
    TYPE_IEB = "ieb"
    TYPE_CUSTOM = "custom"
    TYPE_CHOICES = [
        (TYPE_CAPS, "CAPS (DBE)"),
        (TYPE_IEB, "IEB"),
        (TYPE_CUSTOM, "Custom"),
    ]

    name = models.CharField(max_length=150)
    template_type = models.CharField(
        max_length=20, choices=TYPE_CHOICES, default=TYPE_CAPS
    )

    # Section visibility controls
    show_attendance = models.BooleanField(default=True)
    show_conduct = models.BooleanField(default=True)
    show_subject_comments = models.BooleanField(
        default=True,
        help_text="Show subject teacher comments per subject row",
    )
    show_homeroom_comment = models.BooleanField(default=True)
    show_principal_comment = models.BooleanField(default=True)
    show_promotion_status = models.BooleanField(
        default=False,
        help_text="Only relevant for Term 4 / year-end reports",
    )
    show_caps_symbol = models.BooleanField(
        default=True,
        help_text="Show CAPS achievement symbol (1-7) alongside the percentage",
    )

    # Optional customisation
    footer_text = models.CharField(
        max_length=300,
        blank=True,
        help_text="Text shown in the report card footer (e.g. school motto)",
    )
    comment_max_length = models.PositiveIntegerField(
        default=150,
        help_text="Maximum characters for subject teacher comments",
    )

    is_default = models.BooleanField(
        default=False,
        help_text="Used when no specific template is selected",
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "report_card_templates"
        verbose_name = "Report Card Template"
        verbose_name_plural = "Report Card Templates"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.is_default:
            ReportCardTemplate.objects.exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    @classmethod
    def get_default(cls):
        return cls.objects.filter(is_default=True, is_active=True).first()


class TeacherComment(models.Model):
    """
    Subject teacher comment on a learner for a specific term.
    One comment per learner per subject per term.
    CAPS requires a brief comment per subject.
    """

    TERM_CHOICES = [
        (1, "Term 1"), (2, "Term 2"), (3, "Term 3"), (4, "Term 4"),
    ]

    subject_enrollment = models.ForeignKey(
        SubjectEnrollment,
        on_delete=models.CASCADE,
        related_name="teacher_comments",
    )
    term_number = models.PositiveSmallIntegerField(choices=TERM_CHOICES)
    comment = models.TextField(
        max_length=300,
        help_text="Brief comment on learner progress in this subject",
    )

    captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="comments_captured",
    )
    captured_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "teacher_comments"
        verbose_name = "Teacher Comment"
        verbose_name_plural = "Teacher Comments"
        unique_together = [["subject_enrollment", "term_number"]]
        ordering = ["term_number"]

    def __str__(self):
        return (
            f"{self.subject_enrollment.learner.get_full_name()} - "
            f"{self.subject_enrollment.subject_offering.subject.code} T{self.term_number}"
        )


class ConductRating(models.Model):
    """
    Effort, conduct, attendance summary, and homeroom/principal comments
    per learner per term. Captured by the homeroom teacher.
    """

    RATING_1 = "1"
    RATING_2 = "2"
    RATING_3 = "3"
    RATING_4 = "4"

    RATING_CHOICES = [
        (RATING_1, "Outstanding"),
        (RATING_2, "Good"),
        (RATING_3, "Satisfactory"),
        (RATING_4, "Needs Improvement"),
    ]

    TERM_CHOICES = [
        (1, "Term 1"), (2, "Term 2"), (3, "Term 3"), (4, "Term 4"),
    ]

    learner = models.ForeignKey(
        Learner,
        on_delete=models.CASCADE,
        related_name="conduct_ratings",
    )
    stream = models.ForeignKey(
        GradeStream,
        on_delete=models.CASCADE,
        related_name="conduct_ratings",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name="conduct_ratings",
    )
    term_number = models.PositiveSmallIntegerField(choices=TERM_CHOICES)

    effort_rating = models.CharField(
        max_length=2,
        choices=RATING_CHOICES,
        blank=True,
        help_text="Learner's effort in class",
    )
    conduct_rating = models.CharField(
        max_length=2,
        choices=RATING_CHOICES,
        blank=True,
        help_text="Learner's behaviour and conduct",
    )

    # Attendance for this term
    days_absent = models.PositiveIntegerField(
        default=0,
        help_text="Days absent this term",
    )
    days_late = models.PositiveIntegerField(
        default=0,
        help_text="Times arrived late this term",
    )

    homeroom_comment = models.TextField(
        blank=True,
        max_length=300,
        help_text="Class teacher's general comment on the learner",
    )
    principal_comment = models.TextField(
        blank=True,
        max_length=300,
        help_text="Principal's comment - optional",
    )

    captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="conduct_ratings_captured",
    )
    captured_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conduct_ratings"
        verbose_name = "Conduct Rating"
        verbose_name_plural = "Conduct Ratings"
        unique_together = [["learner", "stream", "academic_year", "term_number"]]
        ordering = ["term_number", "learner__last_name"]

    def __str__(self):
        return (
            f"{self.learner.get_full_name()} - "
            f"{self.stream.get_display_name()} T{self.term_number}"
        )

    def get_effort_label(self):
        return dict(self.RATING_CHOICES).get(self.effort_rating, "")

    def get_conduct_label(self):
        return dict(self.RATING_CHOICES).get(self.conduct_rating, "")


class ReportCard(models.Model):
    """
    A generated report card for one learner for one term.
    The PDF is generated once and stored. Re-generating replaces the file.
    Released cards are visible to parents in the parent portal (Stage 10).
    """

    learner = models.ForeignKey(
        Learner,
        on_delete=models.CASCADE,
        related_name="report_cards",
    )
    stream = models.ForeignKey(
        GradeStream,
        on_delete=models.CASCADE,
        related_name="report_cards",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name="report_cards",
    )
    term_number = models.PositiveSmallIntegerField(
        choices=[(1, "Term 1"), (2, "Term 2"), (3, "Term 3"), (4, "Term 4")]
    )
    template = models.ForeignKey(
        ReportCardTemplate,
        on_delete=models.PROTECT,
        related_name="report_cards",
    )

    # The stored PDF
    pdf_file = models.FileField(
        upload_to="report_cards/%Y/",
        null=True,
        blank=True,
        help_text="Generated PDF - replaced on re-generation",
    )

    # Generation state
    is_generated = models.BooleanField(default=False, db_index=True)
    generated_at = models.DateTimeField(null=True, blank=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="report_cards_generated",
    )

    # Release state
    is_released = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Released = visible to parents on the portal",
    )
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="report_cards_released",
    )

    class Meta:
        db_table = "report_cards"
        verbose_name = "Report Card"
        verbose_name_plural = "Report Cards"
        unique_together = [["learner", "academic_year", "term_number"]]
        ordering = ["-academic_year__year", "term_number", "learner__last_name"]

    def __str__(self):
        return (
            f"{self.learner.get_full_name()} - "
            f"T{self.term_number} {self.academic_year.year}"
        )