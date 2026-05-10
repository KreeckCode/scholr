from django.core.management.base import BaseCommand
from academics.models.attendance import AbsenceReason

_REASONS = [
    ("Sick (no certificate)",       "SICK_NC",  False, False, 10),
    ("Sick (with certificate)",     "SICK_MC",  True,  True,  11),
    ("Family matter",               "FAMILY",   False, False, 20),
    ("Bereavement",                 "BEREAVE",  True,  False, 21),
    ("School sports event",         "SPORTS",   True,  False, 30),
    ("School cultural event",       "CULTURAL", True,  False, 31),
    ("Religious observance",        "RELIGION", False, False, 40),
    ("Suspended",                   "SUSPEND",  False, True,  50),
    ("Awaiting transfer",           "TRANSFER", False, False, 60),
    ("Transport problem",           "TRANSP",   False, False, 70),
    ("Unknown",                     "UNKNOWN",  False, False, 90),
    ("Other",                       "OTHER",    False, True,  99),
]


class Command(BaseCommand):
    help = "Seed common absence reasons."

    def handle(self, *args, **options):
        created = updated = 0
        for name, code, is_excused, req_note, sort in _REASONS:
            _, was_created = AbsenceReason.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "is_excused": is_excused,
                    "requires_note": req_note,
                    "sort_order": sort,
                    "is_active": True,
                },
            )
            verb = "created" if was_created else "updated"
            self.stdout.write(f"  {verb}: {name}")
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(f"\nDone. {created} created, {updated} updated.")
        )