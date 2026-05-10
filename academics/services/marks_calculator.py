"""
Marks calculation service for the Scholr academics module.

Implements CAPS assessment weighting:
  Final mark = (Year SBA% × SBA weighting%) + (Exam% × Exam weighting%)

For subjects where exam_weighting = 0 (Life Orientation, Foundation Phase),
the final mark is purely the SBA percentage × 100%.

All calculations use Decimal arithmetic throughout to avoid floating-point
rounding errors that would affect learner results.
"""

from django.db import models
from decimal import Decimal, ROUND_HALF_UP

from ..models.marks import AssessmentRecord, FinalMark, SubjectEnrollment, TermMark


class MarksCalculatorService:
    """
    Recalculates TermMark and FinalMark records from AssessmentRecord data.
    Instantiate per request - stateless beyond the methods below.
    """

    PRECISION = Decimal("0.01")

    def calculate_and_save_term_mark(
        self, enrollment: SubjectEnrollment, term_number: int
    ) -> TermMark:
        """
        Recalculate and persist the TermMark for this enrollment and term.
        Called after every mark save in the capture view.
        """
        offering = enrollment.subject_offering
        tasks = offering.get_tasks_for_term(term_number)

        sba_tasks = tasks.filter(is_sba=True, is_active=True)
        exam_tasks = tasks.filter(is_sba=False, is_active=True)

        sba_pct = self._calculate_task_percentage(enrollment, sba_tasks)
        exam_pct = self._calculate_task_percentage(enrollment, exam_tasks)

        # Count captured tasks for progress tracking
        all_task_ids = list(tasks.values_list("pk", flat=True))
        captured_count = AssessmentRecord.objects.filter(
            subject_enrollment=enrollment,
            assessment_task_id__in=all_task_ids,
        ).filter(
            models.Q(raw_mark__isnull=False) | models.Q(is_absent=True)
        ).count()

        term_mark, _ = TermMark.objects.update_or_create(
            subject_enrollment=enrollment,
            term_number=term_number,
            defaults={
                "sba_percentage": sba_pct,
                "exam_percentage": exam_pct,
                "tasks_total": tasks.count(),
                "tasks_captured": captured_count,
            },
        )
        return term_mark

    def calculate_and_save_final_mark(
        self, enrollment: SubjectEnrollment
    ) -> FinalMark:
        """
        Recalculate and persist the FinalMark for this enrollment.
        Uses all SBA tasks and all exam tasks across the full year.
        """
        offering = enrollment.subject_offering

        all_sba_tasks = offering.get_sba_tasks()
        all_exam_tasks = offering.get_exam_tasks()

        year_sba_pct = self._calculate_task_percentage(enrollment, all_sba_tasks)
        exam_pct = self._calculate_task_percentage(enrollment, all_exam_tasks)

        sba_contribution = None
        exam_contribution = None
        final_pct = None

        if year_sba_pct is not None:
            sba_contribution = (
                year_sba_pct * Decimal(str(offering.sba_weighting)) / 100
            ).quantize(self.PRECISION, rounding=ROUND_HALF_UP)

        if exam_pct is not None:
            exam_contribution = (
                exam_pct * Decimal(str(offering.exam_weighting)) / 100
            ).quantize(self.PRECISION, rounding=ROUND_HALF_UP)

        # For SBA-only subjects (LO, Foundation Phase), exam_weighting = 0
        if sba_contribution is not None and offering.exam_weighting == 0:
            final_pct = sba_contribution
        elif sba_contribution is not None and exam_contribution is not None:
            final_pct = (sba_contribution + exam_contribution).quantize(
                self.PRECISION, rounding=ROUND_HALF_UP
            )

        result = self._determine_result(final_pct, offering.pass_mark)

        final_mark, _ = FinalMark.objects.update_or_create(
            subject_enrollment=enrollment,
            defaults={
                "year_sba_percentage": year_sba_pct,
                "exam_percentage": exam_pct,
                "sba_contribution": sba_contribution,
                "exam_contribution": exam_contribution,
                "final_percentage": final_pct,
                "result": result,
            },
        )
        return final_mark

    def recalculate_all_for_enrollment(self, enrollment: SubjectEnrollment):
        """Recalculate all 4 term marks and the final mark for one enrollment."""
        for term_number in (1, 2, 3, 4):
            self.calculate_and_save_term_mark(enrollment, term_number)
        self.calculate_and_save_final_mark(enrollment)

    def _calculate_task_percentage(self, enrollment, tasks) -> Decimal | None:
        """
        Calculate the percentage mark for a set of tasks.
        Returns None if no tasks exist or no marks have been captured.
        Uses total marks proportional weighting (not equal task weighting).
        """
        task_list = list(tasks)
        if not task_list:
            return None

        records = AssessmentRecord.objects.filter(
            subject_enrollment=enrollment,
            assessment_task__in=task_list,
        )

        if not records.exists():
            return None

        total_max = sum(Decimal(str(t.max_marks)) for t in task_list)
        if total_max == 0:
            return None

        total_earned = sum(r.effective_mark for r in records)

        return (total_earned / total_max * 100).quantize(
            self.PRECISION, rounding=ROUND_HALF_UP
        )

    def _determine_result(self, percentage, pass_mark: int) -> str:
        if percentage is None:
            return FinalMark.RESULT_INCOMPLETE
        if percentage >= 80:
            return FinalMark.RESULT_DISTINCTION
        if percentage >= 70:
            return FinalMark.RESULT_MERIT
        if percentage >= pass_mark:
            return FinalMark.RESULT_PASS
        return FinalMark.RESULT_FAIL

