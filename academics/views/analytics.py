"""
Analytics and reporting views for Scholr academics.

All views are Senior Management only (principal + admin).
Data is aggregated fresh on each request — volumes for a single school
are small enough that caching is not required at this stage.

Chart.js data is serialised as JSON and injected into templates.
Excel exports use openpyxl.
"""

import io
import json
from collections import defaultdict
from decimal import Decimal
from scholr.features import bridge_enabled as _bridge_enabled
from django.db.models import Avg, Count, Q
from django.http import HttpResponse
from django.shortcuts import render
from django.views import View

from ..mixins import SeniorManagementMixin
from ..models.attendance import AttendanceSummary
from ..models.intervention import LearnerIntervention, PerformanceAnalysis
from ..models.learner import Learner, LearnerEnrollment
from ..models.marks import FinalMark, SubjectEnrollment
from ..models.reports import ReportCard
from ..models.school import AcademicYear, Term
from ..models.structure import Grade


# ==================== COLOUR PALETTE ====================
# Consistent Chart.js colours across all charts

CHART_COLOURS = {
    "blue":   ("rgba(59, 130, 246, 0.85)",  "rgba(59, 130, 246, 1)"),
    "green":  ("rgba(34, 197, 94, 0.85)",   "rgba(34, 197, 94, 1)"),
    "red":    ("rgba(239, 68, 68, 0.85)",   "rgba(239, 68, 68, 1)"),
    "amber":  ("rgba(245, 158, 11, 0.85)",  "rgba(245, 158, 11, 1)"),
    "purple": ("rgba(168, 85, 247, 0.85)",  "rgba(168, 85, 247, 1)"),
    "indigo": ("rgba(99, 102, 241, 0.85)",  "rgba(99, 102, 241, 1)"),
    "teal":   ("rgba(20, 184, 166, 0.85)",  "rgba(20, 184, 166, 1)"),
    "gray":   ("rgba(107, 114, 128, 0.85)", "rgba(107, 114, 128, 1)"),
}

BG_COLOURS = [v[0] for v in CHART_COLOURS.values()]
BORDER_COLOURS = [v[1] for v in CHART_COLOURS.values()]


# ==================== DATA HELPERS ====================

def _get_enrollment_by_grade(academic_year):
    """Return [{grade_name, grade_number, count, male, female}]."""
    qs = LearnerEnrollment.objects.filter(
        academic_year=academic_year, is_active=True
    ).values(
        "grade__name", "grade__grade_number",
        "learner__gender",
    ).annotate(count=Count("id")).order_by("grade__grade_number")

    grade_data = {}
    for row in qs:
        gnum = row["grade__grade_number"]
        if gnum not in grade_data:
            grade_data[gnum] = {
                "grade_name": row["grade__name"],
                "grade_number": gnum,
                "count": 0, "male": 0, "female": 0,
            }
        grade_data[gnum]["count"] += row["count"]
        if row["learner__gender"] == "M":
            grade_data[gnum]["male"] += row["count"]
        elif row["learner__gender"] == "F":
            grade_data[gnum]["female"] += row["count"]

    return sorted(grade_data.values(), key=lambda x: x["grade_number"])


def _get_subject_stats(academic_year):
    """
    Return per-subject stats: total learners, passed, pass rate, average.
    Uses all final marks regardless of is_published — this is an admin view.
    """
    final_marks = FinalMark.objects.filter(
        subject_enrollment__academic_year=academic_year,
        final_percentage__isnull=False,
    ).select_related(
        "subject_enrollment__subject_offering__subject",
        "subject_enrollment__subject_offering__grade",
    )

    # Group by (subject name, grade name)
    groups = defaultdict(list)
    for fm in final_marks:
        offering = fm.subject_enrollment.subject_offering
        key = (offering.subject.name, offering.subject.code, offering.grade.name, offering.pass_mark)
        groups[key].append(fm)

    stats = []
    for (subject_name, code, grade_name, pass_mark), marks in groups.items():
        total = len(marks)
        passed = sum(1 for m in marks if float(m.final_percentage) >= pass_mark)
        avg = sum(float(m.final_percentage) for m in marks) / total if total else 0

        stats.append({
            "subject_name": subject_name,
            "subject_code": code,
            "grade_name": grade_name,
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / total * 100, 1) if total else None,
            "average": round(avg, 1),
        })

    return sorted(stats, key=lambda x: (x["subject_code"], x["grade_name"]))


def _get_attendance_by_grade(academic_year, term_number):
    """Return [{grade_name, avg_pct, at_risk_count, total}]."""
    qs = AttendanceSummary.objects.filter(
        academic_year=academic_year,
        term_number=term_number,
    ).select_related("stream__grade")

    grade_groups = defaultdict(list)
    for summary in qs:
        grade_groups[summary.stream.grade].append(summary)

    result = []
    for grade in sorted(grade_groups.keys(), key=lambda g: g.grade_number):
        summaries = grade_groups[grade]
        avg_pct = sum(float(s.attendance_percentage) for s in summaries) / len(summaries)
        at_risk = sum(1 for s in summaries if s.is_at_risk)
        result.append({
            "grade_name": grade.name,
            "grade_number": grade.grade_number,
            "average_pct": round(avg_pct, 1),
            "at_risk_count": at_risk,
            "total": len(summaries),
        })
    return result


def _get_gender_split(academic_year):
    qs = LearnerEnrollment.objects.filter(
        academic_year=academic_year, is_active=True,
    ).values("learner__gender").annotate(count=Count("id"))

    return {row["learner__gender"]: row["count"] for row in qs}


def _get_risk_distribution(academic_year):
    qs = PerformanceAnalysis.objects.filter(
        academic_year=academic_year,
    ).values("risk_level").annotate(count=Count("id"))
    return {row["risk_level"]: row["count"] for row in qs}


# ==================== DASHBOARD VIEW ====================

class AnalyticsDashboardView(SeniorManagementMixin, View):
    """
    School-wide analytics overview.
    Computes all data in one request and passes Chart.js JSON to the template.
    When the LMS Bridge is enabled, a fifth summary card shows online learning stats.
    """

    template_name = "academics/analytics/dashboard.html"

    def get(self, request):
        current_year = AcademicYear.get_current()
        current_term = Term.get_current()
        term_num = current_term.term_number if current_term else 1

        if not current_year:
            return render(request, self.template_name, {"no_year": True})

        # ---- Summary cards ----
        total_learners = LearnerEnrollment.objects.filter(
            academic_year=current_year, is_active=True
        ).count()

        final_marks_qs = FinalMark.objects.filter(
            subject_enrollment__academic_year=current_year,
            final_percentage__isnull=False,
        )
        total_finals = final_marks_qs.count()
        passed_finals = final_marks_qs.filter(
            result__in=["pass", "merit", "distinction"]
        ).count()
        overall_pass_rate = (
            round(passed_finals / total_finals * 100, 1) if total_finals else None
        )

        avg_attendance = AttendanceSummary.objects.filter(
            academic_year=current_year, term_number=term_num
        ).aggregate(avg=Avg("attendance_percentage"))["avg"]
        avg_attendance = round(float(avg_attendance), 1) if avg_attendance else None

        at_risk_academic = PerformanceAnalysis.objects.filter(
            academic_year=current_year,
            risk_level__in=["high", "critical"],
        ).count()

        active_interventions = LearnerIntervention.objects.filter(
            academic_year=current_year,
            status=LearnerIntervention.STATUS_ACTIVE,
        ).count()

        # ---- Online Learning stats card (bridge only) ----
        # Computed separately so the template can conditionally render the card.
        online_stats = None
        if _bridge_enabled():
            from ..models.lms_link import OnlineEnrollment, LearnerLMSAccount

            _oe_qs = OnlineEnrollment.objects.filter(academic_year=current_year)
            _active_oe = _oe_qs.filter(status=OnlineEnrollment.STATUS_ACTIVE)
            _avg_prog = _active_oe.aggregate(avg=Avg("progress_percentage"))["avg"]

            online_stats = {
                "total_accounts": LearnerLMSAccount.objects.filter(
                    is_active=True
                ).count(),
                "total_enrolled": _active_oe.count(),
                "avg_progress": round(float(_avg_prog), 1) if _avg_prog else 0,
                "completed": _oe_qs.filter(
                    status=OnlineEnrollment.STATUS_COMPLETED
                ).count(),
            }

        # ---- Chart data ----
        grade_enroll   = _get_enrollment_by_grade(current_year)
        gender_split   = _get_gender_split(current_year)
        subject_stats  = _get_subject_stats(current_year)
        attendance_data = _get_attendance_by_grade(current_year, term_num)
        risk_dist      = _get_risk_distribution(current_year)

        # Enrollment bar chart
        enrollment_chart = json.dumps({
            "labels": [g["grade_name"] for g in grade_enroll],
            "datasets": [{
                "label": "Learners",
                "data": [g["count"] for g in grade_enroll],
                "backgroundColor": CHART_COLOURS["blue"][0],
                "borderColor": CHART_COLOURS["blue"][1],
                "borderWidth": 1,
            }],
        })

        # Gender doughnut
        gender_chart = json.dumps({
            "labels": ["Male", "Female"],
            "datasets": [{
                "data": [
                    gender_split.get("M", 0),
                    gender_split.get("F", 0),
                ],
                "backgroundColor": [
                    CHART_COLOURS["blue"][0],
                    CHART_COLOURS["purple"][0],
                ],
                "borderColor": [
                    CHART_COLOURS["blue"][1],
                    CHART_COLOURS["purple"][1],
                ],
            }],
        })

        # Subject pass rates horizontal bar (top 12 by enrollment)
        top_subjects = sorted(
            [s for s in subject_stats if s["pass_rate"] is not None],
            key=lambda x: x["total"],
            reverse=True,
        )[:12]

        subject_chart = json.dumps({
            "labels": [
                f"{s['subject_code']} ({s['grade_name']})"
                for s in top_subjects
            ],
            "datasets": [{
                "label": "Pass Rate %",
                "data": [s["pass_rate"] for s in top_subjects],
                "backgroundColor": [
                    CHART_COLOURS["green"][0] if (s["pass_rate"] or 0) >= 60
                    else CHART_COLOURS["amber"][0] if (s["pass_rate"] or 0) >= 40
                    else CHART_COLOURS["red"][0]
                    for s in top_subjects
                ],
                "borderWidth": 1,
            }],
        })

        # Attendance bar chart
        attendance_chart = json.dumps({
            "labels": [g["grade_name"] for g in attendance_data],
            "datasets": [{
                "label": "Attendance %",
                "data": [g["average_pct"] for g in attendance_data],
                "backgroundColor": [
                    CHART_COLOURS["green"][0] if g["average_pct"] >= 80
                    else CHART_COLOURS["red"][0]
                    for g in attendance_data
                ],
                "borderWidth": 1,
            }],
        })

        # Risk distribution doughnut
        risk_levels = ["low", "medium", "high", "critical"]
        risk_chart = json.dumps({
            "labels": ["Low Risk", "Medium Risk", "High Risk", "Critical"],
            "datasets": [{
                "data": [risk_dist.get(lvl, 0) for lvl in risk_levels],
                "backgroundColor": [
                    CHART_COLOURS["green"][0],
                    CHART_COLOURS["amber"][0],
                    CHART_COLOURS["red"][0],
                    "rgba(127, 29, 29, 0.85)",
                ],
            }],
        })

        return render(request, self.template_name, {
            "current_year":        current_year,
            "current_term":        current_term,
            # Summary cards
            "total_learners":      total_learners,
            "overall_pass_rate":   overall_pass_rate,
            "avg_attendance":      avg_attendance,
            "at_risk_academic":    at_risk_academic,
            "active_interventions": active_interventions,
            "total_finals":        total_finals,
            # Online learning
            "online_stats":        online_stats,
            "bridge_enabled":      _bridge_enabled(),
            # Chart JSON
            "enrollment_chart":    enrollment_chart,
            "gender_chart":        gender_chart,
            "subject_chart":       subject_chart,
            "attendance_chart":    attendance_chart,
            "risk_chart":          risk_chart,
            # Table data
            "grade_enrollment":    grade_enroll,
        })


# ==================== ACADEMIC REPORT ====================

class AcademicReportView(SeniorManagementMixin, View):
    """Detailed academic performance breakdown by subject and grade."""

    template_name = "academics/analytics/academic_report.html"

    def get(self, request):
        current_year = AcademicYear.get_current()
        if not current_year:
            return render(request, self.template_name, {"no_year": True})

        subject_stats = _get_subject_stats(current_year)

        # Grade-level summary
        grade_final_marks = FinalMark.objects.filter(
            subject_enrollment__academic_year=current_year,
            final_percentage__isnull=False,
        ).select_related(
            "subject_enrollment__subject_offering__grade"
        )

        grade_groups = defaultdict(list)
        for fm in grade_final_marks:
            grade = fm.subject_enrollment.subject_offering.grade
            grade_groups[grade].append(fm)

        grade_stats = []
        for grade in sorted(grade_groups.keys(), key=lambda g: g.grade_number):
            marks = grade_groups[grade]
            total = len(marks)
            passed = sum(
                1 for m in marks
                if m.result in ("pass", "merit", "distinction")
            )
            distinctions = sum(1 for m in marks if m.result == "distinction")
            failing = sum(1 for m in marks if m.result == "fail")
            avg = sum(float(m.final_percentage) for m in marks) / total if total else 0

            grade_stats.append({
                "grade_name": grade.name,
                "grade_number": grade.grade_number,
                "total_marks": total,
                "passed": passed,
                "failed": failing,
                "distinctions": distinctions,
                "pass_rate": round(passed / total * 100, 1) if total else None,
                "average": round(avg, 1),
            })

        # Distinction achievers
        distinctions_qs = FinalMark.objects.filter(
            subject_enrollment__academic_year=current_year,
            result="distinction",
        ).select_related(
            "subject_enrollment__learner",
            "subject_enrollment__subject_offering__subject",
            "subject_enrollment__subject_offering__grade",
        ).order_by("-final_percentage")[:20]

        # Learners failing 3+ subjects
        learner_fail_counts = FinalMark.objects.filter(
            subject_enrollment__academic_year=current_year,
            result="fail",
        ).values(
            "subject_enrollment__learner",
            "subject_enrollment__learner__first_name",
            "subject_enrollment__learner__last_name",
            "subject_enrollment__learner__admission_number",
        ).annotate(fail_count=Count("id")).filter(fail_count__gte=3).order_by("-fail_count")

        return render(request, self.template_name, {
            "current_year": current_year,
            "subject_stats": subject_stats,
            "grade_stats": grade_stats,
            "distinctions": distinctions_qs,
            "multi_fail_learners": learner_fail_counts,
        })


# ==================== ENROLLMENT REPORT ====================

class EnrollmentReportView(SeniorManagementMixin, View):
    """Enrollment demographic breakdown."""

    template_name = "academics/analytics/enrollment_report.html"

    def get(self, request):
        current_year = AcademicYear.get_current()
        if not current_year:
            return render(request, self.template_name, {"no_year": True})

        enrollments = LearnerEnrollment.objects.filter(
            academic_year=current_year, is_active=True
        ).select_related("learner", "grade", "stream")

        total = enrollments.count()

        # Grade breakdown
        grade_data = _get_enrollment_by_grade(current_year)

        # Gender
        gender_counts = enrollments.values(
            "learner__gender"
        ).annotate(count=Count("id"))
        gender_map = {r["learner__gender"]: r["count"] for r in gender_counts}

        # Nationality
        nationality_counts = enrollments.values(
            "learner__nationality"
        ).annotate(count=Count("id")).order_by("-count")

        # Home language
        language_counts = enrollments.values(
            "learner__home_language"
        ).annotate(count=Count("id")).order_by("-count")

        # Classification
        classification_counts = enrollments.values(
            "learner__classification"
        ).annotate(count=Count("id"))

        # Transfer stats for current year
        from ..models.learner import LearnerTransfer
        transfers_in = LearnerTransfer.objects.filter(
            enrollment__academic_year=current_year,
            direction=LearnerTransfer.DIRECTION_IN,
        ).count()
        transfers_out = LearnerTransfer.objects.filter(
            enrollment__academic_year=current_year,
            direction=LearnerTransfer.DIRECTION_OUT,
        ).count()

        # Streams without a homeroom teacher
        from ..models.structure import GradeStream
        unassigned_streams = GradeStream.objects.filter(
            academic_year=current_year,
            is_active=True,
            homeroom_teacher__isnull=True,
        ).select_related("grade").count()

        return render(request, self.template_name, {
            "current_year": current_year,
            "total": total,
            "grade_data": grade_data,
            "gender_map": gender_map,
            "male": gender_map.get("M", 0),
            "female": gender_map.get("F", 0),
            "nationality_counts": nationality_counts,
            "language_counts": language_counts,
            "classification_counts": classification_counts,
            "transfers_in": transfers_in,
            "transfers_out": transfers_out,
            "unassigned_streams": unassigned_streams,
        })



# ==================== EXCEL EXPORT ====================

class ExportAnalyticsExcelView(SeniorManagementMixin, View):
    """
    Export all analytics as a styled Excel workbook.
    Sheets: Summary, Academic Performance, Attendance, Enrollment.
    When the LMS Bridge is enabled, a fifth sheet for Online Learning is appended.
    """

    def get(self, request):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
            from openpyxl.utils import get_column_letter
        except ImportError:
            return HttpResponse(
                "openpyxl is required for Excel export. "
                "Install it with: pip install openpyxl",
                status=500,
            )

        current_year = AcademicYear.get_current()
        current_term = Term.get_current()
        term_num = current_term.term_number if current_term else 1

        if not current_year:
            return HttpResponse("No current academic year set.", status=400)

        wb = Workbook()

        # ---- Style helpers (defined once, used across all sheets) ----

        def header_fill():
            return PatternFill("solid", fgColor="1E3A8A")

        def subheader_fill():
            return PatternFill("solid", fgColor="DBEAFE")

        def header_font():
            return Font(bold=True, color="FFFFFF", size=11)

        def subheader_font():
            return Font(bold=True, color="1E3A8A", size=10)

        def thin_border():
            side = Side(style="thin", color="D1D5DB")
            return Border(left=side, right=side, top=side, bottom=side)

        def write_header(ws, row, cols, title):
            ws.merge_cells(
                start_row=row, start_column=1,
                end_row=row, end_column=len(cols),
            )
            cell = ws.cell(row=row, column=1, value=title)
            cell.font = header_font()
            cell.fill = header_fill()
            cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.row_dimensions[row].height = 22

        def write_subheader(ws, row, cols):
            for col_idx, col_name in enumerate(cols, 1):
                cell = ws.cell(row=row, column=col_idx, value=col_name)
                cell.font = subheader_font()
                cell.fill = subheader_fill()
                cell.alignment = Alignment(horizontal="center")
                cell.border = thin_border()

        def write_row(ws, row, values, alt=False):
            fill = PatternFill("solid", fgColor="F9FAFB") if alt else None
            for col_idx, val in enumerate(values, 1):
                cell = ws.cell(row=row, column=col_idx, value=val)
                cell.border = thin_border()
                cell.alignment = Alignment(horizontal="center")
                if fill:
                    cell.fill = fill

        def auto_width(ws):
            for col in ws.columns:
                max_len = max(
                    (len(str(cell.value)) for cell in col if cell.value),
                    default=10,
                )
                ws.column_dimensions[
                    get_column_letter(col[0].column)
                ].width = max_len + 4

        # ==================== SHEET 1: SUMMARY ====================
        ws1 = wb.active
        ws1.title = "Summary"
        ws1.row_dimensions[1].height = 30

        from ..models.school import School
        school = School.get_current()
        school_name = school.name if school else "School"

        ws1.cell(1, 1).value = f"{school_name} — Analytics Report {current_year.year}"
        ws1.cell(1, 1).font = Font(bold=True, size=14)
        ws1.merge_cells("A1:D1")

        ws1.cell(3, 1).value = "Metric"
        ws1.cell(3, 2).value = "Value"
        ws1.cell(3, 1).font = subheader_font()
        ws1.cell(3, 2).font = subheader_font()

        total_learners = LearnerEnrollment.objects.filter(
            academic_year=current_year, is_active=True
        ).count()
        total_finals = FinalMark.objects.filter(
            subject_enrollment__academic_year=current_year,
            final_percentage__isnull=False,
        ).count()
        passed_finals = FinalMark.objects.filter(
            subject_enrollment__academic_year=current_year,
            result__in=["pass", "merit", "distinction"],
        ).count()
        avg_att = AttendanceSummary.objects.filter(
            academic_year=current_year, term_number=term_num
        ).aggregate(avg=Avg("attendance_percentage"))["avg"]

        summary_rows = [
            ("Academic Year",               current_year.year),
            ("Current Term",                f"Term {term_num}"),
            ("Total Enrolled Learners",     total_learners),
            ("Total Subject Marks Captured", total_finals),
            (
                "Overall Pass Rate",
                f"{round(passed_finals / total_finals * 100, 1)}%"
                if total_finals else "N/A",
            ),
            (
                "Average Attendance (current term)",
                f"{round(float(avg_att), 1)}%" if avg_att else "N/A",
            ),
            (
                "At-Risk (High/Critical)",
                PerformanceAnalysis.objects.filter(
                    academic_year=current_year,
                    risk_level__in=["high", "critical"],
                ).count(),
            ),
            (
                "Active Interventions",
                LearnerIntervention.objects.filter(
                    academic_year=current_year,
                    status="active",
                ).count(),
            ),
        ]
        for i, (metric, value) in enumerate(summary_rows, start=4):
            ws1.cell(i, 1).value = metric
            ws1.cell(i, 2).value = value
            ws1.cell(i, 1).font = Font(bold=True)

        ws1.column_dimensions["A"].width = 40
        ws1.column_dimensions["B"].width = 20

        # ==================== SHEET 2: ACADEMIC PERFORMANCE ====================
        ws2 = wb.create_sheet("Academic Performance")
        cols2 = [
            "Subject", "Code", "Grade",
            "Learners", "Passed", "Failed", "Pass Rate %", "Average %",
        ]
        write_header(
            ws2, 1, cols2,
            f"Academic Performance by Subject — {current_year.year}",
        )
        write_subheader(ws2, 2, cols2)

        for i, stat in enumerate(_get_subject_stats(current_year), start=3):
            write_row(ws2, i, [
                stat["subject_name"],
                stat["subject_code"],
                stat["grade_name"],
                stat["total"],
                stat["passed"],
                stat["failed"],
                stat["pass_rate"],
                stat["average"],
            ], alt=(i % 2 == 0))

        auto_width(ws2)

        # ==================== SHEET 3: ATTENDANCE ====================
        ws3 = wb.create_sheet("Attendance")
        cols3 = ["Grade", "Learners Tracked", "Average Attendance %", "Below 80% (At-Risk)"]
        write_header(
            ws3, 1, cols3,
            f"Attendance by Grade — {current_year.year} Term {term_num}",
        )
        write_subheader(ws3, 2, cols3)

        att_data = _get_attendance_by_grade(current_year, term_num)
        for i, row in enumerate(att_data, start=3):
            write_row(ws3, i, [
                row["grade_name"],
                row["total"],
                row["average_pct"],
                row["at_risk_count"],
            ], alt=(i % 2 == 0))

        # Learners below 80% detail block
        detail_start = len(att_data) + 4
        ws3.cell(detail_start, 1).value = "Learners Below 80% Attendance"
        ws3.cell(detail_start, 1).font = Font(bold=True, color="991B1B")

        at_risk_cols = [
            "Surname", "First Name", "Admission No.",
            "Grade", "Attendance %", "Days Absent",
        ]
        write_subheader(ws3, detail_start + 1, at_risk_cols)

        at_risk_qs = AttendanceSummary.objects.filter(
            academic_year=current_year,
            term_number=term_num,
            is_at_risk=True,
        ).select_related("learner", "stream__grade").order_by("attendance_percentage")

        for i, s in enumerate(at_risk_qs, start=detail_start + 2):
            write_row(ws3, i, [
                s.learner.last_name,
                s.learner.first_name,
                s.learner.admission_number,
                s.stream.grade.name,
                float(s.attendance_percentage),
                s.days_absent,
            ], alt=(i % 2 == 0))

        auto_width(ws3)

        # ==================== SHEET 4: ENROLLMENT ====================
        ws4 = wb.create_sheet("Enrollment")
        cols4 = ["Grade", "Total", "Male", "Female"]
        write_header(ws4, 1, cols4, f"Enrollment by Grade — {current_year.year}")
        write_subheader(ws4, 2, cols4)

        for i, g in enumerate(_get_enrollment_by_grade(current_year), start=3):
            write_row(ws4, i, [
                g["grade_name"], g["count"], g["male"], g["female"],
            ], alt=(i % 2 == 0))

        auto_width(ws4)

        # ==================== SHEET 5: ONLINE LEARNING (bridge only) ====================
        if _bridge_enabled():
            from ..models.lms_link import OnlineEnrollment, LearnerLMSAccount

            ws5 = wb.create_sheet("Online Learning")
            ws5.row_dimensions[1].height = 28

            ws5.cell(1, 1).value = f"Online Learning — {current_year.year}"
            ws5.cell(1, 1).font = Font(bold=True, size=14)
            ws5.merge_cells("A1:H1")

            # Summary block
            _oe_all = OnlineEnrollment.objects.filter(academic_year=current_year)
            _active_oe = _oe_all.filter(status=OnlineEnrollment.STATUS_ACTIVE)
            _avg_prog = _active_oe.aggregate(avg=Avg("progress_percentage"))["avg"]

            oe_summary = [
                ("LMS Accounts (active)", LearnerLMSAccount.objects.filter(is_active=True).count()),
                ("Active Enrollments",    _active_oe.count()),
                ("Completed Enrollments", _oe_all.filter(status=OnlineEnrollment.STATUS_COMPLETED).count()),
                (
                    "Average Progress (active)",
                    f"{round(float(_avg_prog), 1)}%" if _avg_prog else "N/A",
                ),
            ]
            for i, (metric, val) in enumerate(oe_summary, start=3):
                ws5.cell(i, 1).value = metric
                ws5.cell(i, 2).value = val
                ws5.cell(i, 1).font = Font(bold=True)

            # Enrollment detail
            detail_row = len(oe_summary) + 5
            cols5 = [
                "Learner", "Admission No.", "Course", "Category",
                "Subject Linked", "Progress %", "Status", "Enrolled On",
            ]
            write_header(ws5, detail_row, cols5, "Enrollment Detail")
            write_subheader(ws5, detail_row + 1, cols5)

            enrollments_export = _oe_all.select_related(
                'learner', 'course__category', 'subject',
            ).order_by('learner__last_name', 'course__title')

            for i, oe in enumerate(enrollments_export, start=detail_row + 2):
                enrolled_date = (
                    oe.enrolled_at.strftime('%Y-%m-%d')
                    if getattr(oe, 'enrolled_at', None)
                    else ''
                )
                write_row(ws5, i, [
                    oe.learner.get_full_name(),
                    oe.learner.admission_number,
                    oe.course.title,
                    oe.course.category.name if oe.course.category else '',
                    oe.subject.name if oe.subject else '',
                    float(oe.progress_percentage) if oe.progress_percentage is not None else 0,
                    oe.get_status_display(),
                    enrolled_date,
                ], alt=(i % 2 == 0))

            auto_width(ws5)

        # ==================== SERVE FILE ====================
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        filename = f"Scholr_Analytics_{current_year.year}_T{term_num}.xlsx"
        response = HttpResponse(
            buffer.read(),
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response