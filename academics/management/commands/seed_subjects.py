from django.core.management.base import BaseCommand

from academics.models.curriculum import Curriculum, SchoolSubject

# CAPS subjects with approximate LURITS codes.
# Verify these codes against the current LURITS specification before submission.
_SUBJECTS = [
    # Languages
    {"name": "English Home Language",            "code": "EHL",  "lurits_code": "0011", "type": SchoolSubject.TYPE_LANGUAGE,        "home_lang": True,  "sba": 40, "exam": 60, "sort": 10},
    {"name": "Afrikaans Home Language",           "code": "AHL",  "lurits_code": "0021", "type": SchoolSubject.TYPE_LANGUAGE,        "home_lang": True,  "sba": 40, "exam": 60, "sort": 11},
    {"name": "isiZulu Home Language",             "code": "ZHL",  "lurits_code": "0031", "type": SchoolSubject.TYPE_LANGUAGE,        "home_lang": True,  "sba": 40, "exam": 60, "sort": 12},
    {"name": "isiXhosa Home Language",            "code": "XHL",  "lurits_code": "0041", "type": SchoolSubject.TYPE_LANGUAGE,        "home_lang": True,  "sba": 40, "exam": 60, "sort": 13},
    {"name": "Sesotho Home Language",             "code": "SHL",  "lurits_code": "0051", "type": SchoolSubject.TYPE_LANGUAGE,        "home_lang": True,  "sba": 40, "exam": 60, "sort": 14},
    {"name": "English First Additional Language", "code": "EFAL", "lurits_code": "0012", "type": SchoolSubject.TYPE_LANGUAGE,        "home_lang": False, "sba": 40, "exam": 60, "sort": 20},
    {"name": "Afrikaans First Additional Language","code": "AFAL", "lurits_code": "0022", "type": SchoolSubject.TYPE_LANGUAGE,        "home_lang": False, "sba": 40, "exam": 60, "sort": 21},
    # Mathematics
    {"name": "Mathematics",                       "code": "MAT",  "lurits_code": "0121", "type": SchoolSubject.TYPE_MATHEMATICS,     "home_lang": False, "sba": 25, "exam": 75, "sort": 30},
    {"name": "Mathematical Literacy",             "code": "LIT",  "lurits_code": "0122", "type": SchoolSubject.TYPE_MATHEMATICS,     "home_lang": False, "sba": 25, "exam": 75, "sort": 31},
    # Natural Sciences
    {"name": "Life and Living",                   "code": "LL",   "lurits_code": "0211", "type": SchoolSubject.TYPE_NATURAL_SCIENCE, "home_lang": False, "sba": 100,"exam": 0,  "sort": 40},
    {"name": "Natural Sciences and Technology",   "code": "NST",  "lurits_code": "0212", "type": SchoolSubject.TYPE_NATURAL_SCIENCE, "home_lang": False, "sba": 75, "exam": 25, "sort": 41},
    {"name": "Natural Sciences",                  "code": "NS",   "lurits_code": "0213", "type": SchoolSubject.TYPE_NATURAL_SCIENCE, "home_lang": False, "sba": 40, "exam": 60, "sort": 42},
    {"name": "Physical Sciences",                 "code": "PHY",  "lurits_code": "0214", "type": SchoolSubject.TYPE_NATURAL_SCIENCE, "home_lang": False, "sba": 25, "exam": 75, "sort": 43},
    {"name": "Life Sciences",                     "code": "LFS",  "lurits_code": "0215", "type": SchoolSubject.TYPE_NATURAL_SCIENCE, "home_lang": False, "sba": 25, "exam": 75, "sort": 44},
    # Social Sciences
    {"name": "Social Sciences",                   "code": "SS",   "lurits_code": "0311", "type": SchoolSubject.TYPE_SOCIAL_SCIENCE,  "home_lang": False, "sba": 40, "exam": 60, "sort": 50},
    {"name": "History",                           "code": "HIS",  "lurits_code": "0312", "type": SchoolSubject.TYPE_SOCIAL_SCIENCE,  "home_lang": False, "sba": 25, "exam": 75, "sort": 51},
    {"name": "Geography",                         "code": "GGR",  "lurits_code": "0313", "type": SchoolSubject.TYPE_SOCIAL_SCIENCE,  "home_lang": False, "sba": 25, "exam": 75, "sort": 52},
    # Technology
    {"name": "Technology",                        "code": "TEC",  "lurits_code": "0411", "type": SchoolSubject.TYPE_TECHNOLOGY,      "home_lang": False, "sba": 40, "exam": 60, "sort": 60},
    {"name": "Computer Applications Technology",  "code": "CAT",  "lurits_code": "0412", "type": SchoolSubject.TYPE_TECHNOLOGY,      "home_lang": False, "sba": 25, "exam": 75, "sort": 61},
    {"name": "Information Technology",            "code": "IT",   "lurits_code": "0413", "type": SchoolSubject.TYPE_TECHNOLOGY,      "home_lang": False, "sba": 25, "exam": 75, "sort": 62},
    # Arts
    {"name": "Creative Arts",                     "code": "CA",   "lurits_code": "0511", "type": SchoolSubject.TYPE_ARTS,            "home_lang": False, "sba": 75, "exam": 25, "sort": 70},
    {"name": "Music",                             "code": "MUS",  "lurits_code": "0512", "type": SchoolSubject.TYPE_ARTS,            "home_lang": False, "sba": 75, "exam": 25, "sort": 71},
    {"name": "Visual Arts",                       "code": "VA",   "lurits_code": "0513", "type": SchoolSubject.TYPE_ARTS,            "home_lang": False, "sba": 75, "exam": 25, "sort": 72},
    {"name": "Dramatic Arts",                     "code": "DRA",  "lurits_code": "0514", "type": SchoolSubject.TYPE_ARTS,            "home_lang": False, "sba": 75, "exam": 25, "sort": 73},
    # Commerce
    {"name": "Economic and Management Sciences",  "code": "EMS",  "lurits_code": "0611", "type": SchoolSubject.TYPE_COMMERCE,        "home_lang": False, "sba": 40, "exam": 60, "sort": 80},
    {"name": "Business Studies",                  "code": "BST",  "lurits_code": "0612", "type": SchoolSubject.TYPE_COMMERCE,        "home_lang": False, "sba": 25, "exam": 75, "sort": 81},
    {"name": "Economics",                         "code": "ECO",  "lurits_code": "0613", "type": SchoolSubject.TYPE_COMMERCE,        "home_lang": False, "sba": 25, "exam": 75, "sort": 82},
    {"name": "Accounting",                        "code": "ACC",  "lurits_code": "0614", "type": SchoolSubject.TYPE_COMMERCE,        "home_lang": False, "sba": 25, "exam": 75, "sort": 83},
    # Life Orientation
    {"name": "Life Orientation",                  "code": "LO",   "lurits_code": "0711", "type": SchoolSubject.TYPE_LIFE_ORIENTATION,"home_lang": False, "sba": 100,"exam": 0,  "sort": 90},
    # Agricultural
    {"name": "Agricultural Sciences",             "code": "AGR",  "lurits_code": "0811", "type": SchoolSubject.TYPE_AGRICULTURAL,    "home_lang": False, "sba": 25, "exam": 75, "sort": 100},
    {"name": "Agricultural Management Practices", "code": "AMP",  "lurits_code": "0812", "type": SchoolSubject.TYPE_AGRICULTURAL,    "home_lang": False, "sba": 25, "exam": 75, "sort": 101},
]


class Command(BaseCommand):
    help = (
        "Seed SchoolSubject with common CAPS subjects. "
        "LURITS codes are approximate - verify against current LURITS specification."
    )

    def handle(self, *args, **options):
        caps = Curriculum.objects.filter(code=Curriculum.CAPS).first()
        if not caps:
            self.stdout.write(
                self.style.ERROR(
                    "CAPS curriculum not found. "
                    "Create it first via admin or run: "
                    "Curriculum.objects.create(code='caps', name='CAPS (DBE)', is_default=True)"
                )
            )
            return

        created = updated = 0

        for data in _SUBJECTS:
            subject, was_created = SchoolSubject.objects.update_or_create(
                code=data["code"],
                defaults={
                    "curriculum": caps,
                    "name": data["name"],
                    "lurits_code": data["lurits_code"],
                    "subject_type": data["type"],
                    "is_home_language": data["home_lang"],
                    "default_sba_weighting": data["sba"],
                    "default_exam_weighting": data["exam"],
                    "sort_order": data["sort"],
                    "is_active": True,
                },
            )
            label = "CREATED" if was_created else "updated"
            self.stdout.write(f"  {label}: {subject.code} - {subject.name}")
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {created} created, {updated} updated. "
                f"Total active subjects: {SchoolSubject.objects.filter(is_active=True).count()}."
            )
        )
        self.stdout.write(
            self.style.WARNING(
                "\nNote: LURITS codes are approximate. "
                "Verify against the current DBE LURITS specification before submissions."
            )
        )