"""
Seed realistic demo data for the Scholr academics module.
Idempotent - safe to run multiple times.

Creates:
  - School profile (Themba Comprehensive School)
  - 2026 Academic year with 4 terms (Term 1 open for marks)
  - 5 departments with HODs
  - 6 staff users (principal, 2 HODs, 2 teachers, 1 clerk)
  - Grades 8-12 activated
  - 4 streams (10A, 10B, 11A, 12A)
  - CAPS curriculum with 6 subjects
  - Subject offerings and assessment tasks for Grade 10
  - 15 learners with guardians across 3 streams
  - Realistic marks (mix of passing, failing, at-risk)
  - Conduct ratings
  - 2 active interventions
  - Report card template
  - Performance analysis run
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from academics.models import (
    AcademicYear,
    AssessmentRecord,
    AssessmentTask,
    ConductRating,
    Curriculum,
    Department,
    Grade,
    GradeStream,
    Guardian,
    InterventionProgram,
    LearnerEnrollment,
    LearnerGuardian,
    LearnerIntervention,
    ProgressNote,
    ReportCardTemplate,
    School,
    SchoolStaff,
    SchoolSubject,
    SubjectEnrollment,
    SubjectOffering,
    Term,
    Learner,
)
from academics.services.marks_calculator import MarksCalculatorService
from academics.services.performance_analyzer import PerformanceAnalyzerService

User = get_user_model()
PASSWORD = "Demo1234!"


# ==================== MARKS TARGETS ====================
# Target percentage per learner per subject code.
# These drive all the AssessmentRecord values created below.

GRADE_10A_LEARNERS = [
    # (first, last, home_language, gender, dob, {code: pct})
    ("Sipho",     "Dlamini",    "isizulu",   "M", "2010-03-15", {"EHL": 72, "MAT": 68, "LFS": 75, "BST": 70, "GGR": 65, "LO": 80}),
    ("Zanele",    "Khumalo",    "isizulu",   "F", "2010-07-22", {"EHL": 85, "MAT": 88, "LFS": 82, "BST": 79, "GGR": 83, "LO": 90}),
    ("Thabo",     "Molefe",     "sesotho",   "M", "2009-11-08", {"EHL": 38, "MAT": 32, "LFS": 45, "BST": 36, "GGR": 40, "LO": 55}),
    ("Nomsa",     "Nkosi",      "isizulu",   "F", "2010-01-30", {"EHL": 62, "MAT": 58, "LFS": 65, "BST": 60, "GGR": 55, "LO": 70}),
    ("Bongani",   "Sithole",    "isizulu",   "M", "2010-05-12", {"EHL": 30, "MAT": 28, "LFS": 35, "BST": 32, "GGR": 25, "LO": 45}),
    ("Lerato",    "Mokoena",    "sesotho",   "F", "2009-09-18", {"EHL": 55, "MAT": 72, "LFS": 60, "BST": 65, "GGR": 68, "LO": 75}),
    ("Tebogo",    "Pietersen",  "setswana",  "M", "2010-04-25", {"EHL": 48, "MAT": 45, "LFS": 52, "BST": 50, "GGR": 47, "LO": 60}),
    ("Kgomotso",  "Botha",      "setswana",  "F", "2010-08-03", {"EHL": 75, "MAT": 70, "LFS": 68, "BST": 72, "GGR": 78, "LO": 85}),
    ("Pieter",    "du Plessis", "afrikaans", "M", "2009-12-17", {"EHL": 65, "MAT": 62, "LFS": 58, "BST": 60, "GGR": 63, "LO": 72}),
    ("Sarah",     "Ndlovu",     "english",   "F", "2010-02-28", {"EHL": 80, "MAT": 55, "LFS": 72, "BST": 68, "GGR": 75, "LO": 88}),
]

GRADE_10B_LEARNERS = [
    ("James",    "van der Berg", "afrikaans", "M", "2010-06-11", {"EHL": 55, "MAT": 50, "LFS": 48, "BST": 52, "GGR": 55, "LO": 65}),
    ("Amahle",   "Zulu",         "isizulu",   "F", "2010-03-05", {"EHL": 70, "MAT": 65, "LFS": 68, "BST": 72, "GGR": 70, "LO": 80}),
    ("Lethiwe",  "Ngcobo",       "isizulu",   "F", "2009-10-21", {"EHL": 42, "MAT": 35, "LFS": 40, "BST": 45, "GGR": 38, "LO": 55}),
]

# Grade 11 learners - no marks yet, just enrolled
GRADE_11A_LEARNERS = [
    ("Nonhlanhla", "Mthembu",   "isizulu",   "F", "2009-04-14", {}),
    ("Lungelo",    "Shabalala", "isizulu",   "M", "2008-08-30", {}),
]


def _pct_to_mark(percentage, max_marks):
    """Convert a target percentage to a whole-number raw mark."""
    return max(0, min(max_marks, round(percentage / 100 * max_marks)))


class Command(BaseCommand):
    help = "Seed demo data for the academics module."

    def handle(self, *args, **options):
        self.stdout.write("Seeding academics demo data...\n")

        with transaction.atomic():
            school          = self._school()
            acad_year, term = self._academic_year()
            depts           = self._departments()
            staff           = self._staff(depts)

            # Link principal - bypass save() singleton check by using update()
            School.objects.filter(pk=school.pk).update(principal=staff["principal"])

            grades   = self._grades(acad_year)
            streams  = self._streams(grades, staff, acad_year)
            curr     = self._curriculum()
            subjects = self._subjects(curr, depts)
            off10    = self._offerings_grade10(subjects, grades["Grade 10"])
            self._offerings_grade11(subjects, grades["Grade 11"])
            tasks    = self._tasks(off10)
            learners = self._learners(streams, acad_year)
            self._marks(learners, off10, tasks, term)
            self._conduct(learners, streams, acad_year, term)
            self._interventions(learners, off10, staff, acad_year)
            self._report_template()
            self._run_analysis(learners, acad_year, term)

        self.stdout.write(self.style.SUCCESS("\nDone. 15 learners, marks, conduct, and interventions created.\n"))
        self.stdout.write("Login credentials (all use password: Demo1234!)")
        self.stdout.write("  Principal:   mokoena.j")
        self.stdout.write("  HOD Langs:   dlamini.z")
        self.stdout.write("  HOD Maths:   khumalo.s")
        self.stdout.write("  Teacher 10A: nkosi.l")
        self.stdout.write("  Teacher 10B: sithole.t")
        self.stdout.write("  Admin Clerk: mahlangu.p")
        self.stdout.write("\nStart exploring:")
        self.stdout.write("  Learners:    /academics/learners/")
        self.stdout.write("  At-risk:     /academics/intervention/at-risk/")
        self.stdout.write("  Marks:       /academics/marks/")
        self.stdout.write("  Matrix:      /academics/curriculum/matrix/")

    # ==================== SCHOOL ====================

    def _school(self):
        """
        Singleton-safe school creation.
        update_or_create calls save() which triggers the singleton check.
        I'm using queryset.update() for existing records and bulk_create()
        for new ones - both bypass save() and therefore bypass full_clean().
        """
        school = School.objects.first()

        data = {
            "name": "Themba Comprehensive School",
            "emis_number": "700000001",
            "phase": School.PHASE_COMBINED,
            "curriculum": School.CURRICULUM_CAPS,
            "quintile": 3,
            "is_section_21": True,
            "is_independent": False,
            "phone": "+27 11 000 0001",
            "email": "info@themba.edu.za",
            "physical_address": "1 Ubuntu Street, Soweto",
            "province": "GP",
            "city_town": "Johannesburg",
            "postal_code": "1804",
        }

        if school:
            School.objects.filter(pk=school.pk).update(**data)
            school.refresh_from_db()
            self._log("School", school.name, False)
        else:
            School.objects.bulk_create([School(**data)])
            school = School.objects.first()
            self._log("School", school.name, True)

        return school

    # ==================== ACADEMIC YEAR & TERMS ====================

    def _academic_year(self):
        acad_year, created = AcademicYear.objects.update_or_create(
            year=2026,
            defaults={
                "start_date": datetime.date(2026, 1, 14),
                "end_date": datetime.date(2026, 11, 27),
                "is_current": True,
            },
        )
        self._log("Academic Year", "2026", created)

        term_data = [
            (1, datetime.date(2026, 1, 14), datetime.date(2026, 3, 27), True,  True),
            (2, datetime.date(2026, 4, 8),  datetime.date(2026, 6, 19), False, False),
            (3, datetime.date(2026, 7, 8),  datetime.date(2026, 9, 18), False, False),
            (4, datetime.date(2026, 10, 6), datetime.date(2026, 11, 27),False, False),
        ]

        current_term = None
        for num, start, end, is_current, marks_open in term_data:
            term, created = Term.objects.update_or_create(
                academic_year=acad_year,
                term_number=num,
                defaults={
                    "start_date": start,
                    "end_date": end,
                    "is_current": is_current,
                    "marks_open": marks_open,
                    "reports_released": False,
                },
            )
            if is_current:
                current_term = term
            self._log("Term", f"Term {num} 2026", created)

        return acad_year, current_term

    # ==================== DEPARTMENTS ====================

    def _departments(self):
        dept_data = [
            ("Languages",        "LANG", 10),
            ("Mathematics",      "MATH", 20),
            ("Natural Sciences", "NS",   30),
            ("Social Sciences",  "SS",   40),
            ("Commerce",         "COM",  50),
        ]
        depts = {}
        for name, code, sort in dept_data:
            dept, created = Department.objects.update_or_create(
                code=code,
                defaults={"name": name, "sort_order": sort, "is_active": True},
            )
            depts[name] = dept
            self._log("Department", name, created)
        return depts

    # ==================== STAFF ====================

    def _staff(self, depts):
        staff_data = [
            # (username, first, last, phone, role, dept_key, persal, sace, is_staff)
            ("mokoena.j",  "Jacob",    "Mokoena",      "+27821000001", "principal",  None,              "GP001234", "SACE001234", True),
            ("dlamini.z",  "Zanele",   "Dlamini",       "+27821000002", "hod",         "Languages",       "GP001235", "SACE001235", False),
            ("khumalo.s",  "Sipho",    "Khumalo",       "+27821000003", "hod",         "Mathematics",     "GP001236", "SACE001236", False),
            ("nkosi.l",    "Lindiwe",  "Nkosi",         "+27821000004", "teacher",     "Languages",       "GP001237", "SACE001237", False),
            ("sithole.t",  "Thabo",    "Sithole",       "+27821000005", "teacher",     "Mathematics",     "GP001238", "SACE001238", False),
            ("mahlangu.p", "Precious", "Mahlangu",      "+27821000006", "admin_clerk", None,              "GP001239", "",           False),
        ]

        role_map = {}
        for username, first, last, phone, role, dept_key, persal, sace, is_staff in staff_data:
            user, created = User.objects.update_or_create(
                username=username,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "phone_number": phone,
                    "role": role,
                    "is_active": True,
                    "is_verified": True,
                    "is_staff": is_staff,
                },
            )
            if created or not user.has_usable_password():
                user.set_password(PASSWORD)
                user.save(update_fields=["password"])

            # Create UserProfile to avoid any profile-access errors
            from accounts.models import UserProfile
            UserProfile.objects.get_or_create(
                user=user,
                defaults={"country": "South Africa", "region": "Gauteng"},
            )

            dept = depts.get(dept_key) if dept_key else None
            SchoolStaff.objects.update_or_create(
                user=user,
                defaults={
                    "department": dept,
                    "persal_number": persal,
                    "sace_number": sace,
                    "employment_type": SchoolStaff.EMPLOYMENT_PERMANENT,
                    "date_joined": datetime.date(2020, 1, 15),
                    "is_active": True,
                },
            )

            # Assign HODs to their departments
            if role == "hod" and dept:
                Department.objects.filter(pk=dept.pk).update(hod=user)

            role_map[role if role not in role_map else f"{role}_{username}"] = user
            role_map[username.split(".")[1][0] + "_" + username.split(".")[0]] = user
            self._log("Staff", f"{first} {last} ({role})", created)

        return {
            "principal": User.objects.get(username="mokoena.j"),
            "hod_lang":  User.objects.get(username="dlamini.z"),
            "hod_math":  User.objects.get(username="khumalo.s"),
            "teacher_a": User.objects.get(username="nkosi.l"),
            "teacher_b": User.objects.get(username="sithole.t"),
            "clerk":     User.objects.get(username="mahlangu.p"),
        }

    # ==================== GRADES ====================

    def _grades(self, acad_year):
        grade_data = [
            (0,  "Grade R",  Grade.PHASE_FOUNDATION,   False),
            (1,  "Grade 1",  Grade.PHASE_FOUNDATION,   False),
            (2,  "Grade 2",  Grade.PHASE_FOUNDATION,   False),
            (3,  "Grade 3",  Grade.PHASE_FOUNDATION,   False),
            (4,  "Grade 4",  Grade.PHASE_INTERMEDIATE, False),
            (5,  "Grade 5",  Grade.PHASE_INTERMEDIATE, False),
            (6,  "Grade 6",  Grade.PHASE_INTERMEDIATE, False),
            (7,  "Grade 7",  Grade.PHASE_SENIOR,       False),
            (8,  "Grade 8",  Grade.PHASE_SENIOR,       True),
            (9,  "Grade 9",  Grade.PHASE_SENIOR,       True),
            (10, "Grade 10", Grade.PHASE_FET,          True),
            (11, "Grade 11", Grade.PHASE_FET,          True),
            (12, "Grade 12", Grade.PHASE_FET,          True),
        ]
        grades = {}
        for num, name, phase, active in grade_data:
            grade, created = Grade.objects.update_or_create(
                grade_number=num,
                defaults={
                    "name": name,
                    "phase": phase,
                    "is_active": active,
                    "max_learners_per_class": 35,
                },
            )
            grades[name] = grade
            self._log("Grade", name, created)
        return grades

    # ==================== STREAMS ====================

    def _streams(self, grades, staff, acad_year):
        stream_data = [
            ("Grade 10", "A", staff["teacher_a"], "B01"),
            ("Grade 10", "B", staff["teacher_b"], "B02"),
            ("Grade 11", "A", staff["teacher_a"], "C01"),
            ("Grade 12", "A", staff["teacher_b"], "D01"),
        ]
        streams = {}
        for grade_name, stream_name, teacher, room in stream_data:
            stream, created = GradeStream.objects.update_or_create(
                grade=grades[grade_name],
                academic_year=acad_year,
                name=stream_name,
                defaults={
                    "homeroom_teacher": teacher,
                    "room_number": room,
                    "capacity": 35,
                    "is_active": True,
                },
            )
            key = f"{grade_name[-2:]}{stream_name}"  # '10A', '10B', '11A', '12A'
            streams[key] = stream
            self._log("Stream", f"Grade {grade_name[-2:]}{stream_name}", created)
        return streams

    # ==================== CURRICULUM ====================

    def _curriculum(self):
        curr, created = Curriculum.objects.update_or_create(
            code=Curriculum.CAPS,
            defaults={
                "name": "CAPS (DBE)",
                "is_active": True,
                "is_default": True,
            },
        )
        self._log("Curriculum", "CAPS", created)
        return curr

    # ==================== SUBJECTS ====================

    def _subjects(self, curr, depts):
        subject_data = [
            # (name, code, lurits, type, home_lang, sba, exam, dept, sort)
            ("English Home Language",  "EHL", "0011", SchoolSubject.TYPE_LANGUAGE,        True,  25, 75, "Languages",        10),
            ("Mathematics",            "MAT", "0121", SchoolSubject.TYPE_MATHEMATICS,     False, 25, 75, "Mathematics",      20),
            ("Life Sciences",          "LFS", "0215", SchoolSubject.TYPE_NATURAL_SCIENCE, False, 25, 75, "Natural Sciences", 30),
            ("Business Studies",       "BST", "0612", SchoolSubject.TYPE_COMMERCE,        False, 25, 75, "Commerce",         40),
            ("Geography",              "GGR", "0313", SchoolSubject.TYPE_SOCIAL_SCIENCE,  False, 25, 75, "Social Sciences",  50),
            ("Life Orientation",       "LO",  "0711", SchoolSubject.TYPE_LIFE_ORIENTATION,False,100,  0, "Social Sciences",  60),
        ]
        subjects = {}
        for name, code, lurits, stype, home_lang, sba, exam, dept_name, sort in subject_data:
            subj, created = SchoolSubject.objects.update_or_create(
                code=code,
                defaults={
                    "curriculum": curr,
                    "department": depts.get(dept_name),
                    "name": name,
                    "lurits_code": lurits,
                    "subject_type": stype,
                    "is_home_language": home_lang,
                    "default_sba_weighting": sba,
                    "default_exam_weighting": exam,
                    "default_pass_mark": 40,
                    "sort_order": sort,
                    "is_active": True,
                },
            )
            subjects[code] = subj
            self._log("Subject", name, created)
        return subjects

    # ==================== OFFERINGS ====================

    def _offerings_grade10(self, subjects, grade):
        """Create subject offerings for Grade 10."""
        offering_data = [
            ("EHL", 25, 75, True),
            ("MAT", 25, 75, True),
            ("LFS", 25, 75, True),
            ("BST", 25, 75, False),  # elective in FET
            ("GGR", 25, 75, False),  # elective in FET
            ("LO",  100, 0, True),
        ]
        offerings = {}
        for code, sba, exam, compulsory in offering_data:
            offering, created = SubjectOffering.objects.update_or_create(
                subject=subjects[code],
                grade=grade,
                defaults={
                    "sba_weighting": sba,
                    "exam_weighting": exam,
                    "pass_mark": 40,
                    "is_compulsory": compulsory,
                    "is_active": True,
                },
            )
            offerings[code] = offering
            self._log("Offering", f"Grade 10 {code}", created)
        return offerings

    def _offerings_grade11(self, subjects, grade):
        """Create subject offerings for Grade 11 - same subjects."""
        for code in ["EHL", "MAT", "LFS", "BST", "GGR", "LO"]:
            sba = 100 if code == "LO" else 25
            exam = 0 if code == "LO" else 75
            SubjectOffering.objects.update_or_create(
                subject=subjects[code],
                grade=grade,
                defaults={
                    "sba_weighting": sba,
                    "exam_weighting": exam,
                    "pass_mark": 40,
                    "is_compulsory": code in ("EHL", "MAT", "LO"),
                    "is_active": True,
                },
            )

    # ==================== ASSESSMENT TASKS ====================

    def _tasks(self, offerings):
        """
        Create Term 1 assessment tasks for Grade 10 offerings.
        Each subject gets 2 SBA tasks (Test + practical/assignment).
        LO gets 1 portfolio task.
        """
        task_data = {
            "EHL": [
                ("Test 1",   AssessmentTask.TYPE_TEST,        50, True,  1),
                ("Oral 1",   AssessmentTask.TYPE_ORAL,        20, True,  2),
            ],
            "MAT": [
                ("Test 1",   AssessmentTask.TYPE_TEST,        50, True,  1),
                ("Assignment 1", AssessmentTask.TYPE_ASSIGNMENT, 30, True, 2),
            ],
            "LFS": [
                ("Test 1",   AssessmentTask.TYPE_TEST,        50, True,  1),
                ("Practical 1", AssessmentTask.TYPE_PRACTICAL,30, True,  2),
            ],
            "BST": [
                ("Test 1",   AssessmentTask.TYPE_TEST,        50, True,  1),
                ("Project 1", AssessmentTask.TYPE_PROJECT,    40, True,  2),
            ],
            "GGR": [
                ("Test 1",   AssessmentTask.TYPE_TEST,        50, True,  1),
                ("Map Work 1", AssessmentTask.TYPE_ASSIGNMENT,30, True,  2),
            ],
            "LO": [
                ("Portfolio Term 1", AssessmentTask.TYPE_PORTFOLIO, 50, True, 1),
            ],
        }

        tasks = {}
        for code, task_list in task_data.items():
            for name, task_type, max_marks, is_sba, seq in task_list:
                task, created = AssessmentTask.objects.update_or_create(
                    subject_offering=offerings[code],
                    term_number=1,
                    name=name,
                    defaults={
                        "task_type": task_type,
                        "max_marks": max_marks,
                        "is_sba": is_sba,
                        "sequence": seq,
                        "is_active": True,
                    },
                )
                tasks[f"{code}_{name}"] = task
                self._log("Task", f"Grade 10 {code}: {name}", created)
        return tasks

    # ==================== LEARNERS ====================

    def _learners(self, streams, acad_year):
        """
        Create learners, guardians, and grade enrollments.
        Returns a list of dicts with learner + marks context.
        """
        all_learner_data = (
            [(*row, "10A") for row in GRADE_10A_LEARNERS] +
            [(*row, "10B") for row in GRADE_10B_LEARNERS] +
            [(*row, "11A") for row in GRADE_11A_LEARNERS]
        )

        learners = []
        admission_counter = 1

        for first, last, home_lang, gender, dob_str, marks_pct, stream_key in all_learner_data:
            admission = f"2026{admission_counter:04d}"
            admission_counter += 1

            learner, created = Learner.objects.update_or_create(
                admission_number=admission,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "gender": gender,
                    "date_of_birth": datetime.date.fromisoformat(dob_str),
                    "home_language": home_lang,
                    "nationality": "ZA",
                    "classification": Learner.CLASSIFICATION_MAINSTREAM,
                    "is_active": True,
                    "birth_cert_number": f"BC{admission}",
                },
            )
            self._log("Learner", f"{first} {last}", created)

            # Guardian
            guardian, g_created = Guardian.objects.update_or_create(
                phone_primary=f"+2783200{admission_counter:04d}",
                defaults={
                    "first_name": "Parent of",
                    "last_name": last,
                    "email": f"parent.{last.lower().replace(' ', '')}@example.com",
                },
            )
            LearnerGuardian.objects.get_or_create(
                learner=learner,
                guardian=guardian,
                defaults={
                    "relationship": "mother",
                    "is_primary_contact": True,
                    "can_collect": True,
                    "receives_reports": True,
                    "receives_sms": True,
                },
            )
            self._log("Guardian", f"Parent of {last}", g_created)

            # Grade enrollment
            stream = streams[stream_key]
            grade = stream.grade
            enrollment, _ = LearnerEnrollment.objects.update_or_create(
                learner=learner,
                academic_year=acad_year,
                defaults={
                    "grade": grade,
                    "stream": stream,
                    "admission_date": datetime.date(2026, 1, 14),
                    "status": LearnerEnrollment.STATUS_ACTIVE,
                    "is_active": True,
                },
            )

            learners.append({
                "learner": learner,
                "stream": stream,
                "stream_key": stream_key,
                "enrollment": enrollment,
                "marks_pct": marks_pct,
            })

        return learners

    # ==================== MARKS ====================

    def _marks(self, learners, offerings, tasks, current_term):
        """
        Create AssessmentRecord for each learner-task pair using the
        target percentage from the marks matrix, then recalculate term marks.
        Only creates marks for Grade 10 learners (11A has no tasks yet).
        """
        calculator = MarksCalculatorService()

        for item in learners:
            if item["stream_key"] not in ("10A", "10B"):
                continue  # Grade 11 has no tasks yet

            learner = item["learner"]
            marks_pct = item["marks_pct"]
            stream = item["stream"]
            acad_year = current_term.academic_year

            for code, offering in offerings.items():
                # Create SubjectEnrollment
                sub_enrollment, _ = SubjectEnrollment.objects.get_or_create(
                    learner=learner,
                    subject_offering=offering,
                    academic_year=acad_year,
                    defaults={"stream": stream},
                )

                target_pct = marks_pct.get(code, 60)

                # Create a record for each task in this offering
                for task_key, task in tasks.items():
                    if not task_key.startswith(f"{code}_"):
                        continue

                    raw_mark = Decimal(str(_pct_to_mark(target_pct, task.max_marks)))

                    AssessmentRecord.objects.update_or_create(
                        subject_enrollment=sub_enrollment,
                        assessment_task=task,
                        defaults={
                            "raw_mark": raw_mark,
                            "is_absent": False,
                        },
                    )

                # Recalculate term marks
                calculator.calculate_and_save_term_mark(sub_enrollment, 1)
                calculator.calculate_and_save_final_mark(sub_enrollment)

        self.stdout.write("  Marks and term calculations complete.")

    # ==================== CONDUCT ====================

    def _conduct(self, learners, streams, acad_year, term):
        """Create conduct ratings for Term 1 for all Grade 10 learners."""
        conduct_map = {
            # admission_number prefix -> (effort, conduct, absent, late, comment)
            "20260001": ("1", "2", 0, 1, "Sipho shows great commitment and effort in all activities."),
            "20260002": ("1", "1", 0, 0, "Zanele is an outstanding learner - a real asset to the class."),
            "20260003": ("3", "3", 4, 3, "Thabo struggles to maintain focus. Intervention recommended."),
            "20260004": ("2", "2", 1, 0, "Nomsa is a diligent student who works consistently."),
            "20260005": ("4", "4", 8, 5, "Bongani requires urgent academic and pastoral support."),
            "20260006": ("2", "2", 0, 0, "Lerato is well-organised and participates actively."),
            "20260007": ("2", "3", 2, 1, "Tebogo needs to improve focus and homework submission."),
            "20260008": ("1", "1", 0, 0, "Kgomotso is a model learner who motivates her peers."),
            "20260009": ("2", "2", 1, 0, "Pieter is responsible and meets all deadlines."),
            "20260010": ("2", "1", 0, 0, "Sarah is enthusiastic and produces quality work."),
            "20260011": ("2", "3", 3, 2, "James needs to improve punctuality and homework submission."),
            "20260012": ("2", "2", 0, 0, "Amahle is a cooperative learner who shows initiative."),
            "20260013": ("3", "3", 5, 4, "Lethiwe requires additional academic support this term."),
        }

        for item in learners:
            learner = item["learner"]
            stream = item["stream"]
            if item["stream_key"] not in ("10A", "10B"):
                continue

            data = conduct_map.get(learner.admission_number, ("2", "2", 0, 0, ""))
            effort, conduct, absent, late, comment = data

            ConductRating.objects.update_or_create(
                learner=learner,
                stream=stream,
                academic_year=acad_year,
                term_number=1,
                defaults={
                    "effort_rating": effort,
                    "conduct_rating": conduct,
                    "days_absent": absent,
                    "days_late": late,
                    "homeroom_comment": comment,
                    "principal_comment": "",
                },
            )

        self.stdout.write("  Conduct ratings created.")

    # ==================== INTERVENTIONS ====================

    def _interventions(self, learners, offerings, staff, acad_year):
        """Create intervention programs and two active interventions."""
        # Seed programs if not already present
        programs = {}
        for name, ptype, sort in [
            ("Extra Mathematics Classes",  InterventionProgram.TYPE_EXTRA_CLASSES,  10),
            ("Subject Support Programme",  InterventionProgram.TYPE_SUBJECT_SUPPORT, 30),
            ("Social Worker Referral",     InterventionProgram.TYPE_SOCIAL_WORKER,  50),
            ("Parent / Guardian Meeting",  InterventionProgram.TYPE_PARENT_MEETING, 70),
        ]:
            prog, _ = InterventionProgram.objects.update_or_create(
                name=name,
                defaults={"program_type": ptype, "sort_order": sort, "is_active": True},
            )
            programs[ptype] = prog

        # Thabo Molefe (20260003) - extra maths
        thabo = next((i["learner"] for i in learners if i["learner"].admission_number == "20260003"), None)
        # Bongani Sithole (20260005) - social worker
        bongani = next((i["learner"] for i in learners if i["learner"].admission_number == "20260005"), None)

        if thabo:
            LearnerIntervention.objects.update_or_create(
                learner=thabo,
                program=programs[InterventionProgram.TYPE_EXTRA_CLASSES],
                academic_year=acad_year,
                defaults={
                    "subject_offering": offerings.get("MAT"),
                    "status": LearnerIntervention.STATUS_ACTIVE,
                    "start_date": datetime.date(2026, 2, 3),
                    "assigned_to": staff["hod_math"],
                    "notes": "Thabo is struggling with algebra fundamentals. Extra classes every Tuesday at 14:00.",
                    "created_by": staff["teacher_a"],
                },
            )
            ProgressNote.objects.update_or_create(
                learner=thabo,
                academic_year=acad_year,
                created_by=staff["teacher_a"],
                defaults={
                    "note": "Thabo has shown a slight improvement since starting extra classes. He needs more practice with equations.",
                    "share_with_parent": True,
                },
            )
            self.stdout.write("  Intervention created for Thabo Molefe (extra maths).")

        if bongani:
            LearnerIntervention.objects.update_or_create(
                learner=bongani,
                program=programs[InterventionProgram.TYPE_SOCIAL_WORKER],
                academic_year=acad_year,
                defaults={
                    "subject_offering": None,
                    "status": LearnerIntervention.STATUS_ACTIVE,
                    "start_date": datetime.date(2026, 2, 10),
                    "assigned_to": staff["principal"],
                    "notes": "Bongani is failing all subjects and has high absenteeism. Social worker referral initiated to assess home situation.",
                    "created_by": staff["principal"],
                },
            )
            self.stdout.write("  Intervention created for Bongani Sithole (social worker).")

    # ==================== REPORT TEMPLATE ====================

    def _report_template(self):
        template, created = ReportCardTemplate.objects.update_or_create(
            name="CAPS Term Report",
            defaults={
                "template_type": ReportCardTemplate.TYPE_CAPS,
                "show_attendance": True,
                "show_conduct": True,
                "show_subject_comments": True,
                "show_homeroom_comment": True,
                "show_principal_comment": True,
                "show_caps_symbol": True,
                "show_promotion_status": False,
                "footer_text": "Themba Comprehensive School - Empowering Young Minds",
                "is_default": True,
                "is_active": True,
            },
        )
        self._log("Report Template", "CAPS Term Report", created)

    # ==================== PERFORMANCE ANALYSIS ====================

    def _run_analysis(self, learners, acad_year, current_term):
        """Run performance analysis for all learners with marks."""
        analyzer = PerformanceAnalyzerService()
        count = 0
        for item in learners:
            if item["marks_pct"]:  # only analyse learners who have marks
                analyzer.analyze_learner(item["learner"], acad_year, current_term)
                count += 1
        self.stdout.write(f"  Performance analysis run for {count} learners.")

    # ==================== HELPERS ====================

    def _log(self, model, name, created):
        verb = "created" if created else "exists"
        self.stdout.write(f"  [{verb}] {model}: {name}")