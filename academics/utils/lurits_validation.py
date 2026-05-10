"""
LURITS data validation rules.

Produces LURITSValidationIssue records for each problem found.
Errors block export. Warnings allow export with acknowledgement.

Run this before any export to catch data quality problems early.
"""

from django.db.models import Q

from ..models.learner import Learner, LearnerEnrollment
from ..models.curriculum import SchoolSubject, SubjectOffering
from ..models.marks import FinalMark, SubjectEnrollment
from ..models.attendance import AttendanceSummary
from ..models.school import AcademicYear, School
from ..models.lurits import LURITSValidationIssue
from ..utils.sa_id import validate_sa_id


def run_all_validations(academic_year: AcademicYear) -> tuple:
    """
    Run all validation rules for the given academic year.
    Clears existing issues for this year first (re-running is idempotent).
    Returns (error_count, warning_count).
    """
    # Clear previous issues for this year before re-running
    LURITSValidationIssue.objects.filter(academic_year=academic_year).delete()

    issues = []

    issues.extend(_validate_school(academic_year))
    issues.extend(_validate_learners(academic_year))
    issues.extend(_validate_subjects(academic_year))
    issues.extend(_validate_results(academic_year))

    LURITSValidationIssue.objects.bulk_create(issues)

    errors = sum(1 for i in issues if i.severity == LURITSValidationIssue.SEVERITY_ERROR)
    warnings = len(issues) - errors

    return errors, warnings


def _issue(academic_year, severity, category, message, field="", learner=None):
    return LURITSValidationIssue(
        academic_year=academic_year,
        severity=severity,
        category=category,
        message=message,
        field=field,
        learner=learner,
    )


def _error(academic_year, category, message, field="", learner=None):
    return _issue(
        academic_year, LURITSValidationIssue.SEVERITY_ERROR,
        category, message, field, learner
    )


def _warning(academic_year, category, message, field="", learner=None):
    return _issue(
        academic_year, LURITSValidationIssue.SEVERITY_WARNING,
        category, message, field, learner
    )


def _validate_school(academic_year):
    issues = []
    school = School.get_current()

    if not school:
        issues.append(_error(
            academic_year,
            LURITSValidationIssue.CATEGORY_SCHOOL,
            "No school profile configured. Create one in the admin first.",
        ))
        return issues

    if not school.emis_number:
        issues.append(_error(
            academic_year,
            LURITSValidationIssue.CATEGORY_SCHOOL,
            "School EMIS number is missing. Required for all LURITS submissions.",
            "emis_number",
        ))

    if not school.province:
        issues.append(_error(
            academic_year,
            LURITSValidationIssue.CATEGORY_SCHOOL,
            "School province is not set.",
            "province",
        ))

    return issues


def _validate_learners(academic_year):
    """
    Check that each active learner has the minimum fields
    required for a LURITS learner record.
    """
    issues = []

    enrollments = LearnerEnrollment.objects.filter(
        academic_year=academic_year,
        is_active=True,
    ).select_related("learner", "grade", "stream")

    for enrollment in enrollments:
        learner = enrollment.learner

        # Identifier: must have SA ID or birth cert number
        if not learner.sa_id_number and not learner.birth_cert_number:
            issues.append(_error(
                academic_year,
                LURITSValidationIssue.CATEGORY_LEARNER,
                "No SA ID number or birth certificate number. "
                "At least one identifier is required.",
                "sa_id_number",
                learner,
            ))
        elif learner.sa_id_number:
            valid, result = validate_sa_id(learner.sa_id_number)
            if not valid:
                issues.append(_error(
                    academic_year,
                    LURITSValidationIssue.CATEGORY_LEARNER,
                    f"SA ID number is invalid: {result}",
                    "sa_id_number",
                    learner,
                ))

        # LURITS ULI (Unique Learner Identifier) - warning if missing
        if not learner.emis_learner_number:
            issues.append(_warning(
                academic_year,
                LURITSValidationIssue.CATEGORY_LEARNER,
                "LURITS Unique Learner Identifier (ULI) not set. "
                "Obtain from the DBE LURITS portal and update this record.",
                "emis_learner_number",
                learner,
            ))

        # Required demographics
        for field, label in [
            ("date_of_birth", "Date of birth"),
            ("gender", "Gender"),
            ("home_language", "Home language"),
            ("nationality", "Nationality"),
        ]:
            if not getattr(learner, field):
                issues.append(_error(
                    academic_year,
                    LURITSValidationIssue.CATEGORY_LEARNER,
                    f"{label} is missing.",
                    field,
                    learner,
                ))

        # Stream assignment
        if not enrollment.stream:
            issues.append(_warning(
                academic_year,
                LURITSValidationIssue.CATEGORY_ENROLLMENT,
                "Learner is not assigned to a class (stream). "
                "Assign a stream for accurate class-level reporting.",
                "stream",
                learner,
            ))

    return issues


def _validate_subjects(academic_year):
    """
    Check that all active subjects have LURITS codes set.
    """
    issues = []

    missing_code = SchoolSubject.objects.filter(
        is_active=True,
        lurits_code="",
    )
    for subject in missing_code:
        issues.append(_warning(
            academic_year,
            LURITSValidationIssue.CATEGORY_RESULTS,
            f"Subject '{subject.name}' ({subject.code}) has no LURITS subject code. "
            f"Results for this subject will be exported without a LURITS code.",
            "lurits_code",
        ))

    return issues


def _validate_results(academic_year):
    """
    Check that compulsory subjects have final marks for all enrolled learners.
    """
    issues = []

    enrollments = LearnerEnrollment.objects.filter(
        academic_year=academic_year,
        is_active=True,
    ).select_related("learner", "grade")

    for enrollment in enrollments:
        learner = enrollment.learner

        # Get compulsory offerings for this grade
        compulsory = SubjectOffering.objects.filter(
            grade=enrollment.grade,
            is_compulsory=True,
            is_active=True,
        ).select_related("subject")

        for offering in compulsory:
            has_sub_enrollment = SubjectEnrollment.objects.filter(
                learner=learner,
                subject_offering=offering,
                academic_year=academic_year,
            ).exists()

            if not has_sub_enrollment:
                issues.append(_warning(
                    academic_year,
                    LURITSValidationIssue.CATEGORY_RESULTS,
                    f"Not enrolled in compulsory subject: {offering.subject.name}.",
                    "subject_enrollment",
                    learner,
                ))
                continue

            sub_enr = SubjectEnrollment.objects.get(
                learner=learner,
                subject_offering=offering,
                academic_year=academic_year,
            )
            if not FinalMark.objects.filter(
                subject_enrollment=sub_enr,
                final_percentage__isnull=False,
            ).exists():
                issues.append(_warning(
                    academic_year,
                    LURITSValidationIssue.CATEGORY_RESULTS,
                    f"No final mark for {offering.subject.name}. "
                    f"Capture marks and calculate finals before export.",
                    "final_mark",
                    learner,
                ))

    return issues