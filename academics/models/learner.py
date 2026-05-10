from django.db import models
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.utils import timezone

from .school import AcademicYear


# I'm defining these at module level so they're reusable in forms
SA_LANGUAGE_CHOICES = [
    ("afrikaans", "Afrikaans"),
    ("english", "English"),
    ("isindebele", "isiNdebele"),
    ("isixhosa", "isiXhosa"),
    ("isizulu", "isiZulu"),
    ("sepedi", "Sepedi"),
    ("sesotho", "Sesotho"),
    ("setswana", "Setswana"),
    ("siswati", "siSwati"),
    ("tshivenda", "Tshivenda"),
    ("xitsonga", "Xitsonga"),
    ("other", "Other"),
]

NATIONALITY_CHOICES = [
    ("ZA", "South African"),
    ("ZW", "Zimbabwean"),
    ("MZ", "Mozambican"),
    ("LS", "Basotho (Lesotho)"),
    ("SZ", "Swazi"),
    ("BW", "Botswanan"),
    ("ZM", "Zambian"),
    ("CD", "Congolese (DRC)"),
    ("SO", "Somali"),
    ("ET", "Ethiopian"),
    ("NG", "Nigerian"),
    ("GH", "Ghanaian"),
    ("other", "Other"),
]


class Learner(models.Model):
    """
    Permanent school record for a learner.
    Exists independently of a User account - young learners (Grade R-7)
    typically don't have portal logins.
    A User(role=LEARNER) is linked when portal access is granted by admin.
    """

    GENDER_MALE = "M"
    GENDER_FEMALE = "F"
    GENDER_CHOICES = [
        (GENDER_MALE, "Male"),
        (GENDER_FEMALE, "Female"),
    ]

    CLASSIFICATION_MAINSTREAM = "mainstream"
    CLASSIFICATION_LSEN = "lsen"
    CLASSIFICATION_SPECIAL = "special"
    CLASSIFICATION_CHOICES = [
        (CLASSIFICATION_MAINSTREAM, "Mainstream"),
        (CLASSIFICATION_LSEN, "LSEN (Learning Support)"),
        (CLASSIFICATION_SPECIAL, "Special Needs"),
    ]

    TRANSPORT_WALK = "walk"
    TRANSPORT_SCHOLAR = "scholar_transport"
    TRANSPORT_PARENT = "parent"
    TRANSPORT_PUBLIC = "public_transport"
    TRANSPORT_HOSTEL = "hostel"
    TRANSPORT_CHOICES = [
        (TRANSPORT_WALK, "Walks"),
        (TRANSPORT_SCHOLAR, "Scholar Transport"),
        (TRANSPORT_PARENT, "Parent/Guardian"),
        (TRANSPORT_PUBLIC, "Public Transport"),
        (TRANSPORT_HOSTEL, "Hostel Resident"),
    ]

    # Linked portal account - null until access is needed
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="learner_profile",
        limit_choices_to={"role": "learner"},
        help_text="Portal login account. Created when learner needs system access.",
    )

    # Core identity
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    preferred_name = models.CharField(
        max_length=100,
        blank=True,
        help_text="Name learner goes by if different from legal first name",
    )

    # SA-specific identifiers - at least one is required (enforced in form)
    sa_id_number = models.CharField(
        max_length=13,
        blank=True,
        db_index=True,
        help_text="13-digit SA ID - Luhn checksum validated on save",
    )
    birth_cert_number = models.CharField(
        max_length=50,
        blank=True,
        help_text="Birth certificate number for learners without an SA ID",
    )

    # School identifiers
    admission_number = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        help_text="School-assigned admission number. Auto-generated on registration.",
    )
    emis_learner_number = models.CharField(
        max_length=20,
        blank=True,
        db_index=True,
        help_text="LURITS Unique Learner Identifier (ULI) assigned by DBE",
    )

    # Demographics
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    home_language = models.CharField(
        max_length=20,
        choices=SA_LANGUAGE_CHOICES,
        help_text="Home/first language - required for LURITS",
    )
    nationality = models.CharField(
        max_length=10,
        choices=NATIONALITY_CHOICES,
        default="ZA",
    )
    classification = models.CharField(
        max_length=20,
        choices=CLASSIFICATION_CHOICES,
        default=CLASSIFICATION_MAINSTREAM,
    )

    # Medical - sensitive, access controlled in views
    allergies = models.TextField(blank=True)
    chronic_conditions = models.TextField(blank=True)
    current_medication = models.TextField(
        blank=True,
        help_text="Medication taken at school - include dosage and schedule",
    )
    medical_aid_scheme = models.CharField(max_length=200, blank=True)
    medical_aid_number = models.CharField(max_length=100, blank=True)

    # Transport
    transport_mode = models.CharField(
        max_length=30,
        choices=TRANSPORT_CHOICES,
        blank=True,
    )
    transport_notes = models.CharField(
        max_length=200,
        blank=True,
        help_text="Route, stop, or pickup point details",
    )

    # Address
    street_address = models.TextField(blank=True)
    suburb = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=10, blank=True)

    # Photo
    photo = models.ImageField(
        upload_to="learner_photos/%Y/",
        blank=True,
        null=True,
    )

    # Status - soft delete only, records are permanent
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Inactive = withdrawn or transferred. Records are never deleted.",
    )

    # Audit
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="learners_registered",
        help_text="Staff member who registered this learner",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "learners"
        verbose_name = "Learner"
        verbose_name_plural = "Learners"
        ordering = ["last_name", "first_name"]
        indexes = [
            models.Index(fields=["last_name", "first_name"], name="idx_learner_name"),
            models.Index(fields=["sa_id_number"], name="idx_learner_sa_id"),
            models.Index(fields=["admission_number"], name="idx_learner_admission"),
            models.Index(fields=["is_active"], name="idx_learner_active"),
        ]

    def __str__(self):
        return f"{self.last_name}, {self.first_name} ({self.admission_number})"

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}"

    def get_display_name(self):
        """Preferred name for registers and reports."""
        return self.preferred_name if self.preferred_name else self.first_name

    def get_age(self):
        if not self.date_of_birth:
            return None
        today = timezone.now().date()
        age = today.year - self.date_of_birth.year
        if (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day):
            age -= 1
        return age

    def has_medical_info(self):
        """Flag for registers - does this learner have medical notes on file?"""
        return bool(self.allergies or self.chronic_conditions or self.current_medication)

    def get_current_enrollment(self):
        """Fetch active enrollment for the current academic year."""
        current_year = AcademicYear.get_current()
        if not current_year:
            return None
        return (
            self.enrollments.filter(academic_year=current_year, is_active=True)
            .select_related("grade", "stream")
            .first()
        )

    def get_primary_guardian(self):
        """Return the primary contact guardian relationship."""
        return (
            self.guardian_relationships.filter(is_primary_contact=True)
            .select_related("guardian")
            .first()
        )


class Guardian(models.Model):
    """
    Parent or legal guardian.
    One record covers all siblings at this school.
    Portal access is granted by linking to a User(role=PARENT).
    """

    # I'm keeping relationship choices here so LearnerGuardian can reference them
    RELATIONSHIP_CHOICES = [
        ("mother", "Mother"),
        ("father", "Father"),
        ("grandmother", "Grandmother"),
        ("grandfather", "Grandfather"),
        ("aunt", "Aunt"),
        ("uncle", "Uncle"),
        ("sibling", "Sibling"),
        ("legal_guardian", "Legal Guardian"),
        ("foster_parent", "Foster Parent"),
        ("social_worker", "Social Worker"),
        ("emergency_contact", "Emergency Contact Only"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="guardian_profile",
        limit_choices_to={"role": "parent"},
        help_text="Portal account - linked when parent needs access to the parent portal",
    )

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    sa_id_number = models.CharField(max_length=13, blank=True)

    # Contact
    phone_primary = models.CharField(max_length=20)
    phone_secondary = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    whatsapp_number = models.CharField(max_length=20, blank=True)

    # Address
    street_address = models.TextField(blank=True)
    suburb = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)

    # Employment - useful context for home visits and emergencies
    employer = models.CharField(max_length=200, blank=True)
    work_phone = models.CharField(max_length=20, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "guardians"
        verbose_name = "Guardian"
        verbose_name_plural = "Guardians"
        ordering = ["last_name", "first_name"]
        indexes = [
            models.Index(fields=["last_name", "first_name"], name="idx_guardian_name"),
            models.Index(fields=["phone_primary"], name="idx_guardian_phone"),
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}"

    def get_learners(self):
        """All active learners this guardian is linked to."""
        return Learner.objects.filter(
            guardian_relationships__guardian=self,
            is_active=True,
        ).distinct()


class LearnerGuardian(models.Model):
    """
    Through model linking a Learner to a Guardian.
    Stores the relationship type and per-child communication preferences.
    Siblings share one Guardian record but each has their own LearnerGuardian row.
    """

    learner = models.ForeignKey(
        Learner,
        on_delete=models.CASCADE,
        related_name="guardian_relationships",
    )
    guardian = models.ForeignKey(
        Guardian,
        on_delete=models.CASCADE,
        related_name="learner_relationships",
    )
    relationship = models.CharField(
        max_length=30,
        choices=Guardian.RELATIONSHIP_CHOICES,
    )

    # Contact preferences per child
    is_primary_contact = models.BooleanField(
        default=False,
        help_text="One primary contact per learner. Gets all communications first.",
    )
    can_collect = models.BooleanField(
        default=True,
        help_text="Authorised to collect this learner from school premises",
    )
    receives_reports = models.BooleanField(
        default=True,
        help_text="Should receive a copy of report cards",
    )
    receives_sms = models.BooleanField(
        default=True,
        help_text="Should receive SMS notifications for absence, fees, notices",
    )

    # Court restrictions - critical for safeguarding
    has_court_restriction = models.BooleanField(
        default=False,
        help_text="Flag if a court order restricts this guardian's access or collection rights",
    )
    court_restriction_notes = models.TextField(
        blank=True,
        help_text="Details of restriction. Treated as highly sensitive - staff eyes only.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "learner_guardians"
        verbose_name = "Learner Guardian"
        verbose_name_plural = "Learner Guardians"
        unique_together = [["learner", "guardian"]]
        indexes = [
            models.Index(
                fields=["learner", "is_primary_contact"],
                name="idx_lg_learner_primary",
            ),
            models.Index(fields=["guardian"], name="idx_lg_guardian"),
        ]

    def __str__(self):
        return (
            f"{self.guardian.get_full_name()} "
            f"({self.get_relationship_display()}) "
            f"of {self.learner.get_full_name()}"
        )


class LearnerEnrollment(models.Model):
    """
    Grade placement for a learner in a specific academic year.
    A new record is created each year. At year end, the outcome is recorded
    (promoted, retained, progressed) before the next year's records are created.
    """

    STATUS_ADMITTED = "admitted"
    STATUS_ACTIVE = "active"
    STATUS_WITHDRAWN = "withdrawn"
    STATUS_TRANSFERRED_OUT = "transferred_out"
    STATUS_COMPLETED = "completed"
    STATUS_CHOICES = [
        (STATUS_ADMITTED, "Admitted (Awaiting Start Date)"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_WITHDRAWN, "Withdrawn During Year"),
        (STATUS_TRANSFERRED_OUT, "Transferred to Another School"),
        (STATUS_COMPLETED, "Completed Academic Year"),
    ]

    # Year-end outcomes - set during promotion processing
    OUTCOME_PROMOTED = "promoted"
    OUTCOME_RETAINED = "retained"
    OUTCOME_PROGRESSED = "progressed"  # Age-progressed (conditional - Grade 10+)
    OUTCOME_WITHDRAWN = "withdrawn"
    OUTCOME_CHOICES = [
        (OUTCOME_PROMOTED, "Promoted (Passed)"),
        (OUTCOME_RETAINED, "Retained (Repeating Grade)"),
        (OUTCOME_PROGRESSED, "Age-Progressed (Conditionally Moved Up)"),
        (OUTCOME_WITHDRAWN, "Withdrawn"),
    ]

    learner = models.ForeignKey(
        Learner,
        on_delete=models.PROTECT,
        related_name="enrollments",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="learner_enrollments",
    )
    grade = models.ForeignKey(
        "academics.Grade",
        on_delete=models.PROTECT,
        related_name="enrollments",
    )
    stream = models.ForeignKey(
        "academics.GradeStream",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrollments",
        help_text="Class assignment. Can be set after initial enrollment.",
    )

    admission_date = models.DateField(default=timezone.now)
    withdrawal_date = models.DateField(null=True, blank=True)

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
    )
    year_end_outcome = models.CharField(
        max_length=20,
        choices=OUTCOME_CHOICES,
        blank=True,
        help_text="Set at end of year. Determines next year grade placement.",
    )

    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "learner_enrollments"
        verbose_name = "Learner Enrollment"
        verbose_name_plural = "Learner Enrollments"
        # A learner can only be enrolled in one grade per year
        unique_together = [["learner", "academic_year"]]
        ordering = ["-academic_year__year"]
        indexes = [
            models.Index(
                fields=["academic_year", "grade", "status"],
                name="idx_enroll_year_grade_status",
            ),
            models.Index(
                fields=["learner", "is_active"],
                name="idx_enroll_learner_active",
            ),
            models.Index(fields=["stream"], name="idx_enroll_stream"),
        ]

    def __str__(self):
        return (
            f"{self.learner.get_full_name()} - "
            f"{self.grade.name} ({self.academic_year.year})"
        )

    def withdraw(self, date=None):
        """Mark the learner as withdrawn. Called when they leave mid-year."""
        self.status = self.STATUS_WITHDRAWN
        self.withdrawal_date = date or timezone.now().date()
        self.is_active = False
        self.save(update_fields=["status", "withdrawal_date", "is_active", "updated_at"])


class LearnerTransfer(models.Model):
    """
    Records a learner transfer into or out of the school.
    LURITS requires transfer records with the other school's EMIS number.
    """

    DIRECTION_IN = "transfer_in"
    DIRECTION_OUT = "transfer_out"
    DIRECTION_CHOICES = [
        (DIRECTION_IN, "Transfer In (Arriving)"),
        (DIRECTION_OUT, "Transfer Out (Leaving)"),
    ]

    learner = models.ForeignKey(
        Learner,
        on_delete=models.PROTECT,
        related_name="transfers",
    )
    enrollment = models.ForeignKey(
        LearnerEnrollment,
        on_delete=models.PROTECT,
        related_name="transfers",
    )
    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES)
    transfer_date = models.DateField()

    other_school_name = models.CharField(max_length=200)
    other_school_emis = models.CharField(max_length=20, blank=True)
    other_school_province = models.CharField(max_length=3, blank=True)
    reason = models.TextField(blank=True)

    # DBE transfer card tracking
    transfer_card_issued = models.BooleanField(
        default=False,
        help_text="DBE Transfer Card issued to outgoing learner",
    )
    transfer_card_received = models.BooleanField(
        default=False,
        help_text="Transfer card received from incoming learner's previous school",
    )

    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="transfers_processed",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "learner_transfers"
        verbose_name = "Learner Transfer"
        verbose_name_plural = "Learner Transfers"
        ordering = ["-transfer_date"]
        indexes = [
            models.Index(
                fields=["learner", "direction"],
                name="idx_transfer_learner_dir",
            ),
        ]

    def __str__(self):
        verb = "from" if self.direction == self.DIRECTION_IN else "to"
        return f"{self.learner.get_full_name()} transferred {verb} {self.other_school_name}"


class LearnerDocument(models.Model):
    """
    Sensitive documents attached to a learner record.
    Served through a protected Django view - never directly via Nginx.
    Only staff can upload; access is role-gated at the view layer.
    """

    TYPE_BIRTH_CERT = "birth_cert"
    TYPE_ID = "id_document"
    TYPE_IMMUNISATION = "immunisation"
    TYPE_PREVIOUS_REPORT = "previous_report"
    TYPE_MEDICAL = "medical"
    TYPE_COURT_ORDER = "court_order"
    TYPE_STUDY_PERMIT = "study_permit"
    TYPE_OTHER = "other"

    DOCUMENT_TYPE_CHOICES = [
        (TYPE_BIRTH_CERT, "Birth Certificate"),
        (TYPE_ID, "SA ID / Passport"),
        (TYPE_IMMUNISATION, "Immunisation Record"),
        (TYPE_PREVIOUS_REPORT, "Previous School Report"),
        (TYPE_MEDICAL, "Medical Document"),
        (TYPE_COURT_ORDER, "Court Order"),
        (TYPE_STUDY_PERMIT, "Study Permit"),
        (TYPE_OTHER, "Other"),
    ]

    learner = models.ForeignKey(
        Learner,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    document_type = models.CharField(max_length=30, choices=DOCUMENT_TYPE_CHOICES)
    name = models.CharField(max_length=200)
    file = models.FileField(
        upload_to="learner_documents/%Y/%m/",
        validators=[
            FileExtensionValidator(
                allowed_extensions=["pdf", "jpg", "jpeg", "png"],
                message="Only PDF, JPG, and PNG files are accepted.",
            )
        ],
    )
    notes = models.TextField(blank=True)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="documents_uploaded",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "learner_documents"
        verbose_name = "Learner Document"
        verbose_name_plural = "Learner Documents"
        ordering = ["document_type", "-uploaded_at"]
        indexes = [
            models.Index(
                fields=["learner", "document_type"],
                name="idx_doc_learner_type",
            ),
        ]

    def __str__(self):
        return f"{self.learner.get_full_name()} - {self.get_document_type_display()}"

    def get_file_size(self):
        """Human-readable file size for the document list UI."""
        try:
            size = self.file.size
            if size < 1024:
                return f"{size} B"
            elif size < 1024 ** 2:
                return f"{size / 1024:.1f} KB"
            return f"{size / 1024 ** 2:.1f} MB"
        except (OSError, FileNotFoundError):
            return "Unknown"