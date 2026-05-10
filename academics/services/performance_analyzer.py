"""
Performance analysis service for the Scholr academics module.

Analyses each learner's marks against their subject pass marks,
identifies risk factors, computes performance trends across terms,
and persists a PerformanceAnalysis snapshot for dashboard access.

Risk scoring:
  FAILING_SUBJECT (per subject)   +2
  FAILING_MULTIPLE (3+ subjects)  +3
  DOWNWARD_TREND                  +2
  BELOW_PASS_CURRENT_TERM         +1 per subject

Risk level thresholds:
  0       = low
  1-3     = medium
  4-6     = high
  7+      = critical
"""

from decimal import Decimal, ROUND_HALF_UP

from ..models.intervention import PerformanceAnalysis
from ..models.marks import FinalMark, SubjectEnrollment, TermMark
from ..models.school import AcademicYear, Term


class PerformanceAnalyzerService:
    """
    Analyses one learner at a time. Instantiate once and call analyze_learner
    for each learner. The result is persisted as a PerformanceAnalysis record
    and also returned for immediate use in the calling view.
    """

    RISK_THRESHOLDS = {
        PerformanceAnalysis.RISK_LOW: 0,
        PerformanceAnalysis.RISK_MEDIUM: 1,
        PerformanceAnalysis.RISK_HIGH: 4,
        PerformanceAnalysis.RISK_CRITICAL: 7,
    }

    def analyze_learner(self, learner, academic_year, current_term=None, run_by=None):
        """
        Run a full risk analysis for one learner.

        Returns:
            (PerformanceAnalysis, list[subject_result_dict])
            subject_result_dict keys: enrollment, offering, current_percentage,
                                       term_marks, final_mark, status
        """
        if current_term is None:
            current_term = Term.get_current()

        enrollments = list(
            SubjectEnrollment.objects.filter(
                learner=learner,
                academic_year=academic_year,
                is_active=True,
            ).select_related(
                "subject_offering__subject",
                "subject_offering",
            )
        )

        if not enrollments:
            # I'm still creating an analysis record for learners with no enrollments
            # so they show up in the dashboard as "incomplete"
            analysis, _ = PerformanceAnalysis.objects.update_or_create(
                learner=learner,
                academic_year=academic_year,
                defaults={
                    "risk_level": PerformanceAnalysis.RISK_LOW,
                    "risk_score": 0,
                    "risk_flags": [{"type": "NO_MARKS_CAPTURED"}],
                    "subjects_total": 0,
                    "subjects_passing": 0,
                    "subjects_failing": 0,
                    "subjects_incomplete": 0,
                    "average_percentage": None,
                    "trend": PerformanceAnalysis.TREND_UNKNOWN,
                    "analysed_by": run_by,
                },
            )
            return analysis, []

        # Pre-fetch term marks and final marks in two queries
        all_term_marks = TermMark.objects.filter(
            subject_enrollment__in=enrollments
        )
        term_mark_map = {}
        for tm in all_term_marks:
            term_mark_map.setdefault(tm.subject_enrollment_id, {})[tm.term_number] = tm

        final_mark_map = {}
        for fm in FinalMark.objects.filter(subject_enrollment__in=enrollments):
            final_mark_map[fm.subject_enrollment_id] = fm

        flags = []
        risk_score = 0
        subject_results = []
        term_subject_averages = {}  # {term_number: [percentages]}

        for enrollment in enrollments:
            offering = enrollment.subject_offering
            tm_by_term = term_mark_map.get(enrollment.pk, {})
            final = final_mark_map.get(enrollment.pk)

            # Determine the best current percentage to use
            current_pct = None
            if final and final.final_percentage is not None:
                current_pct = final.final_percentage
            elif current_term and current_term.term_number in tm_by_term:
                tm = tm_by_term[current_term.term_number]
                current_pct = tm.sba_percentage or tm.exam_percentage

            # Collect per-term percentages for trend analysis
            for term_num, tm in tm_by_term.items():
                pct = tm.sba_percentage
                if pct is not None:
                    term_subject_averages.setdefault(term_num, []).append(float(pct))

            status = "incomplete"
            if current_pct is not None:
                if current_pct < offering.pass_mark:
                    status = "failing"
                    flags.append({
                        "type": "FAILING_SUBJECT",
                        "subject": offering.subject.name,
                        "subject_code": offering.subject.code,
                        "percentage": float(current_pct),
                        "pass_mark": offering.pass_mark,
                    })
                    risk_score += 2
                else:
                    status = "passing"

            # Also flag below pass in the current term specifically
            # (even if the overall final mark is passing)
            if (
                current_term
                and current_term.term_number in tm_by_term
                and status != "failing"
            ):
                tm = tm_by_term[current_term.term_number]
                pct = tm.sba_percentage
                if pct is not None and pct < offering.pass_mark:
                    flags.append({
                        "type": "BELOW_PASS_CURRENT_TERM",
                        "subject": offering.subject.name,
                        "term": current_term.term_number,
                        "percentage": float(pct),
                    })
                    risk_score += 1

            subject_results.append({
                "enrollment": enrollment,
                "offering": offering,
                "current_percentage": current_pct,
                "term_marks": tm_by_term,
                "final_mark": final,
                "status": status,
            })

        # Failing multiple subjects is a compounded risk
        failing_count = sum(1 for sr in subject_results if sr["status"] == "failing")
        if failing_count >= 3:
            flags.append({"type": "FAILING_MULTIPLE", "count": failing_count})
            risk_score += 3

        # Trend: compare average performance across the last two terms
        trend = self._calculate_trend(term_subject_averages, flags, risk_score)
        risk_score = trend["risk_score"]
        if trend.get("flag"):
            flags.append(trend["flag"])
        trend_label = trend["trend"]

        # Overall average across all subjects for the current snapshot
        all_pcts = [
            sr["current_percentage"]
            for sr in subject_results
            if sr["current_percentage"] is not None
        ]
        average_pct = None
        if all_pcts:
            average_pct = Decimal(
                str(sum(all_pcts) / len(all_pcts))
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        risk_level = self._determine_risk_level(risk_score)

        analysis, _ = PerformanceAnalysis.objects.update_or_create(
            learner=learner,
            academic_year=academic_year,
            defaults={
                "risk_level": risk_level,
                "risk_score": risk_score,
                "risk_flags": flags,
                "subjects_total": len(subject_results),
                "subjects_passing": sum(1 for sr in subject_results if sr["status"] == "passing"),
                "subjects_failing": failing_count,
                "subjects_incomplete": sum(1 for sr in subject_results if sr["status"] == "incomplete"),
                "average_percentage": average_pct,
                "trend": trend_label,
                "analysed_by": run_by,
            },
        )

        return analysis, subject_results

    def analyze_all_for_year(self, academic_year, run_by=None):
        """
        Analyse every active learner in the academic year.
        Returns the count of analyses run.
        """
        from ..models.learner import Learner, LearnerEnrollment

        current_term = Term.get_current()
        learner_ids = LearnerEnrollment.objects.filter(
            academic_year=academic_year,
            is_active=True,
        ).values_list("learner_id", flat=True).distinct()

        learners = Learner.objects.filter(
            pk__in=learner_ids, is_active=True
        )

        count = 0
        for learner in learners:
            self.analyze_learner(learner, academic_year, current_term, run_by)
            count += 1
        return count

    def _calculate_trend(self, term_subject_averages, flags, current_score):
        """
        Calculate the performance trend from term-by-term data.
        Requires at least 2 terms of data to determine a trend.
        """
        if len(term_subject_averages) < 2:
            return {
                "trend": PerformanceAnalysis.TREND_UNKNOWN,
                "risk_score": current_score,
                "flag": None,
            }

        sorted_terms = sorted(term_subject_averages.keys())
        term_avgs = [
            sum(term_subject_averages[t]) / len(term_subject_averages[t])
            for t in sorted_terms
        ]

        last_change = term_avgs[-1] - term_avgs[-2]

        if last_change > 5:
            return {
                "trend": PerformanceAnalysis.TREND_IMPROVING,
                "risk_score": current_score,
                "flag": None,
            }
        elif last_change < -5:
            return {
                "trend": PerformanceAnalysis.TREND_DECLINING,
                "risk_score": current_score + 2,
                "flag": {
                    "type": "DOWNWARD_TREND",
                    "change": round(last_change, 1),
                    "from_term": sorted_terms[-2],
                    "to_term": sorted_terms[-1],
                },
            }
        else:
            return {
                "trend": PerformanceAnalysis.TREND_STABLE,
                "risk_score": current_score,
                "flag": None,
            }

    def _determine_risk_level(self, risk_score: int) -> str:
        if risk_score >= 7:
            return PerformanceAnalysis.RISK_CRITICAL
        if risk_score >= 4:
            return PerformanceAnalysis.RISK_HIGH
        if risk_score >= 1:
            return PerformanceAnalysis.RISK_MEDIUM
        return PerformanceAnalysis.RISK_LOW