from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator

from .school import AcademicYear, Department


class Grade(models.Model):
    """
    One of the 13 SA school grades (Grade R through Grade 12).
    Pre-seeded via management command - admin marks which are active
    based on the school's phase (primary/secondary/combined).
    Never created manually via admin.
    """

    PHASE_FOUNDATION = "foundation"
    PHASE_INTERMEDIATE = "intermediate"
    PHASE_SENIOR = "senior"
    PHASE_FET = "fet"

    PHASE_CHOICES = [
        (PHASE_FOUNDATION, "Foundation Phase (Grade R - 3)"),
        (PHASE_INTERMEDIATE, "Intermediate Phase (Grade 4 - 6)"),
        (PHASE_SENIOR, "Senior Phase (Grade 7 - 9)"),
        (PHASE_FET, "FET Phase (Grade 10 - 12)"),
    ]

    name = models.CharField(
        max_length=20,
        unique=True,
        help_text="Display name e.g. 'Grade R', 'Grade 1', 'Grade 12'",
    )
    grade_number = models.IntegerField(
        unique=True,
        validators=[MinValueValidator(0), MaxValueValidator(12)],
        help_text="0 = Grade R, 1-12 = Grade 1 through Grade 12",
    )
    phase = models.CharField(max_length=20, choices=PHASE_CHOICES)
    is_active = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Active grades are offered by this school. Inactive grades are hidden.",
    )
    max_learners_per_class = models.PositiveIntegerField(
        default=40,
        help_text="DBE guideline cap per stream. Used for capacity warnings.",
    )

    class Meta:
        db_table = "grades"
        verbose_name = "Grade"
        verbose_name_plural = "Grades"
        ordering = ["grade_number"]

    def __str__(self):
        return self.name

    def get_streams(self, academic_year=None):
        """Return active streams for this grade, optionally scoped to a year."""
        qs = self.streams.filter(is_active=True)
        if academic_year:
            qs = qs.filter(academic_year=academic_year)
        return qs.select_related("homeroom_teacher", "academic_year")

    @property
    def short_phase(self):
        return {
            self.PHASE_FOUNDATION: "Foundation",
            self.PHASE_INTERMEDIATE: "Intermediate",
            self.PHASE_SENIOR: "Senior",
            self.PHASE_FET: "FET",
        }.get(self.phase, self.phase)


class GradeStream(models.Model):
    """
    A class (stream) within a grade for a specific academic year.
    e.g. Grade 10A in 2026 and Grade 10A in 2027 are separate records.
    Streams are recreated each year by admin during year setup.
    The homeroom teacher is assigned here.
    """

    grade = models.ForeignKey(
        Grade,
        on_delete=models.PROTECT,
        related_name="streams",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="streams",
    )
    # Stream letter - 'A', 'B', 'C', or custom like 'Blue', 'Gold'
    name = models.CharField(
        max_length=10,
        help_text="Stream identifier e.g. 'A', 'B', 'Blue'",
    )
    homeroom_teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="homeroom_classes",
        limit_choices_to={"role": "teacher"},
        help_text="Class teacher responsible for registers and pastoral care",
    )
    room_number = models.CharField(max_length=20, blank=True)
    capacity = models.PositiveIntegerField(
        default=40,
        help_text="Maximum learners - triggers a warning when exceeded",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "grade_streams"
        verbose_name = "Class (Stream)"
        verbose_name_plural = "Classes (Streams)"
        unique_together = [["grade", "academic_year", "name"]]
        ordering = ["grade__grade_number", "name"]
        indexes = [
            models.Index(fields=["academic_year", "grade"], name="idx_stream_year_grade"),
            models.Index(fields=["homeroom_teacher"], name="idx_stream_teacher"),
            models.Index(fields=["is_active"], name="idx_stream_active"),
        ]

    def __str__(self):
        return f"{self.get_display_name()} ({self.academic_year.year})"

    def get_display_name(self):
        """Short label e.g. '10A', 'RA', '7B'."""
        num = self.grade.grade_number
        prefix = "R" if num == 0 else str(num)
        return f"{prefix}{self.name}"

    def get_learner_count(self):
        """Count currently active enrollments for this stream."""
        # I'm importing inline to avoid a circular import between models packages
        from .learner import LearnerEnrollment
        return LearnerEnrollment.objects.filter(
            stream=self,
            academic_year=self.academic_year,
            status=LearnerEnrollment.STATUS_ACTIVE,
        ).count()

    def is_full(self):
        return self.get_learner_count() >= self.capacity

    def available_capacity(self):
        return max(0, self.capacity - self.get_learner_count())