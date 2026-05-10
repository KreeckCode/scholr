from django.core.management.base import BaseCommand
from academics.models.intervention import InterventionProgram

_PROGRAMS = [
    {"name": "Extra Mathematics Classes",   "type": InterventionProgram.TYPE_EXTRA_CLASSES,  "sort": 10},
    {"name": "Extra Language Classes",       "type": InterventionProgram.TYPE_EXTRA_CLASSES,  "sort": 11},
    {"name": "Extra Sciences Classes",       "type": InterventionProgram.TYPE_EXTRA_CLASSES,  "sort": 12},
    {"name": "Peer Tutoring",                "type": InterventionProgram.TYPE_PEER_TUTORING,  "sort": 20},
    {"name": "Subject Support Programme",    "type": InterventionProgram.TYPE_SUBJECT_SUPPORT,"sort": 30},
    {"name": "Reading Support",              "type": InterventionProgram.TYPE_READING_SUPPORT, "sort": 40},
    {"name": "Social Worker Referral",       "type": InterventionProgram.TYPE_SOCIAL_WORKER,  "sort": 50},
    {"name": "School Counsellor Referral",   "type": InterventionProgram.TYPE_COUNSELLING,    "sort": 60},
    {"name": "Parent / Guardian Meeting",    "type": InterventionProgram.TYPE_PARENT_MEETING, "sort": 70},
    {"name": "LSEN Assessment Referral",     "type": InterventionProgram.TYPE_LSEN_REFERRAL,  "sort": 80},
    {"name": "Other Intervention",           "type": InterventionProgram.TYPE_OTHER,           "sort": 99},
]


class Command(BaseCommand):
    help = "Seed InterventionProgram with common support program types."

    def handle(self, *args, **options):
        created = updated = 0
        for data in _PROGRAMS:
            program, was_created = InterventionProgram.objects.update_or_create(
                name=data["name"],
                defaults={
                    "program_type": data["type"],
                    "sort_order": data["sort"],
                    "is_active": True,
                },
            )
            label = "CREATED" if was_created else "updated"
            self.stdout.write(f"  {label}: {program.name}")
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {created} created, {updated} updated."
            )
        )