from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from .school import Department
from .structure import Grade


class Curriculum(models.Model):
    """
    Curriculum type for this school.
    One is marked as default - this is the school's primary curriculum.
    Multiple can be active if the school runs e.g. CAPS and Cambridge in parallel.
    """

    CAPS = "caps"
    IEB = "ieb"
    CAMBRIDGE = "cambridge"
    IB = "ib"
    CUSTOM = "custom"

    TYPE_CHOICES = [
        (CAPS, "CAPS (DBE)"),
        (IEB, "IEB"),
        (CAMBRIDGE, "Cambridge (IGCSE / AS / A-Level)"),
        (IB, "International Baccalaureate"),
        (CUSTOM, "Custom Curriculum"),
    ]

    code = models.CharField(max_length=20, unique=True, choices=TYPE_CHOICES)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(
        default=False,
        help_text="The school's primary curriculum. Only one can be default.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "curricula"
        verbose_name = "Curriculum"
        verbose_name_plural = "Curricula"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        """Enforce a single default curriculum."""
        if self.is_default:
            Curriculum.objects.exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    @classmethod
    def get_default(cls):
        return cls.objects.filter(is_default=True).first()


class SchoolSubject(models.Model):
    """
    A subject offered at this school.
    Not the same as accounts.Subject which is for LMS course cataloguing.
    This model carries DBE/LURITS metadata for compliance reporting.
    """

    TYPE_LANGUAGE = "language"
    TYPE_MATHEMATICS = "mathematics"
    TYPE_NATURAL_SCIENCE = "natural_science"
    TYPE_SOCIAL_SCIENCE = "social_science"
    TYPE_TECHNOLOGY = "technology"
    TYPE_ARTS = "arts"
    TYPE_COMMERCE = "commerce"
    TYPE_LIFE_ORIENTATION = "life_orientation"
    TYPE_AGRICULTURAL = "agricultural"
    TYPE_OTHER = "other"

    TYPE_CHOICES = [
        (TYPE_LANGUAGE, "Language"),
        (TYPE_MATHEMATICS, "Mathematics"),
        (TYPE_NATURAL_SCIENCE, "Natural Sciences"),
        (TYPE_SOCIAL_SCIENCE, "Social Sciences"),
        (TYPE_TECHNOLOGY, "Technology"),
        (TYPE_ARTS, "Arts and Culture"),
        (TYPE_COMMERCE, "Commerce"),
        (TYPE_LIFE_ORIENTATION, "Life Orientation"),
        (TYPE_AGRICULTURAL, "Agricultural Sciences"),
        (TYPE_OTHER, "Other"),
    ]

    # Tailwind colour per type - used in templates
    TYPE_COLOURS = {
        TYPE_LANGUAGE: "blue",
        TYPE_MATHEMATICS: "indigo",
        TYPE_NATURAL_SCIENCE: "green",
        TYPE_SOCIAL_SCIENCE: "yellow",
        TYPE_TECHNOLOGY: "cyan",
        TYPE_ARTS: "pink",
        TYPE_COMMERCE: "orange",
        TYPE_LIFE_ORIENTATION: "teal",
        TYPE_AGRICULTURAL: "lime",
        TYPE_OTHER: "gray",
    }

    curriculum = models.ForeignKey(
        Curriculum,
        on_delete=models.PROTECT,
        related_name="subjects",
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subjects",
        help_text="Department responsible for this subject - determines HOD access",
    )

    name = models.CharField(max_length=150, help_text="Full name e.g. 'English Home Language'")
    code = models.CharField(
        max_length=20,
        unique=True,
        help_text="Short code for registers and reports e.g. 'EHL', 'MAT', 'PHY'",
    )
    lurits_code = models.CharField(
        max_length=10,
        blank=True,
        help_text="DBE subject code for LURITS submissions. Verify against current LURITS specification.",
    )
    subject_type = models.CharField(max_length=30, choices=TYPE_CHOICES)

    is_home_language = models.BooleanField(
        default=False,
        help_text="Mark True for home language subjects - affects assessment weighting in some phases",
    )

    # Default weightings - can be overridden per grade on SubjectOffering
    default_sba_weighting = models.PositiveIntegerField(
        default=25,
        help_text="Default SBA percentage. Adjust per grade on SubjectOffering.",
    )
    default_exam_weighting = models.PositiveIntegerField(
        default=75,
        help_text="Default exam percentage. Must sum to 100 with SBA.",
    )
    default_pass_mark = models.PositiveIntegerField(
        default=40,
        help_text="Minimum pass percentage for CAPS (typically 40%)",
    )

    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "school_subjects"
        verbose_name = "School Subject"
        verbose_name_plural = "School Subjects"
        ordering = ["sort_order", "name"]
        indexes = [
            models.Index(fields=["curriculum", "is_active"], name="idx_subject_curr_active"),
            models.Index(fields=["subject_type"], name="idx_subject_type"),
            models.Index(fields=["department", "is_active"], name="idx_subject_dept_active"),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def get_type_colour(self):
        return self.TYPE_COLOURS.get(self.subject_type, "gray")

    def active_offering_count(self):
        return self.offerings.filter(is_active=True).count()


class SubjectOffering(models.Model):
    """
    A subject offered at a specific grade.
    Defines the assessment weighting and pass mark for this grade-subject pair.
    Year-independent - the same offering applies every academic year.
    Assessment tasks are attached to this model.
    """

    subject = models.ForeignKey(
        SchoolSubject,
        on_delete=models.PROTECT,
        related_name="offerings",
    )
    grade = models.ForeignKey(
        Grade,
        on_delete=models.PROTECT,
        related_name="subject_offerings",
    )

    is_compulsory = models.BooleanField(
        default=True,
        help_text="Compulsory for all learners in this grade. Set False for electives (Grade 10+).",
    )

    # Weighting for this specific grade - validated to sum to 100
    sba_weighting = models.PositiveIntegerField(
        help_text="SBA component percentage e.g. 25 for CAPS FET",
    )
    exam_weighting = models.PositiveIntegerField(
        help_text="Examination component percentage e.g. 75 for CAPS FET",
    )

    pass_mark = models.PositiveIntegerField(
        default=40,
        help_text="Minimum pass percentage for this subject at this grade",
    )

    is_active = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "subject_offerings"
        verbose_name = "Subject Offering"
        verbose_name_plural = "Subject Offerings"
        unique_together = [["subject", "grade"]]
        ordering = ["grade__grade_number", "subject__sort_order"]
        indexes = [
            models.Index(fields=["grade", "is_active"], name="idx_offering_grade_active"),
            models.Index(fields=["subject", "is_active"], name="idx_offering_subject_active"),
        ]

    def __str__(self):
        return f"{self.subject.name} - {self.grade.name}"

    def clean(self):
        """Weighting must sum to exactly 100. This is a hard constraint."""
        total = self.sba_weighting + self.exam_weighting
        if total != 100:
            raise ValidationError({
                "exam_weighting": (
                    f"SBA ({self.sba_weighting}%) + Exam ({self.exam_weighting}%) "
                    f"= {total}%. These must sum to exactly 100%."
                )
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def get_sba_tasks(self):
        return self.tasks.filter(is_sba=True, is_active=True).order_by("term_number", "sequence")

    def get_exam_tasks(self):
        return self.tasks.filter(is_sba=False, is_active=True).order_by("term_number", "sequence")

    def get_tasks_for_term(self, term_number):
        return self.tasks.filter(
            term_number=term_number, is_active=True
        ).order_by("sequence")

    def total_sba_marks(self):
        """Sum of max_marks for all active SBA tasks."""
        from django.db.models import Sum
        result = self.tasks.filter(is_sba=True, is_active=True).aggregate(
            total=Sum("max_marks")
        )
        return result["total"] or 0

    def total_exam_marks(self):
        """Sum of max_marks for all active examination tasks."""
        from django.db.models import Sum
        result = self.tasks.filter(is_sba=False, is_active=True).aggregate(
            total=Sum("max_marks")
        )
        return result["total"] or 0


class AssessmentTask(models.Model):
    """
    Assessment task definition for a subject offering.
    A year-independent template - the same tasks repeat every academic year.
    Actual marks per learner are stored in Stage 5's AssessmentRecord.

    Using term_number (1-4) rather than a FK to Term, because tasks are
    perpetual templates that must work across multiple academic years.
    """

    TYPE_TEST = "test"
    TYPE_EXAM = "examination"
    TYPE_ASSIGNMENT = "assignment"
    TYPE_PROJECT = "project"
    TYPE_ORAL = "oral"
    TYPE_PRACTICAL = "practical"
    TYPE_INVESTIGATION = "investigation"
    TYPE_PORTFOLIO = "portfolio"
    TYPE_PERFORMANCE = "performance"
    TYPE_OTHER = "other"

    TASK_TYPE_CHOICES = [
        (TYPE_TEST, "Test"),
        (TYPE_EXAM, "Examination"),
        (TYPE_ASSIGNMENT, "Assignment"),
        (TYPE_PROJECT, "Project"),
        (TYPE_ORAL, "Oral"),
        (TYPE_PRACTICAL, "Practical"),
        (TYPE_INVESTIGATION, "Investigation"),
        (TYPE_PORTFOLIO, "Portfolio"),
        (TYPE_PERFORMANCE, "Performance Task"),
        (TYPE_OTHER, "Other"),
    ]

    TERM_CHOICES = [
        (1, "Term 1"),
        (2, "Term 2"),
        (3, "Term 3"),
        (4, "Term 4"),
    ]

    subject_offering = models.ForeignKey(
        SubjectOffering,
        on_delete=models.CASCADE,
        related_name="tasks",
    )

    term_number = models.PositiveSmallIntegerField(
        choices=TERM_CHOICES,
        help_text="Which term this task falls in",
    )
    name = models.CharField(
        max_length=100,
        help_text="e.g. 'Test 1', 'Mid-Year Exam', 'Research Project'",
    )
    task_type = models.CharField(max_length=30, choices=TASK_TYPE_CHOICES)

    max_marks = models.PositiveIntegerField(
        help_text="Total marks available e.g. 50 or 100",
    )

    is_sba = models.BooleanField(
        default=True,
        help_text=(
            "True = SBA (continuous assessment). "
            "False = Formal Examination. "
            "Controls which weighting component this mark contributes to."
        ),
    )

    # Optional individual weight within SBA - if null, equal weighting is applied
    sba_weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Percentage weight within the SBA component. "
            "Leave blank for equal weighting across all SBA tasks."
        ),
    )

    sequence = models.PositiveIntegerField(
        default=1,
        help_text="Display order within the term. Lower = earlier.",
    )

    typical_week = models.CharField(
        max_length=50,
        blank=True,
        help_text="Informational timing note e.g. 'Week 4' or 'Last week of term'",
    )

    is_active = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assessment_tasks"
        verbose_name = "Assessment Task"
        verbose_name_plural = "Assessment Tasks"
        ordering = ["term_number", "sequence"]
        indexes = [
            models.Index(
                fields=["subject_offering", "term_number"],
                name="idx_task_offering_term",
            ),
            models.Index(fields=["is_sba", "is_active"], name="idx_task_sba_active"),
        ]

    def __str__(self):
        component = "SBA" if self.is_sba else "Exam"
        return (
            f"{self.subject_offering} | "
            f"T{self.term_number} {self.name} ({component}, {self.max_marks} marks)"
        )

    def get_weight_display(self):
        if self.sba_weight:
            return f"{self.sba_weight}% of SBA"
        return "Equal SBA weighting"