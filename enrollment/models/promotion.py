"""
Grade promotion and learner migration models.

Staff use these to move learners from one academic year and grade to the next.
The LearnerPromotion record drives the PromotionService which creates the
new LearnerEnrollment and (optionally) new SubjectEnrollment records.

BulkPromotionBatch groups individual promotions so admin can see
"Year End 2025 → 2026 Grade 9A → 10" as a single operation.
"""
from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


class BulkPromotionBatch(models.Model):
    """
    Groups LearnerPromotion records created in a single staff operation.
    Useful for the batch undo feature and for the audit log.
    """

    label = models.CharField(
        max_length=200,
        help_text='e.g. "Grade 9A → Grade 10A, Year End 2025".',
    )
    from_academic_year = models.ForeignKey(
        'academics.AcademicYear',
        on_delete=models.PROTECT,
        related_name='promotion_batches_from',
    )
    to_academic_year = models.ForeignKey(
        'academics.AcademicYear',
        on_delete=models.PROTECT,
        related_name='promotion_batches_to',
    )
    created_by  = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='promotion_batches',
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    notes       = models.TextField(blank=True)

    # Execution tracking
    executed    = models.BooleanField(default=False)
    executed_at = models.DateTimeField(null=True, blank=True)
    total_count = models.PositiveIntegerField(default=0)
    success_count = models.PositiveIntegerField(default=0)
    failure_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Bulk Promotion Batch'
        verbose_name_plural = 'Bulk Promotion Batches'

    def __str__(self):
        return f'{self.label} ({self.created_at.strftime("%d %b %Y")})'


class LearnerPromotion(models.Model):
    """
    Records the promotion or retention decision for a single learner
    at the end of an academic year.

    Statuses:
      pending   — decision made but LearnerEnrollment not yet created
      promoted  — moved to the next grade, LearnerEnrollment exists
      retained  — staying in the same grade, LearnerEnrollment exists
      graduated — completed their final year, no new enrollment needed
      withdrawn — leaving the school, no new enrollment created

    The PromotionService (Stage 4) executes pending records, creating the
    new LearnerEnrollment and SubjectEnrollment records in academics.
    """

    STATUS_PENDING   = 'pending'
    STATUS_PROMOTED  = 'promoted'
    STATUS_RETAINED  = 'retained'
    STATUS_GRADUATED = 'graduated'
    STATUS_WITHDRAWN = 'withdrawn'
    STATUS_FAILED    = 'failed'

    STATUS_CHOICES = [
        (STATUS_PENDING,   'Pending Execution'),
        (STATUS_PROMOTED,  'Promoted'),
        (STATUS_RETAINED,  'Retained (Repeating Grade)'),
        (STATUS_GRADUATED, 'Graduated'),
        (STATUS_WITHDRAWN, 'Withdrawn'),
        (STATUS_FAILED,    'Execution Failed'),
    ]

    # ---- Core relationships ----
    learner = models.ForeignKey(
        'academics.Learner',
        on_delete=models.PROTECT,
        related_name='promotions',
    )
    batch = models.ForeignKey(
        BulkPromotionBatch,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='promotions',
    )

    # ---- From ----
    from_academic_year = models.ForeignKey(
        'academics.AcademicYear',
        on_delete=models.PROTECT,
        related_name='promotions_from',
    )
    from_grade = models.ForeignKey(
        'academics.Grade',
        on_delete=models.PROTECT,
        related_name='promotions_from',
    )
    from_stream = models.ForeignKey(
        'academics.GradeStream',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='promotions_from',
    )

    # ---- To ----
    to_academic_year = models.ForeignKey(
        'academics.AcademicYear',
        on_delete=models.PROTECT,
        related_name='promotions_to',
    )
    to_grade = models.ForeignKey(
        'academics.Grade',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='promotions_to',
        help_text='Null for graduated or withdrawn learners.',
    )
    to_stream = models.ForeignKey(
        'academics.GradeStream',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='promotions_to',
        help_text=(
            'The stream assigned for the next year. '
            'Can differ from from_stream when reorganising classes.'
        ),
    )

    # ---- Decision ----
    status     = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    decided_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='promotion_decisions',
    )
    decided_at = models.DateTimeField(auto_now_add=True)
    notes      = models.TextField(blank=True)

    # ---- Subject selection required? ----
    # True for Grade 9 → 10 transitions where electives change.
    # When True, the PromotionService creates a pending SubjectSelectionRequest.
    requires_subject_selection = models.BooleanField(
        default=False,
        help_text=(
            'If True, the learner or parent must complete subject selection '
            'before their LearnerEnrollment is finalised.'
        ),
    )
    subject_selection_completed = models.BooleanField(default=False)

    # ---- Execution result ----
    # FK to the LearnerEnrollment created for the new year.
    # Null until execution succeeds.
    new_learner_enrollment = models.OneToOneField(
        'academics.LearnerEnrollment',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='source_promotion',
    )
    executed_at    = models.DateTimeField(null=True, blank=True)
    execution_error = models.TextField(
        blank=True,
        help_text='Populated when status=failed. Cleared on retry.',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('learner', 'from_academic_year')]
        ordering = ['-decided_at', 'from_grade__grade_number', 'learner__last_name']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['from_academic_year', 'status']),
            models.Index(fields=['to_academic_year', 'status']),
        ]
        verbose_name = 'Learner Promotion'
        verbose_name_plural = 'Learner Promotions'

    def __str__(self):
        return (
            f'{self.learner.get_full_name()} — '
            f'{self.from_grade} → {self.to_grade or "Leaving"} '
            f'({self.get_status_display()})'
        )