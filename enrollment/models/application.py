"""
Core enrollment application models.

Lifecycle:
  draft → submitted → under_review → offer_issued → payment_pending
       → enrolled

  Or: submitted → additional_docs_requested → under_review → rejected
  Or: submitted → possible_duplicate (admin clears or rejects)
  Or: offer_issued → waitlisted (if capacity reached after offer)

Django is the source of truth. These records are never modified after
the application transitions to STATUS_ENROLLED — the Learner record
created at that point becomes the authoritative record.
"""
import os
import uuid

from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

User = get_user_model()


def _document_upload_path(instance, filename):
    """
    Store enrollment documents outside any publicly served directory.
    Served through a protected Django view, not directly by Nginx.
    """
    ext = os.path.splitext(filename)[1].lower()
    unique = uuid.uuid4().hex[:10]
    return (
        f"enrollment_private/{instance.application.reference_number}/"
        f"{instance.document_type}_{unique}{ext}"
    )


# ==================== ENROLLMENT CONFIG ====================

class EnrollmentConfig(models.Model):
    """
    School-wide enrollment settings singleton.
    Only one record should ever exist. Accessed via EnrollmentConfig.get().
    Stores operational decisions that change per year and belong in the DB,
    not the .env file.
    """

    # Re-enrollment behaviour
    require_parent_reenrollment_confirmation = models.BooleanField(
        default=False,
        help_text=(
            'If enabled, promoted learners are not fully re-enrolled until '
            'a parent confirms via the portal. If disabled, staff promotion '
            'immediately creates the new academic year enrollment.'
        ),
    )

    # Default registration fee (can be overridden per ApplicationTemplate)
    default_registration_fee = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
    )

    # Late application surcharge
    late_application_fee = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
    )

    # Email addresses that receive admin alerts
    enrollment_admin_email = models.EmailField(
        blank=True,
        help_text='Email address that receives new application notifications.',
    )

    # Status message shown on the public application landing page
    public_notice = models.TextField(
        blank=True,
        help_text='Optional notice shown above the application entry page.',
    )

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        User,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        verbose_name = 'Enrollment Configuration'
        verbose_name_plural = 'Enrollment Configuration'

    def __str__(self):
        return 'Enrollment Configuration'

    @classmethod
    def get(cls):
        """Return the singleton config, creating it with defaults if absent."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ==================== APPLICATION TEMPLATE ====================

class ApplicationTemplate(models.Model):
    """
    Configures the application form for a specific entry grade and enrollment year.
    Each grade+year combination needs its own template so requirements,
    capacity, and dates can be managed independently.

    Example: Grade 8 — 2026, Grade R — 2026, Certificate Level 1 — 2026.
    """

    name = models.CharField(
        max_length=100,
        help_text='Display name, e.g. "Grade 8 Entry 2026".',
    )
    entry_grade = models.ForeignKey(
        'academics.Grade',
        on_delete=models.PROTECT,
        related_name='application_templates',
    )
    enrollment_year = models.PositiveIntegerField(
        help_text='The academic year the applicant will enter, e.g. 2026.',
    )

    # ---- Enrollment window ----
    open_date = models.DateTimeField(
        null=True, blank=True,
        help_text='When applications open. Null means always open.',
    )
    close_date = models.DateTimeField(
        null=True, blank=True,
        help_text='When applications close. Null means no closing date.',
    )
    late_applications_allowed = models.BooleanField(
        default=False,
        help_text='Allow submissions after close_date (with a late surcharge if configured).',
    )

    # ---- Capacity ----
    enrollment_capacity = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Maximum learners to approve. Null means unlimited.',
    )
    waitlist_enabled = models.BooleanField(
        default=True,
        help_text='Accept waitlist applications when capacity is reached.',
    )

    # ---- Fee ----
    registration_fee_required = models.BooleanField(default=False)
    registration_fee_amount = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
    )
    registration_fee_is_refundable = models.BooleanField(
        default=False,
        help_text='Whether the registration fee is refunded if the application is rejected.',
    )

    # ---- Form sections ----
    show_previous_school_section = models.BooleanField(
        default=True,
        help_text='Show the previous school fields on the application form.',
    )
    show_medical_section = models.BooleanField(
        default=True,
        help_text='Show the medical information section.',
    )
    show_subject_selection = models.BooleanField(
        default=False,
        help_text=(
            'Show the subject selection step. '
            'Only needed for grade transitions requiring elective choices.'
        ),
    )
    show_essay_section = models.BooleanField(
        default=False,
        help_text='Show custom essay questions (theological colleges).',
    )
    essay_question = models.TextField(
        blank=True,
        help_text='The essay question shown when show_essay_section is True.',
    )

    # ---- Priority rules ----
    sibling_priority_enabled = models.BooleanField(
        default=True,
        help_text='Give priority to siblings of currently enrolled learners.',
    )
    staff_child_priority_enabled = models.BooleanField(
        default=True,
        help_text='Give priority to children of current staff members.',
    )

    # ---- Consent documents ----
    # A comma-separated list of consent types to collect during this application.
    # Defined choices in ConsentRecord.CONSENT_TYPES.
    required_consents = models.JSONField(
        default=list,
        help_text=(
            'List of consent type codes required for this template. '
            'e.g. ["fee_agreement","code_of_conduct","popia_consent"]'
        ),
    )

    is_active = models.BooleanField(
        default=True,
        help_text='Only active templates appear on the public application page.',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        unique_together = [('entry_grade', 'enrollment_year')]
        ordering = ['enrollment_year', 'entry_grade__grade_number']
        verbose_name = 'Application Template'
        verbose_name_plural = 'Application Templates'

    def __str__(self):
        return self.name

    @property
    def is_open(self):
        """Return True if the enrollment window is currently open."""
        now = timezone.now()
        if self.open_date and now < self.open_date:
            return False
        if self.close_date and now > self.close_date:
            return self.late_applications_allowed
        return self.is_active

    @property
    def approved_count(self):
        """Approved applications against this template."""
        return self.applications.filter(
            status=Application.STATUS_ENROLLED
        ).count()

    @property
    def is_at_capacity(self):
        if not self.enrollment_capacity:
            return False
        return self.approved_count >= self.enrollment_capacity


# ==================== DOCUMENT REQUIREMENTS ====================

class ApplicationDocumentRequirement(models.Model):
    """
    Documents required or optional for a specific ApplicationTemplate.
    Stored as a normalised table rather than JSON so admin can manage
    them via the standard Django admin interface.
    """

    DOCUMENT_TYPES = [
        ('birth_certificate',    'Certified Birth Certificate'),
        ('id_document',          'ID Document or Passport'),
        ('previous_report',      'Previous School Report (most recent)'),
        ('transfer_letter',      'Transfer Letter from Previous School'),
        ('immunisation',         'Immunisation Record'),
        ('medical_aid',          'Medical Aid Certificate'),
        ('court_order',          'Court Order (Custody/Guardianship)'),
        ('pastoral_reference',   'Pastoral Reference Letter'),
        ('proof_of_address',     'Proof of Address'),
        ('other',                'Other Document'),
    ]

    template = models.ForeignKey(
        ApplicationTemplate,
        on_delete=models.CASCADE,
        related_name='document_requirements',
    )
    document_type = models.CharField(max_length=40, choices=DOCUMENT_TYPES)
    is_required = models.BooleanField(
        default=True,
        help_text='Required documents block progression. Optional ones are requested but not blocking.',
    )
    description = models.CharField(
        max_length=200,
        blank=True,
        help_text='Extra instruction shown to applicant, e.g. "Must be certified in the last 3 months".',
    )

    class Meta:
        unique_together = [('template', 'document_type')]
        ordering = ['template', 'is_required', 'document_type']

    def __str__(self):
        req = 'Required' if self.is_required else 'Optional'
        return f'{self.template.name} — {self.get_document_type_display()} ({req})'


# ==================== APPLICATION ====================

class Application(models.Model):
    """
    A single prospective learner's application record.

    The applicant is NOT a User or Learner until the moment of successful
    conversion (status → STATUS_ENROLLED). Before that moment, all data
    lives here in isolation. The conversion_service creates User, Learner,
    LearnerEnrollment, and Guardian atomically from this record.

    After conversion, this record is permanently linked to the resulting
    Learner via the `learner` OneToOneField for audit purposes.
    """

    # ---- Status choices ----
    STATUS_DRAFT                  = 'draft'
    STATUS_SUBMITTED              = 'submitted'
    STATUS_DOCUMENTS_PENDING      = 'documents_pending'
    STATUS_UNDER_REVIEW           = 'under_review'
    STATUS_POSSIBLE_DUPLICATE     = 'possible_duplicate'
    STATUS_DOCS_REQUESTED         = 'additional_docs_requested'
    STATUS_OFFER_ISSUED           = 'offer_issued'
    STATUS_WAITLISTED             = 'waitlisted'
    STATUS_PAYMENT_PENDING        = 'payment_pending'
    STATUS_ENROLLED               = 'enrolled'
    STATUS_REJECTED               = 'rejected'
    STATUS_WITHDRAWN              = 'withdrawn'
    STATUS_CONVERSION_FAILED      = 'conversion_failed'

    STATUS_CHOICES = [
        (STATUS_DRAFT,             'Draft'),
        (STATUS_SUBMITTED,         'Submitted'),
        (STATUS_DOCUMENTS_PENDING, 'Documents Pending'),
        (STATUS_UNDER_REVIEW,      'Under Review'),
        (STATUS_POSSIBLE_DUPLICATE,'Possible Duplicate'),
        (STATUS_DOCS_REQUESTED,    'Additional Documents Requested'),
        (STATUS_OFFER_ISSUED,      'Conditional Offer Issued'),
        (STATUS_WAITLISTED,        'Waitlisted'),
        (STATUS_PAYMENT_PENDING,   'Awaiting Payment'),
        (STATUS_ENROLLED,          'Enrolled'),
        (STATUS_REJECTED,          'Rejected'),
        (STATUS_WITHDRAWN,         'Withdrawn'),
        (STATUS_CONVERSION_FAILED, 'Conversion Failed'),
    ]

    # ---- Identity types ----
    ID_SA            = 'sa_id'
    ID_PASSPORT      = 'passport'
    ID_ASYLUM        = 'asylum_permit'
    ID_OTHER         = 'other'

    ID_TYPE_CHOICES = [
        (ID_SA,       'SA ID Number'),
        (ID_PASSPORT, 'Passport'),
        (ID_ASYLUM,   'Asylum/Refugee Permit'),
        (ID_OTHER,    'Other'),
    ]

    # ---- Priority tiers ----
    PRIORITY_SIBLING      = 1
    PRIORITY_STAFF_CHILD  = 2
    PRIORITY_ALUMNI_CHILD = 3
    PRIORITY_GENERAL      = 4

    # ---- Core fields ----
    reference_number = models.CharField(
        max_length=20, unique=True, editable=False,
        help_text='Auto-generated. Format: ENR-{YEAR}-{5DIGIT}.',
    )
    template = models.ForeignKey(
        ApplicationTemplate,
        on_delete=models.PROTECT,
        related_name='applications',
    )
    enrollment_year = models.PositiveIntegerField(
        help_text='Taken from template.enrollment_year at submission time.',
    )
    target_grade = models.ForeignKey(
        'academics.Grade',
        on_delete=models.PROTECT,
        related_name='applications',
    )

    # ---- Applicant (child) details ----
    first_name   = models.CharField(max_length=100)
    last_name    = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    gender = models.CharField(
        max_length=10,
        choices=[('M', 'Male'), ('F', 'Female'), ('O', 'Other / Prefer not to say')],
    )

    # ---- Identity ----
    id_type   = models.CharField(max_length=20, choices=ID_TYPE_CHOICES, default=ID_SA)
    id_number = models.CharField(max_length=50)
    id_country_of_issue = models.CharField(
        max_length=60, blank=True,
        help_text='Required for passports and foreign documents.',
    )
    nationality   = models.CharField(max_length=60, blank=True)
    home_language = models.CharField(max_length=60, blank=True)

    # ---- Primary guardian ----
    guardian_first_name    = models.CharField(max_length=100)
    guardian_last_name     = models.CharField(max_length=100)
    guardian_relationship  = models.CharField(
        max_length=40,
        choices=[
            ('mother', 'Mother'),
            ('father', 'Father'),
            ('legal_guardian', 'Legal Guardian'),
            ('grandparent', 'Grandparent'),
            ('other', 'Other'),
        ],
    )
    guardian_email         = models.EmailField()
    guardian_phone         = models.CharField(max_length=20)
    guardian_id_number     = models.CharField(max_length=50, blank=True)
    guardian_employer      = models.CharField(max_length=100, blank=True)
    guardian_address_line1 = models.CharField(max_length=200)
    guardian_address_line2 = models.CharField(max_length=200, blank=True)
    guardian_city          = models.CharField(max_length=100)
    guardian_province      = models.CharField(max_length=60, blank=True)
    guardian_postal_code   = models.CharField(max_length=10, blank=True)

    # ---- Second guardian (optional) ----
    second_guardian_first_name   = models.CharField(max_length=100, blank=True)
    second_guardian_last_name    = models.CharField(max_length=100, blank=True)
    second_guardian_relationship = models.CharField(max_length=40, blank=True)
    second_guardian_email        = models.EmailField(blank=True)
    second_guardian_phone        = models.CharField(max_length=20, blank=True)

    # ---- Emergency contact (can differ from guardian) ----
    emergency_contact_name         = models.CharField(max_length=100)
    emergency_contact_phone        = models.CharField(max_length=20)
    emergency_contact_relationship = models.CharField(max_length=40)

    # ---- Previous school ----
    previous_school_name     = models.CharField(max_length=200, blank=True)
    previous_school_type     = models.CharField(
        max_length=20, blank=True,
        choices=[
            ('public', 'Public / Government'),
            ('private', 'Private / Independent'),
            ('homeschool', 'Homeschool'),
            ('other', 'Other'),
        ],
    )
    previous_grade_completed = models.CharField(max_length=20, blank=True)
    previous_school_country  = models.CharField(max_length=60, blank=True, default='South Africa')
    reason_for_leaving       = models.TextField(blank=True)

    # ---- Medical ----
    has_medical_conditions = models.BooleanField(default=False)
    medical_details        = models.TextField(
        blank=True,
        help_text='Allergies, chronic conditions, medication, special needs.',
    )
    has_learning_support_needs = models.BooleanField(default=False)
    learning_support_details   = models.TextField(blank=True)
    medical_aid_provider  = models.CharField(max_length=100, blank=True)
    medical_aid_number    = models.CharField(max_length=50, blank=True)

    # ---- Essay (optional, for theological entries) ----
    essay_answer = models.TextField(blank=True)

    # ---- Status & workflow ----
    status   = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    priority = models.PositiveSmallIntegerField(default=PRIORITY_GENERAL)

    # Admin review
    assigned_to   = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_applications',
        help_text='Enrollment officer handling this application.',
    )
    internal_notes = models.TextField(blank=True)
    rejection_reason = models.TextField(blank=True)

    # Waitlist
    waitlist_position = models.PositiveIntegerField(null=True, blank=True)

    # ---- Duplicate detection ----
    suspected_duplicate_of = models.ForeignKey(
        'self', null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='duplicate_suspects',
    )

    duplicate_cleared_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='duplicate_clearances',
    )
    duplicate_cleared_at = models.DateTimeField(null=True, blank=True)

    # ---- Fee ----
    # Links to accounts.Order when a registration fee is required.
    # Import avoided here to prevent circular imports — resolved in services.
    fee_order_id = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='PK of the associated accounts.Order for the registration fee.',
    )
    fee_exempted = models.BooleanField(default=False)
    fee_exemption_reason = models.CharField(max_length=200, blank=True)
    fee_exemption_granted_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='fee_exemptions_granted',
    )

    # ---- Conversion to Learner ----
    learner = models.OneToOneField(
        'academics.Learner',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='source_application',
        help_text='Set after successful enrollment conversion.',
    )
    converted_at    = models.DateTimeField(null=True, blank=True)
    converted_by    = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='enrollment_conversions',
    )
    conversion_error = models.TextField(
        blank=True,
        help_text='Populated when status=conversion_failed. Cleared on retry.',
    )

    # ---- Source tracking ----
    HOW_HEARD_CHOICES = [
        ('word_of_mouth', 'Word of mouth'),
        ('social_media',  'Social media'),
        ('website',       'School website'),
        ('flyer',         'Flyer / poster'),
        ('newspaper',     'Newspaper'),
        ('previous_sibling', 'Sibling attended'),
        ('staff_referral', 'Staff referral'),
        ('other',         'Other'),
    ]
    how_did_you_hear = models.CharField(
        max_length=30, blank=True, choices=HOW_HEARD_CHOICES,
    )

    # ---- Submission metadata ----
    submitted_at = models.DateTimeField(null=True, blank=True)
    ip_address   = models.GenericIPAddressField(null=True, blank=True)
    user_agent   = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-submitted_at', '-created_at']
        indexes = [
            models.Index(fields=['reference_number']),
            models.Index(fields=['status']),
            models.Index(fields=['enrollment_year']),
            models.Index(fields=['id_number', 'id_type']),
            models.Index(fields=['guardian_email']),
        ]
        verbose_name = 'Application'
        verbose_name_plural = 'Applications'

    def __str__(self):
        return f'{self.reference_number} — {self.first_name} {self.last_name}'

    @classmethod
    def generate_reference_number(cls, enrollment_year: int) -> str:
        """
        Generate a unique, human-readable reference number.
        Format: ENR-2026-00023
        Uses a counter padded to 5 digits. Retries if a collision occurs
        (extremely unlikely but possible under high concurrency).
        """
        from django.db import IntegrityError
        base_count = cls.objects.filter(enrollment_year=enrollment_year).count()
        for attempt in range(10):
            ref = f"ENR-{enrollment_year}-{str(base_count + attempt + 1).zfill(5)}"
            if not cls.objects.filter(reference_number=ref).exists():
                return ref
        # Fallback: append a UUID fragment
        return f"ENR-{enrollment_year}-{uuid.uuid4().hex[:6].upper()}"

    def get_full_name(self) -> str:
        return f'{self.first_name} {self.last_name}'

    def get_status_colour(self) -> str:
        """Return a Tailwind colour token for the current status badge."""
        return {
            self.STATUS_DRAFT:             'gray',
            self.STATUS_SUBMITTED:         'blue',
            self.STATUS_DOCUMENTS_PENDING: 'amber',
            self.STATUS_UNDER_REVIEW:      'indigo',
            self.STATUS_POSSIBLE_DUPLICATE:'orange',
            self.STATUS_DOCS_REQUESTED:    'amber',
            self.STATUS_OFFER_ISSUED:      'teal',
            self.STATUS_WAITLISTED:        'purple',
            self.STATUS_PAYMENT_PENDING:   'yellow',
            self.STATUS_ENROLLED:          'green',
            self.STATUS_REJECTED:          'red',
            self.STATUS_WITHDRAWN:         'gray',
            self.STATUS_CONVERSION_FAILED: 'red',
        }.get(self.status, 'gray')

    def transition_status(self, new_status: str, changed_by=None, notes: str = ''):
        """
        Move to a new status, recording the transition in ApplicationStatusHistory.
        Does NOT save() — caller is responsible for the transaction.
        """
        old_status = self.status
        self.status = new_status
        ApplicationStatusHistory.objects.create(
            application=self,
            from_status=old_status,
            to_status=new_status,
            changed_by=changed_by,
            notes=notes,
        )


# ==================== STATUS HISTORY ====================

class ApplicationStatusHistory(models.Model):
    """
    Append-only audit trail of every status change on an Application.
    Never modified after creation — provides full traceability.
    """

    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name='status_history',
    )
    from_status  = models.CharField(max_length=30, blank=True)
    to_status    = models.CharField(max_length=30)
    changed_by   = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
        help_text='Null indicates a system-generated transition.',
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    notes      = models.TextField(blank=True)

    class Meta:
        ordering = ['changed_at']
        verbose_name = 'Status History Entry'
        verbose_name_plural = 'Status History'

    def __str__(self):
        return (
            f'{self.application.reference_number}: '
            f'{self.from_status} → {self.to_status} '
            f'({self.changed_at.strftime("%d %b %Y %H:%M")})'
        )


# ==================== DOCUMENTS ====================

class ApplicationDocument(models.Model):
    """
    A file uploaded by the applicant (or re-uploaded after rejection).
    Previous versions are kept (status → superseded) for audit purposes.
    Documents are served via a protected view — not publicly accessible.
    """

    DOCUMENT_TYPES = ApplicationDocumentRequirement.DOCUMENT_TYPES

    STATUS_PENDING   = 'pending'
    STATUS_APPROVED  = 'approved'
    STATUS_REJECTED  = 'rejected'
    STATUS_SUPERSEDED = 'superseded'

    STATUS_CHOICES = [
        (STATUS_PENDING,    'Pending Review'),
        (STATUS_APPROVED,   'Approved'),
        (STATUS_REJECTED,   'Rejected — Re-upload Required'),
        (STATUS_SUPERSEDED, 'Superseded by Newer Upload'),
    ]

    application   = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name='documents',
    )
    document_type = models.CharField(max_length=40, choices=DOCUMENT_TYPES)
    upload        = models.FileField(upload_to=_document_upload_path)
    original_filename = models.CharField(max_length=255)
    file_size_bytes   = models.PositiveIntegerField(default=0)

    status           = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    version          = models.PositiveSmallIntegerField(
        default=1,
        help_text='Increments each time the applicant re-uploads this document type.',
    )
    rejection_reason = models.CharField(max_length=300, blank=True)

    uploaded_at  = models.DateTimeField(auto_now_add=True)
    reviewed_at  = models.DateTimeField(null=True, blank=True)
    reviewed_by  = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        ordering = ['application', 'document_type', '-version']
        indexes = [
            models.Index(fields=['application', 'document_type', 'status']),
        ]
        verbose_name = 'Application Document'
        verbose_name_plural = 'Application Documents'

    def __str__(self):
        return (
            f'{self.application.reference_number} — '
            f'{self.get_document_type_display()} v{self.version}'
        )

    def approve(self, reviewed_by):
        """Mark this document as approved."""
        self.status = self.STATUS_APPROVED
        self.reviewed_by = reviewed_by
        self.reviewed_at = timezone.now()
        self.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    def reject(self, reviewed_by, reason: str):
        """Mark this document as rejected and record the reason."""
        self.status = self.STATUS_REJECTED
        self.rejection_reason = reason
        self.reviewed_by = reviewed_by
        self.reviewed_at = timezone.now()
        self.save(update_fields=['status', 'rejection_reason', 'reviewed_by', 'reviewed_at'])

    def supersede(self):
        """Mark this version as superseded when a newer upload arrives."""
        self.status = self.STATUS_SUPERSEDED
        self.save(update_fields=['status'])


# ==================== CONSENT RECORDS ====================
#consent 1
class ConsentRecord(models.Model):
    """
    Append-only record of a parent/guardian digitally consenting to a document.
    Captures IP, user-agent, timestamp, and document version for ECTA compliance.
    Never updated — only new records are added.
    """

    CONSENT_FEE_AGREEMENT  = 'fee_agreement'
    CONSENT_CODE_OF_CONDUCT = 'code_of_conduct'
    CONSENT_POPIA           = 'popia_consent'
    CONSENT_MEDICAL         = 'medical_treatment'
    CONSENT_PHOTOGRAPHY     = 'photography'

    CONSENT_TYPES = [
        (CONSENT_FEE_AGREEMENT,   'School Fee Agreement'),
        (CONSENT_CODE_OF_CONDUCT, 'Code of Conduct'),
        (CONSENT_POPIA,           'POPIA Data Consent'),
        (CONSENT_MEDICAL,         'Medical Treatment Authorisation'),
        (CONSENT_PHOTOGRAPHY,     'Photography and Media Permission'),
    ]

    """
    Core enrollment application models.

    Lifecycle:
    draft → submitted → under_review → offer_issued → payment_pending
        → enrolled

    Or: submitted → additional_docs_requested → under_review → rejected
    Or: submitted → possible_duplicate (admin clears or rejects)
    Or: offer_issued → waitlisted (if capacity reached after offer)

    Django is the source of truth. These records are never modified after
    the application transitions to STATUS_ENROLLED — the Learner record
    created at that point becomes the authoritative record.
    """
import os
import uuid

from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

User = get_user_model()


def _document_upload_path(instance, filename):
    """
    Store enrollment documents outside any publicly served directory.
    Served through a protected Django view, not directly by Nginx.
    """
    ext = os.path.splitext(filename)[1].lower()
    unique = uuid.uuid4().hex[:10]
    return (
        f"enrollment_private/{instance.application.reference_number}/"
        f"{instance.document_type}_{unique}{ext}"
    )


# ==================== ENROLLMENT CONFIG ====================

class EnrollmentConfig(models.Model):
    """
    School-wide enrollment settings singleton.
    Only one record should ever exist. Accessed via EnrollmentConfig.get().
    Stores operational decisions that change per year and belong in the DB,
    not the .env file.
    """

    # Re-enrollment behaviour
    require_parent_reenrollment_confirmation = models.BooleanField(
        default=False,
        help_text=(
            'If enabled, promoted learners are not fully re-enrolled until '
            'a parent confirms via the portal. If disabled, staff promotion '
            'immediately creates the new academic year enrollment.'
        ),
    )

    # Default registration fee (can be overridden per ApplicationTemplate)
    default_registration_fee = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
    )

    # Late application surcharge
    late_application_fee = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
    )

    # Email addresses that receive admin alerts
    enrollment_admin_email = models.EmailField(
        blank=True,
        help_text='Email address that receives new application notifications.',
    )

    # Status message shown on the public application landing page
    public_notice = models.TextField(
        blank=True,
        help_text='Optional notice shown above the application entry page.',
    )

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        User,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        verbose_name = 'Enrollment Configuration'
        verbose_name_plural = 'Enrollment Configuration'

    def __str__(self):
        return 'Enrollment Configuration'

    @classmethod
    def get(cls):
        """Return the singleton config, creating it with defaults if absent."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ==================== APPLICATION TEMPLATE ====================

class ApplicationTemplate(models.Model):
    """
    Configures the application form for a specific entry grade and enrollment year.
    Each grade+year combination needs its own template so requirements,
    capacity, and dates can be managed independently.

    Example: Grade 8 — 2026, Grade R — 2026, Certificate Level 1 — 2026.
    """

    name = models.CharField(
        max_length=100,
        help_text='Display name, e.g. "Grade 8 Entry 2026".',
    )
    entry_grade = models.ForeignKey(
        'academics.Grade',
        on_delete=models.PROTECT,
        related_name='application_templates',
    )
    enrollment_year = models.PositiveIntegerField(
        help_text='The academic year the applicant will enter, e.g. 2026.',
    )

    # ---- Enrollment window ----
    open_date = models.DateTimeField(
        null=True, blank=True,
        help_text='When applications open. Null means always open.',
    )
    close_date = models.DateTimeField(
        null=True, blank=True,
        help_text='When applications close. Null means no closing date.',
    )
    late_applications_allowed = models.BooleanField(
        default=False,
        help_text='Allow submissions after close_date (with a late surcharge if configured).',
    )

    # ---- Capacity ----
    enrollment_capacity = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Maximum learners to approve. Null means unlimited.',
    )
    waitlist_enabled = models.BooleanField(
        default=True,
        help_text='Accept waitlist applications when capacity is reached.',
    )

    # ---- Fee ----
    registration_fee_required = models.BooleanField(default=False)
    registration_fee_amount = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
    )
    registration_fee_is_refundable = models.BooleanField(
        default=False,
        help_text='Whether the registration fee is refunded if the application is rejected.',
    )

    # ---- Form sections ----
    show_previous_school_section = models.BooleanField(
        default=True,
        help_text='Show the previous school fields on the application form.',
    )
    show_medical_section = models.BooleanField(
        default=True,
        help_text='Show the medical information section.',
    )
    show_subject_selection = models.BooleanField(
        default=False,
        help_text=(
            'Show the subject selection step. '
            'Only needed for grade transitions requiring elective choices.'
        ),
    )
    show_essay_section = models.BooleanField(
        default=False,
        help_text='Show custom essay questions (theological colleges).',
    )
    essay_question = models.TextField(
        blank=True,
        help_text='The essay question shown when show_essay_section is True.',
    )

    # ---- Priority rules ----
    sibling_priority_enabled = models.BooleanField(
        default=True,
        help_text='Give priority to siblings of currently enrolled learners.',
    )
    staff_child_priority_enabled = models.BooleanField(
        default=True,
        help_text='Give priority to children of current staff members.',
    )

    # ---- Consent documents ----
    # A comma-separated list of consent types to collect during this application.
    # Defined choices in ConsentRecord.CONSENT_TYPES.
    required_consents = models.JSONField(
        default=list,
        help_text=(
            'List of consent type codes required for this template. '
            'e.g. ["fee_agreement","code_of_conduct","popia_consent"]'
        ),
    )

    is_active = models.BooleanField(
        default=True,
        help_text='Only active templates appear on the public application page.',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        unique_together = [('entry_grade', 'enrollment_year')]
        ordering = ['enrollment_year', 'entry_grade__grade_number']
        verbose_name = 'Application Template'
        verbose_name_plural = 'Application Templates'

    def __str__(self):
        return self.name

    @property
    def is_open(self):
        """Return True if the enrollment window is currently open."""
        now = timezone.now()
        if self.open_date and now < self.open_date:
            return False
        if self.close_date and now > self.close_date:
            return self.late_applications_allowed
        return self.is_active

    @property
    def approved_count(self):
        """Approved applications against this template."""
        return self.applications.filter(
            status=Application.STATUS_ENROLLED
        ).count()

    @property
    def is_at_capacity(self):
        if not self.enrollment_capacity:
            return False
        return self.approved_count >= self.enrollment_capacity


# ==================== DOCUMENT REQUIREMENTS ====================

class ApplicationDocumentRequirement(models.Model):
    """
    Documents required or optional for a specific ApplicationTemplate.
    Stored as a normalised table rather than JSON so admin can manage
    them via the standard Django admin interface.
    """

    DOCUMENT_TYPES = [
        ('birth_certificate',    'Certified Birth Certificate'),
        ('id_document',          'ID Document or Passport'),
        ('previous_report',      'Previous School Report (most recent)'),
        ('transfer_letter',      'Transfer Letter from Previous School'),
        ('immunisation',         'Immunisation Record'),
        ('medical_aid',          'Medical Aid Certificate'),
        ('court_order',          'Court Order (Custody/Guardianship)'),
        ('pastoral_reference',   'Pastoral Reference Letter'),
        ('proof_of_address',     'Proof of Address'),
        ('other',                'Other Document'),
    ]

    template = models.ForeignKey(
        ApplicationTemplate,
        on_delete=models.CASCADE,
        related_name='document_requirements',
    )
    document_type = models.CharField(max_length=40, choices=DOCUMENT_TYPES)
    is_required = models.BooleanField(
        default=True,
        help_text='Required documents block progression. Optional ones are requested but not blocking.',
    )
    description = models.CharField(
        max_length=200,
        blank=True,
        help_text='Extra instruction shown to applicant, e.g. "Must be certified in the last 3 months".',
    )

    class Meta:
        unique_together = [('template', 'document_type')]
        ordering = ['template', 'is_required', 'document_type']

    def __str__(self):
        req = 'Required' if self.is_required else 'Optional'
        return f'{self.template.name} — {self.get_document_type_display()} ({req})'


# ==================== APPLICATION ====================

class Application(models.Model):
    """
    A single prospective learner's application record.

    The applicant is NOT a User or Learner until the moment of successful
    conversion (status → STATUS_ENROLLED). Before that moment, all data
    lives here in isolation. The conversion_service creates User, Learner,
    LearnerEnrollment, and Guardian atomically from this record.

    After conversion, this record is permanently linked to the resulting
    Learner via the `learner` OneToOneField for audit purposes.
    """

    # ---- Status choices ----
    STATUS_DRAFT                  = 'draft'
    STATUS_SUBMITTED              = 'submitted'
    STATUS_DOCUMENTS_PENDING      = 'documents_pending'
    STATUS_UNDER_REVIEW           = 'under_review'
    STATUS_POSSIBLE_DUPLICATE     = 'possible_duplicate'
    STATUS_DOCS_REQUESTED         = 'additional_docs_requested'
    STATUS_OFFER_ISSUED           = 'offer_issued'
    STATUS_WAITLISTED             = 'waitlisted'
    STATUS_PAYMENT_PENDING        = 'payment_pending'
    STATUS_ENROLLED               = 'enrolled'
    STATUS_REJECTED               = 'rejected'
    STATUS_WITHDRAWN              = 'withdrawn'
    STATUS_CONVERSION_FAILED      = 'conversion_failed'

    STATUS_CHOICES = [
        (STATUS_DRAFT,             'Draft'),
        (STATUS_SUBMITTED,         'Submitted'),
        (STATUS_DOCUMENTS_PENDING, 'Documents Pending'),
        (STATUS_UNDER_REVIEW,      'Under Review'),
        (STATUS_POSSIBLE_DUPLICATE,'Possible Duplicate'),
        (STATUS_DOCS_REQUESTED,    'Additional Documents Requested'),
        (STATUS_OFFER_ISSUED,      'Conditional Offer Issued'),
        (STATUS_WAITLISTED,        'Waitlisted'),
        (STATUS_PAYMENT_PENDING,   'Awaiting Payment'),
        (STATUS_ENROLLED,          'Enrolled'),
        (STATUS_REJECTED,          'Rejected'),
        (STATUS_WITHDRAWN,         'Withdrawn'),
        (STATUS_CONVERSION_FAILED, 'Conversion Failed'),
    ]

    # ---- Identity types ----
    ID_SA            = 'sa_id'
    ID_PASSPORT      = 'passport'
    ID_ASYLUM        = 'asylum_permit'
    ID_OTHER         = 'other'

    ID_TYPE_CHOICES = [
        (ID_SA,       'SA ID Number'),
        (ID_PASSPORT, 'Passport'),
        (ID_ASYLUM,   'Asylum/Refugee Permit'),
        (ID_OTHER,    'Other'),
    ]

    # ---- Priority tiers ----
    PRIORITY_SIBLING      = 1
    PRIORITY_STAFF_CHILD  = 2
    PRIORITY_ALUMNI_CHILD = 3
    PRIORITY_GENERAL      = 4

    # ---- Core fields ----
    reference_number = models.CharField(
        max_length=20, unique=True, editable=False,
        help_text='Auto-generated. Format: ENR-{YEAR}-{5DIGIT}.',
    )
    template = models.ForeignKey(
        ApplicationTemplate,
        on_delete=models.PROTECT,
        related_name='applications',
    )
    enrollment_year = models.PositiveIntegerField(
        help_text='Taken from template.enrollment_year at submission time.',
    )
    target_grade = models.ForeignKey(
        'academics.Grade',
        on_delete=models.PROTECT,
        related_name='applications',
    )

    # ---- Applicant (child) details ----
    first_name   = models.CharField(max_length=100)
    last_name    = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    gender = models.CharField(
        max_length=10,
        choices=[('M', 'Male'), ('F', 'Female'), ('O', 'Other / Prefer not to say')],
    )

    # ---- Identity ----
    id_type   = models.CharField(max_length=20, choices=ID_TYPE_CHOICES, default=ID_SA)
    id_number = models.CharField(max_length=50)
    id_country_of_issue = models.CharField(
        max_length=60, blank=True,
        help_text='Required for passports and foreign documents.',
    )
    nationality   = models.CharField(max_length=60, blank=True)
    home_language = models.CharField(max_length=60, blank=True)

    # ---- Primary guardian ----
    guardian_first_name    = models.CharField(max_length=100)
    guardian_last_name     = models.CharField(max_length=100)
    guardian_relationship  = models.CharField(
        max_length=40,
        choices=[
            ('mother', 'Mother'),
            ('father', 'Father'),
            ('legal_guardian', 'Legal Guardian'),
            ('grandparent', 'Grandparent'),
            ('other', 'Other'),
        ],
    )
    guardian_email         = models.EmailField()
    guardian_phone         = models.CharField(max_length=20)
    guardian_id_number     = models.CharField(max_length=50, blank=True)
    guardian_employer      = models.CharField(max_length=100, blank=True)
    guardian_address_line1 = models.CharField(max_length=200)
    guardian_address_line2 = models.CharField(max_length=200, blank=True)
    guardian_city          = models.CharField(max_length=100)
    guardian_province      = models.CharField(max_length=60, blank=True)
    guardian_postal_code   = models.CharField(max_length=10, blank=True)

    # ---- Second guardian (optional) ----
    second_guardian_first_name   = models.CharField(max_length=100, blank=True)
    second_guardian_last_name    = models.CharField(max_length=100, blank=True)
    second_guardian_relationship = models.CharField(max_length=40, blank=True)
    second_guardian_email        = models.EmailField(blank=True)
    second_guardian_phone        = models.CharField(max_length=20, blank=True)

    # ---- Emergency contact (can differ from guardian) ----
    emergency_contact_name         = models.CharField(max_length=100)
    emergency_contact_phone        = models.CharField(max_length=20)
    emergency_contact_relationship = models.CharField(max_length=40)

    # ---- Previous school ----
    previous_school_name     = models.CharField(max_length=200, blank=True)
    previous_school_type     = models.CharField(
        max_length=20, blank=True,
        choices=[
            ('public', 'Public / Government'),
            ('private', 'Private / Independent'),
            ('homeschool', 'Homeschool'),
            ('other', 'Other'),
        ],
    )
    previous_grade_completed = models.CharField(max_length=20, blank=True)
    previous_school_country  = models.CharField(max_length=60, blank=True, default='South Africa')
    reason_for_leaving       = models.TextField(blank=True)

    # ---- Medical ----
    has_medical_conditions = models.BooleanField(default=False)
    medical_details        = models.TextField(
        blank=True,
        help_text='Allergies, chronic conditions, medication, special needs.',
    )
    has_learning_support_needs = models.BooleanField(default=False)
    learning_support_details   = models.TextField(blank=True)
    medical_aid_provider  = models.CharField(max_length=100, blank=True)
    medical_aid_number    = models.CharField(max_length=50, blank=True)

    # ---- Essay (optional, for theological entries) ----
    essay_answer = models.TextField(blank=True)

    # ---- Status & workflow ----
    status   = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    priority = models.PositiveSmallIntegerField(default=PRIORITY_GENERAL)

    # Admin review
    assigned_to   = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_applications',
        help_text='Enrollment officer handling this application.',
    )
    internal_notes = models.TextField(blank=True)
    rejection_reason = models.TextField(blank=True)

    # Waitlist
    waitlist_position = models.PositiveIntegerField(null=True, blank=True)

    # ---- Duplicate detection ----
    suspected_duplicate_of = models.ForeignKey(
        'self', null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='duplicate_suspects',
    )
    duplicate_cleared_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='duplicate_clearances',
    )
    duplicate_cleared_at = models.DateTimeField(null=True, blank=True)

    # ---- Fee ----
    # Links to accounts.Order when a registration fee is required.
    # Import avoided here to prevent circular imports — resolved in services.
    fee_order_id = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='PK of the associated accounts.Order for the registration fee.',
    )
    fee_exempted = models.BooleanField(default=False)
    fee_exemption_reason = models.CharField(max_length=200, blank=True)
    fee_exemption_granted_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='fee_exemptions_granted',
    )

    # ---- Conversion to Learner ----
    learner = models.OneToOneField(
        'academics.Learner',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='source_application',
        help_text='Set after successful enrollment conversion.',
    )
    converted_at    = models.DateTimeField(null=True, blank=True)
    converted_by    = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='enrollment_conversions',
    )
    conversion_error = models.TextField(
        blank=True,
        help_text='Populated when status=conversion_failed. Cleared on retry.',
    )

    # ---- Source tracking ----
    HOW_HEARD_CHOICES = [
        ('word_of_mouth', 'Word of mouth'),
        ('social_media',  'Social media'),
        ('website',       'School website'),
        ('flyer',         'Flyer / poster'),
        ('newspaper',     'Newspaper'),
        ('previous_sibling', 'Sibling attended'),
        ('staff_referral', 'Staff referral'),
        ('other',         'Other'),
    ]
    how_did_you_hear = models.CharField(
        max_length=30, blank=True, choices=HOW_HEARD_CHOICES,
    )

    # ---- Submission metadata ----
    submitted_at = models.DateTimeField(null=True, blank=True)
    ip_address   = models.GenericIPAddressField(null=True, blank=True)
    user_agent   = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-submitted_at', '-created_at']
        indexes = [
            models.Index(fields=['reference_number']),
            models.Index(fields=['status']),
            models.Index(fields=['enrollment_year']),
            models.Index(fields=['id_number', 'id_type']),
            models.Index(fields=['guardian_email']),
        ]
        verbose_name = 'Application'
        verbose_name_plural = 'Applications'

    def __str__(self):
        return f'{self.reference_number} — {self.first_name} {self.last_name}'

    @classmethod
    def generate_reference_number(cls, enrollment_year: int) -> str:
        """
        Generate a unique, human-readable reference number.
        Format: ENR-2026-00023
        Uses a counter padded to 5 digits. Retries if a collision occurs
        (extremely unlikely but possible under high concurrency).
        """
        from django.db import IntegrityError
        base_count = cls.objects.filter(enrollment_year=enrollment_year).count()
        for attempt in range(10):
            ref = f"ENR-{enrollment_year}-{str(base_count + attempt + 1).zfill(5)}"
            if not cls.objects.filter(reference_number=ref).exists():
                return ref
        # Fallback: append a UUID fragment
        return f"ENR-{enrollment_year}-{uuid.uuid4().hex[:6].upper()}"

    def get_full_name(self) -> str:
        return f'{self.first_name} {self.last_name}'

    def get_status_colour(self) -> str:
        """Return a Tailwind colour token for the current status badge."""
        return {
            self.STATUS_DRAFT:             'gray',
            self.STATUS_SUBMITTED:         'blue',
            self.STATUS_DOCUMENTS_PENDING: 'amber',
            self.STATUS_UNDER_REVIEW:      'indigo',
            self.STATUS_POSSIBLE_DUPLICATE:'orange',
            self.STATUS_DOCS_REQUESTED:    'amber',
            self.STATUS_OFFER_ISSUED:      'teal',
            self.STATUS_WAITLISTED:        'purple',
            self.STATUS_PAYMENT_PENDING:   'yellow',
            self.STATUS_ENROLLED:          'green',
            self.STATUS_REJECTED:          'red',
            self.STATUS_WITHDRAWN:         'gray',
            self.STATUS_CONVERSION_FAILED: 'red',
        }.get(self.status, 'gray')

    def transition_status(self, new_status: str, changed_by=None, notes: str = ''):
        """
        Move to a new status, recording the transition in ApplicationStatusHistory.
        Does NOT save() — caller is responsible for the transaction.
        """
        old_status = self.status
        self.status = new_status
        ApplicationStatusHistory.objects.create(
            application=self,
            from_status=old_status,
            to_status=new_status,
            changed_by=changed_by,
            notes=notes,
        )


# ==================== STATUS HISTORY ====================

class ApplicationStatusHistory(models.Model):
    """
    Append-only audit trail of every status change on an Application.
    Never modified after creation — provides full traceability.
    """

    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name='status_history',
    )
    from_status  = models.CharField(max_length=30, blank=True)
    to_status    = models.CharField(max_length=30)
    changed_by   = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
        help_text='Null indicates a system-generated transition.',
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    notes      = models.TextField(blank=True)

    class Meta:
        ordering = ['changed_at']
        verbose_name = 'Status History Entry'
        verbose_name_plural = 'Status History'

    def __str__(self):
        return (
            f'{self.application.reference_number}: '
            f'{self.from_status} → {self.to_status} '
            f'({self.changed_at.strftime("%d %b %Y %H:%M")})'
        )


# ==================== DOCUMENTS ====================

class ApplicationDocument(models.Model):
    """
    A file uploaded by the applicant (or re-uploaded after rejection).
    Previous versions are kept (status → superseded) for audit purposes.
    Documents are served via a protected view — not publicly accessible.
    """

    DOCUMENT_TYPES = ApplicationDocumentRequirement.DOCUMENT_TYPES

    STATUS_PENDING   = 'pending'
    STATUS_APPROVED  = 'approved'
    STATUS_REJECTED  = 'rejected'
    STATUS_SUPERSEDED = 'superseded'

    STATUS_CHOICES = [
        (STATUS_PENDING,    'Pending Review'),
        (STATUS_APPROVED,   'Approved'),
        (STATUS_REJECTED,   'Rejected — Re-upload Required'),
        (STATUS_SUPERSEDED, 'Superseded by Newer Upload'),
    ]

    application   = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name='documents',
    )
    document_type = models.CharField(max_length=40, choices=DOCUMENT_TYPES)
    upload        = models.FileField(upload_to=_document_upload_path)
    original_filename = models.CharField(max_length=255)
    file_size_bytes   = models.PositiveIntegerField(default=0)

    status           = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    version          = models.PositiveSmallIntegerField(
        default=1,
        help_text='Increments each time the applicant re-uploads this document type.',
    )
    rejection_reason = models.CharField(max_length=300, blank=True)

    uploaded_at  = models.DateTimeField(auto_now_add=True)
    reviewed_at  = models.DateTimeField(null=True, blank=True)
    reviewed_by  = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        ordering = ['application', 'document_type', '-version']
        indexes = [
            models.Index(fields=['application', 'document_type', 'status']),
        ]
        verbose_name = 'Application Document'
        verbose_name_plural = 'Application Documents'

    def __str__(self):
        return (
            f'{self.application.reference_number} — '
            f'{self.get_document_type_display()} v{self.version}'
        )

    def approve(self, reviewed_by):
        """Mark this document as approved."""
        self.status = self.STATUS_APPROVED
        self.reviewed_by = reviewed_by
        self.reviewed_at = timezone.now()
        self.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    def reject(self, reviewed_by, reason: str):
        """Mark this document as rejected and record the reason."""
        self.status = self.STATUS_REJECTED
        self.rejection_reason = reason
        self.reviewed_by = reviewed_by
        self.reviewed_at = timezone.now()
        self.save(update_fields=['status', 'rejection_reason', 'reviewed_by', 'reviewed_at'])

    def supersede(self):
        """Mark this version as superseded when a newer upload arrives."""
        self.status = self.STATUS_SUPERSEDED
        self.save(update_fields=['status'])


# ==================== CONSENT RECORDS ====================
#consent 2
class ConsentRecord(models.Model):
    """
    Append-only record of a parent/guardian digitally consenting to a document.
    Captures IP, user-agent, timestamp, and document version for ECTA compliance.
    Never updated — only new records are added.
    """

    CONSENT_FEE_AGREEMENT  = 'fee_agreement'
    CONSENT_CODE_OF_CONDUCT = 'code_of_conduct'
    CONSENT_POPIA           = 'popia_consent'
    CONSENT_MEDICAL         = 'medical_treatment'
    CONSENT_PHOTOGRAPHY     = 'photography'

    CONSENT_TYPES = [
        (CONSENT_FEE_AGREEMENT,   'School Fee Agreement'),
        (CONSENT_CODE_OF_CONDUCT, 'Code of Conduct'),
        (CONSENT_POPIA,           'POPIA Data Consent'),
        (CONSENT_MEDICAL,         'Medical Treatment Authorisation'),
        (CONSENT_PHOTOGRAPHY,     'Photography and Media Permission'),
    ]

    application    = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name='consent_records',
    )
    consent_type   = models.CharField(max_length=30, choices=CONSENT_TYPES)
    # Version of the document shown — changed when school updates policy
    document_version = models.CharField(
        max_length=20,
        help_text='e.g. "2026-v1". Update when policy changes.',
    )
    # SHA-256 hash of the document text shown at time of consent
    document_hash  = models.CharField(max_length=64, blank=True)

    agreed    = models.BooleanField(default=True)
    agreed_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ['application', 'agreed_at']
        verbose_name = 'Consent Record'
        verbose_name_plural = 'Consent Records'

    def __str__(self):
        return (
            f'{self.application.reference_number} — '
            f'{self.get_consent_type_display()} '
            f'({self.agreed_at.strftime("%d %b %Y")})'
        )
    # Version of the document shown — changed when school updates policy
    document_version = models.CharField(
        max_length=20,
        help_text='e.g. "2026-v1". Update when policy changes.',
    )
    # SHA-256 hash of the document text shown at time of consent
    document_hash  = models.CharField(max_length=64, blank=True)

    agreed    = models.BooleanField(default=True)
    agreed_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ['agreed_at']
        verbose_name = 'Consent Record'
        verbose_name_plural = 'Consent Records'
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(application__isnull=False, reenrollment__isnull=True) |
                    models.Q(application__isnull=True, reenrollment__isnull=False)
                ),
                name='consent_record_single_parent',
            )
        ]

    def __str__(self):
        if self.application:
            ref = self.application.reference_number
        elif self.reenrollment:
            ref = f'Reenroll-{self.reenrollment.pk}'
        else:
            ref = 'Unknown'
        
        return (
            f'{ref} — '
            f'{self.get_consent_type_display()} '
            f'({self.agreed_at.strftime("%d %b %Y")})'
        )
