from django.core.management.base import BaseCommand

from academics.models.structure import Grade

_GRADE_DATA = [
    {"name": "Grade R",  "grade_number": 0,  "phase": Grade.PHASE_FOUNDATION},
    {"name": "Grade 1",  "grade_number": 1,  "phase": Grade.PHASE_FOUNDATION},
    {"name": "Grade 2",  "grade_number": 2,  "phase": Grade.PHASE_FOUNDATION},
    {"name": "Grade 3",  "grade_number": 3,  "phase": Grade.PHASE_FOUNDATION},
    {"name": "Grade 4",  "grade_number": 4,  "phase": Grade.PHASE_INTERMEDIATE},
    {"name": "Grade 5",  "grade_number": 5,  "phase": Grade.PHASE_INTERMEDIATE},
    {"name": "Grade 6",  "grade_number": 6,  "phase": Grade.PHASE_INTERMEDIATE},
    {"name": "Grade 7",  "grade_number": 7,  "phase": Grade.PHASE_SENIOR},
    {"name": "Grade 8",  "grade_number": 8,  "phase": Grade.PHASE_SENIOR},
    {"name": "Grade 9",  "grade_number": 9,  "phase": Grade.PHASE_SENIOR},
    {"name": "Grade 10", "grade_number": 10, "phase": Grade.PHASE_FET},
    {"name": "Grade 11", "grade_number": 11, "phase": Grade.PHASE_FET},
    {"name": "Grade 12", "grade_number": 12, "phase": Grade.PHASE_FET},
]

# Which grade numbers are active per school phase
_ACTIVE_BY_PHASE = {
    "primary":   set(range(0, 8)),   # Grade R - 7
    "secondary": set(range(8, 13)),  # Grade 8 - 12
    "combined":  set(range(0, 13)),  # Grade R - 12
}


class Command(BaseCommand):
    help = "Seed the grades table with all SA school grades (Grade R through Grade 12)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--phase",
            type=str,
            choices=["primary", "secondary", "combined"],
            default="combined",
            help="Activate grades matching this school phase (default: combined).",
        )

    def handle(self, *args, **options):
        phase = options["phase"]
        active_numbers = _ACTIVE_BY_PHASE[phase]

        self.stdout.write(f"Seeding grades for a {phase} school...\n")
        created = updated = 0

        for data in _GRADE_DATA:
            data["is_active"] = data["grade_number"] in active_numbers
            grade, was_created = Grade.objects.update_or_create(
                grade_number=data["grade_number"],
                defaults=data,
            )
            label = "CREATED" if was_created else "updated"
            active_label = "active" if grade.is_active else "inactive"
            self.stdout.write(f"  {label}: {grade.name} ({active_label})")
            if was_created:
                created += 1
            else:
                updated += 1

        active_count = Grade.objects.filter(is_active=True).count()
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {created} created, {updated} updated. "
                f"{active_count} grades active."
            )
        )