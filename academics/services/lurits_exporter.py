"""
LURITS CSV export service.

Generates CSV files in the DBE LURITS format.
Verify column names and codes against the current DBE specification
at lurits.dbe.gov.za before official submission — the format updates annually.

Grade codes: 00=Grade R, 01=Grade 1, ... 12=Grade 12
Gender codes: M=Male, F=Female
Language codes: As per SA language choices in Learner model
Result codes: P=Promoted, R=Retained, A=Age-progressed, W=Withdrawn
"""

import csv
import io
import zipfile
from datetime import datetime
from decimal import Decimal

from django.core.files.base import ContentFile
from django.utils import timezone

from ..models.attendance import AttendanceSummary
from ..models.learner import Learner, LearnerEnrollment
from ..models.lurits import LURITSExport
from ..models.marks import FinalMark, SubjectEnrollment
from ..models.school import AcademicYear, School, Term


# Language code mapping: home_language value → LURITS code
LANGUAGE_CODES = {
    "afrikaans": "AFK",
    "english": "ENG",
    "isindebele": "NDB",
    "isixhosa": "XHO",
    "isizulu": "ZUL",
    "sepedi": "NSO",
    "sesotho": "SOT",
    "setswana": "TSW",
    "siswati": "SWA",
    "tshivenda": "VEN",
    "xitsonga": "TSO",
    "other": "OTH",
}

NATIONALITY_CODES = {
    "ZA": "SA",
    "ZW": "ZIM",
    "MZ": "MOZ",
    "LS": "LES",
    "SZ": "SWZ",
    "BW": "BOT",
    "other": "OTH",
}


class LURITSExporterService:

    def __init__(self):
        self.school = School.get_current()

    def _emis(self):
        return self.school.emis_number if self.school else ""

    def _grade_code(self, grade_number: int) -> str:
        """LURITS grade code: 00 for Grade R, 01-12 for Grades 1-12."""
        return f"{grade_number:02d}"

    def _write_csv(self, headers: list, rows: list) -> bytes:
        """Build a CSV file in memory and return as bytes."""
        buffer = io.StringIO()
        writer = csv.writer(buffer, quoting=csv.QUOTE_ALL)
        writer.writerow(headers)
        writer.writerows(rows)
        return buffer.getvalue().encode("utf-8-sig")  # BOM for Excel compatibility

    # ==================== LEARNER DEMOGRAPHICS ====================

    def export_learners(self, academic_year: AcademicYear) -> LURITSExport:
        """Export learner personal details for all active learners."""
        headers = [
            "EMIS_NUMBER", "LEARNER_ULI", "SURNAME", "FIRST_NAME",
            "GENDER", "DATE_OF_BIRTH", "HOME_LANGUAGE",
            "NATIONALITY", "SA_ID_NUMBER", "BIRTH_CERT_NUMBER",
            "GRADE_CODE", "CLASSIFICATION",
        ]

        enrollments = LearnerEnrollment.objects.filter(
            academic_year=academic_year, is_active=True,
        ).select_related("learner", "grade").order_by(
            "grade__grade_number", "learner__last_name"
        )

        rows = []
        for enrollment in enrollments:
            learner = enrollment.learner
            rows.append([
                self._emis(),
                learner.emis_learner_number or "",
                learner.last_name.upper(),
                learner.first_name.upper(),
                learner.gender,
                learner.date_of_birth.strftime("%Y-%m-%d") if learner.date_of_birth else "",
                LANGUAGE_CODES.get(learner.home_language, "OTH"),
                NATIONALITY_CODES.get(learner.nationality, "OTH"),
                learner.sa_id_number or "",
                learner.birth_cert_number or "",
                self._grade_code(enrollment.grade.grade_number),
                learner.classification.upper(),
            ])

        csv_bytes = self._write_csv(headers, rows)
        filename = f"LURITS_Learners_{academic_year.year}_{self._emis()}.csv"

        return self._save_export(
            academic_year=academic_year,
            export_type=LURITSExport.TYPE_LEARNERS,
            csv_bytes=csv_bytes,
            filename=filename,
            record_count=len(rows),
        )

    # ==================== ENROLLMENT ====================

    def export_enrollment(self, academic_year: AcademicYear) -> LURITSExport:
        """Export grade enrollment data."""
        headers = [
            "EMIS_NUMBER", "LEARNER_ULI", "SURNAME", "FIRST_NAME",
            "GRADE_CODE", "ADMISSION_DATE", "STATUS",
            "STREAM", "YEAR",
        ]

        enrollments = LearnerEnrollment.objects.filter(
            academic_year=academic_year, is_active=True,
        ).select_related("learner", "grade", "stream").order_by(
            "grade__grade_number", "learner__last_name"
        )

        rows = []
        for enrollment in enrollments:
            learner = enrollment.learner
            rows.append([
                self._emis(),
                learner.emis_learner_number or "",
                learner.last_name.upper(),
                learner.first_name.upper(),
                self._grade_code(enrollment.grade.grade_number),
                enrollment.admission_date.strftime("%Y-%m-%d") if enrollment.admission_date else "",
                enrollment.status.upper(),
                enrollment.stream.get_display_name() if enrollment.stream else "",
                academic_year.year,
            ])

        csv_bytes = self._write_csv(headers, rows)
        filename = f"LURITS_Enrollment_{academic_year.year}_{self._emis()}.csv"

        return self._save_export(
            academic_year=academic_year,
            export_type=LURITSExport.TYPE_ENROLLMENT,
            csv_bytes=csv_bytes,
            filename=filename,
            record_count=len(rows),
        )

    # ==================== ASSESSMENT RESULTS ====================

    def export_results(self, academic_year: AcademicYear, term_number: int) -> LURITSExport:
        """Export subject marks and final results for a term."""
        headers = [
            "EMIS_NUMBER", "LEARNER_ULI", "SURNAME", "FIRST_NAME",
            "GRADE_CODE", "SUBJECT_CODE", "SUBJECT_NAME",
            "LURITS_SUBJECT_CODE", "MARK_PERCENTAGE",
            "ACHIEVEMENT_SYMBOL", "RESULT",
        ]

        sub_enrollments = SubjectEnrollment.objects.filter(
            academic_year=academic_year, is_active=True,
        ).select_related(
            "learner",
            "subject_offering__subject",
            "subject_offering__grade",
        ).order_by(
            "subject_offering__grade__grade_number",
            "learner__last_name",
            "subject_offering__subject__sort_order",
        )

        final_marks = {
            fm.subject_enrollment_id: fm
            for fm in FinalMark.objects.filter(
                subject_enrollment__academic_year=academic_year,
            )
        }

        rows = []
        for se in sub_enrollments:
            offering = se.subject_offering
            fm = final_marks.get(se.pk)

            mark_pct = fm.final_percentage if fm and fm.final_percentage is not None else ""
            symbol = ""
            result = ""

            if mark_pct != "":
                from ..utils.caps_marks import get_symbol_and_description
                sym, _ = get_symbol_and_description(mark_pct)
                symbol = sym or ""
                result = "P" if float(mark_pct) >= offering.pass_mark else "F"

            rows.append([
                self._emis(),
                se.learner.emis_learner_number or "",
                se.learner.last_name.upper(),
                se.learner.first_name.upper(),
                self._grade_code(offering.grade.grade_number),
                offering.subject.code,
                offering.subject.name.upper(),
                offering.subject.lurits_code or "",
                f"{float(mark_pct):.1f}" if mark_pct != "" else "",
                symbol,
                result,
            ])

        csv_bytes = self._write_csv(headers, rows)
        filename = f"LURITS_Results_T{term_number}_{academic_year.year}_{self._emis()}.csv"

        return self._save_export(
            academic_year=academic_year,
            term_number=term_number,
            export_type=LURITSExport.TYPE_RESULTS,
            csv_bytes=csv_bytes,
            filename=filename,
            record_count=len(rows),
        )

    # ==================== ATTENDANCE ====================

    def export_attendance(self, academic_year: AcademicYear, term_number: int) -> LURITSExport:
        """Export attendance summary per learner per term."""
        headers = [
            "EMIS_NUMBER", "LEARNER_ULI", "SURNAME", "FIRST_NAME",
            "GRADE_CODE", "TERM", "SCHOOL_DAYS",
            "DAYS_PRESENT", "DAYS_ABSENT", "DAYS_LATE",
            "DAYS_EXCUSED", "ATTENDANCE_PERCENTAGE",
        ]

        summaries = AttendanceSummary.objects.filter(
            academic_year=academic_year,
            term_number=term_number,
        ).select_related("learner", "stream__grade").order_by(
            "stream__grade__grade_number", "learner__last_name"
        )

        rows = []
        for s in summaries:
            enrollment = LearnerEnrollment.objects.filter(
                learner=s.learner, academic_year=academic_year, is_active=True
            ).select_related("grade").first()
            grade_code = self._grade_code(enrollment.grade.grade_number) if enrollment else ""

            rows.append([
                self._emis(),
                s.learner.emis_learner_number or "",
                s.learner.last_name.upper(),
                s.learner.first_name.upper(),
                grade_code,
                term_number,
                s.total_days,
                s.days_present,
                s.days_absent,
                s.days_late,
                s.days_excused,
                f"{float(s.attendance_percentage):.2f}",
            ])

        csv_bytes = self._write_csv(headers, rows)
        filename = f"LURITS_Attendance_T{term_number}_{academic_year.year}_{self._emis()}.csv"

        return self._save_export(
            academic_year=academic_year,
            term_number=term_number,
            export_type=LURITSExport.TYPE_ATTENDANCE,
            csv_bytes=csv_bytes,
            filename=filename,
            record_count=len(rows),
        )

    # ==================== FULL ZIP EXPORT ====================

    def export_full_zip(self, academic_year: AcademicYear, term_number: int) -> LURITSExport:
        """
        Bundle all four CSV exports into a single zip file.
        Generates each CSV in memory — does not save them individually.
        """
        zip_buffer = io.BytesIO()

        # I'm generating each CSV in memory without saving to disk
        # and bundling them directly into the zip
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                f"learners_{academic_year.year}.csv",
                self._learners_csv_bytes(academic_year),
            )
            zf.writestr(
                f"enrollment_{academic_year.year}.csv",
                self._enrollment_csv_bytes(academic_year),
            )
            zf.writestr(
                f"results_T{term_number}_{academic_year.year}.csv",
                self._results_csv_bytes(academic_year, term_number),
            )
            zf.writestr(
                f"attendance_T{term_number}_{academic_year.year}.csv",
                self._attendance_csv_bytes(academic_year, term_number),
            )
            # Include a README
            zf.writestr(
                "README.txt",
                self._readme_text(academic_year, term_number),
            )

        zip_bytes = zip_buffer.getvalue()
        filename = (
            f"LURITS_Full_T{term_number}_{academic_year.year}_"
            f"{self._emis()}_{timezone.now().strftime('%Y%m%d_%H%M')}.zip"
        )

        return self._save_export(
            academic_year=academic_year,
            term_number=term_number,
            export_type=LURITSExport.TYPE_FULL,
            csv_bytes=zip_bytes,
            filename=filename,
            record_count=0,
            extension="zip",
        )

    # ==================== INTERNAL CSV BUILDERS ====================

    def _learners_csv_bytes(self, academic_year):
        """Build learners CSV bytes without saving an export record."""
        export = self.export_learners(academic_year)
        export.file.open("rb")
        data = export.file.read()
        export.file.close()
        return data

    def _enrollment_csv_bytes(self, academic_year):
        export = self.export_enrollment(academic_year)
        export.file.open("rb")
        data = export.file.read()
        export.file.close()
        return data

    def _results_csv_bytes(self, academic_year, term_number):
        export = self.export_results(academic_year, term_number)
        export.file.open("rb")
        data = export.file.read()
        export.file.close()
        return data

    def _attendance_csv_bytes(self, academic_year, term_number):
        export = self.export_attendance(academic_year, term_number)
        export.file.open("rb")
        data = export.file.read()
        export.file.close()
        return data

    def _readme_text(self, academic_year, term_number):
        school = self.school
        return (
            f"LURITS Data Export\n"
            f"{'=' * 40}\n"
            f"School:       {school.name if school else 'Unknown'}\n"
            f"EMIS Number:  {self._emis()}\n"
            f"Academic Year: {academic_year.year}\n"
            f"Term:          {term_number}\n"
            f"Generated:     {timezone.now().strftime('%d %B %Y %H:%M')}\n\n"
            f"Files in this export:\n"
            f"  learners_*.csv    - Learner personal details\n"
            f"  enrollment_*.csv  - Grade enrollment data\n"
            f"  results_*.csv     - Assessment results\n"
            f"  attendance_*.csv  - Attendance summary\n\n"
            f"IMPORTANT: Verify column formats against the current\n"
            f"DBE LURITS specification before official submission.\n"
            f"Specification: https://lurits.dbe.gov.za\n"
        )

    # ==================== SAVE HELPER ====================

    def _save_export(
        self, academic_year, export_type, csv_bytes, filename,
        record_count=0, term_number=None, extension="csv"
    ) -> LURITSExport:
        export = LURITSExport.objects.create(
            academic_year=academic_year,
            term_number=term_number,
            export_type=export_type,
            status=LURITSExport.STATUS_PENDING,
            total_records=record_count,
            filename=filename,
        )
        export.file.save(filename, ContentFile(csv_bytes), save=False)
        export.status = LURITSExport.STATUS_READY
        export.completed_at = timezone.now()
        export.save(update_fields=["file", "status", "completed_at"])
        return export