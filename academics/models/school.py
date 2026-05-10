from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator


class School(models.Model):
    """
    Single school profile for this deployment.
    Singleton - one record only. Each school gets its own Scholr instance
    on its own subdomain, so there's no need for multi-tenancy here.
    """

    PHASE_PRIMARY = 'primary'
    PHASE_SECONDARY = 'secondary'
    PHASE_COMBINED = 'combined'
    PHASE_SPECIAL = 'special'

    PHASE_CHOICES = [
        (PHASE_PRIMARY, 'Primary School (Grade R - 7)'),
        (PHASE_SECONDARY, 'Secondary School (Grade 8 - 12)'),
        (PHASE_COMBINED, 'Combined School (Grade R - 12)'),
        (PHASE_SPECIAL, 'Special Needs School'),
    ]

    CURRICULUM_CAPS = 'caps'
    CURRICULUM_IEB = 'ieb'
    CURRICULUM_CAMBRIDGE = 'cambridge'
    CURRICULUM_IB = 'ib'
    CURRICULUM_CUSTOM = 'custom'

    CURRICULUM_CHOICES = [
        (CURRICULUM_CAPS, 'CAPS (DBE)'),
        (CURRICULUM_IEB, 'IEB'),
        (CURRICULUM_CAMBRIDGE, 'Cambridge (IGCSE/AS/A-Level)'),
        (CURRICULUM_IB, 'International Baccalaureate'),
        (CURRICULUM_CUSTOM, 'Custom Curriculum'),
    ]

    PROVINCE_CHOICES = [
        ('EC', 'Eastern Cape'),
        ('FS', 'Free State'),
        ('GP', 'Gauteng'),
        ('KZN', 'KwaZulu-Natal'),
        ('LP', 'Limpopo'),
        ('MP', 'Mpumalanga'),
        ('NC', 'Northern Cape'),
        ('NW', 'North West'),
        ('WC', 'Western Cape'),
    ]

    QUINTILE_CHOICES = [(i, f'Quintile {i}') for i in range(1, 6)]

    # Identity
    name = models.CharField(
        max_length=200,
        help_text="Full official school name as registered with DBE"
    )
    emis_number = models.CharField(
        max_length=20,
        unique=True,
        help_text="DBE EMIS number - unique government identifier for this school"
    )
    curriculum = models.CharField(
        max_length=20,
        choices=CURRICULUM_CHOICES,
        default=CURRICULUM_CAPS,
        help_text="Primary curriculum followed - affects mark calculations and report templates"
    )

    # Classification
    phase = models.CharField(
        max_length=20,
        choices=PHASE_CHOICES,
        help_text="School phase determines which grade levels are available"
    )
    quintile = models.PositiveSmallIntegerField(
        choices=QUINTILE_CHOICES,
        null=True,
        blank=True,
        help_text="Fee quintile classification (1=poorest, 5=wealthiest) - public schools only"
    )
    is_section_21 = models.BooleanField(
        default=False,
        help_text="Section 21 schools have financial autonomy to manage their own budgets"
    )
    is_independent = models.BooleanField(
        default=False,
        help_text="Private/independent school vs DBE public school"
    )

    # Contact details
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    fax = models.CharField(max_length=20, blank=True)

    # Physical address
    physical_address = models.TextField(help_text="Street address for deliveries and official correspondence")
    postal_address = models.TextField(blank=True, help_text="Leave blank if same as physical address")
    province = models.CharField(max_length=3, choices=PROVINCE_CHOICES)
    city_town = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=10, blank=True)

    # Principal - FK to User with principal role
    principal = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='principal_of',
        limit_choices_to={'role': 'principal'},
        help_text="The school principal - must have the Principal role"
    )

    # Branding - used in report cards, parent portal, email templates
    logo = models.ImageField(
        upload_to='school/logos/',
        blank=True,
        null=True,
        help_text="School crest or logo - used on report cards and portal header"
    )
    primary_colour = models.CharField(
        max_length=7,
        default='#1e40af',
        help_text="Primary brand colour in hex (e.g. #1e40af)"
    )
    secondary_colour = models.CharField(
        max_length=7,
        default='#ffffff',
        help_text="Secondary brand colour in hex"
    )

    # Meta
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'school'
        verbose_name = 'School'
        # Intentionally singular - signals the singleton intent in admin
        verbose_name_plural = 'School Profile'

    def __str__(self):
        return self.name

    def clean(self):
        """Enforce the singleton constraint - one school per deployment."""
        if not self.pk and School.objects.exists():
            raise ValidationError(
                "Only one school profile is allowed per deployment. "
                "Edit the existing profile instead."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @classmethod
    def get_current(cls):
        """Fetch the single school instance for this deployment."""
        return cls.objects.select_related('principal').first()

    def get_address_display(self):
        """Return formatted single-line address."""
        parts = [self.physical_address, self.city_town, self.province, self.postal_code]
        return ', '.join(p for p in parts if p)


class AcademicYear(models.Model):
    """
    Academic year for the school. SA schools run January to December
    aligned to the calendar year - not a rolling 12-month period.
    """

    year = models.PositiveIntegerField(
        unique=True,
        help_text="Calendar year e.g. 2026"
    )
    start_date = models.DateField(help_text="First day of school for this year")
    end_date = models.DateField(help_text="Last day of school for this year")
    is_current = models.BooleanField(
        default=False,
        db_index=True,
        help_text="The active year - all mark capture and attendance references this"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'academic_years'
        verbose_name = 'Academic Year'
        verbose_name_plural = 'Academic Years'
        ordering = ['-year']

    def __str__(self):
        return str(self.year)

    def save(self, *args, **kwargs):
        """I'm enforcing a single current year - setting this year as current
        clears the flag on all others first."""
        if self.is_current:
            AcademicYear.objects.exclude(pk=self.pk).update(is_current=False)
        super().save(*args, **kwargs)

    @classmethod
    def get_current(cls):
        """Fetch the current academic year - returns None if not configured."""
        return cls.objects.filter(is_current=True).first()

    def get_terms(self):
        """Return all 4 terms for this year ordered by term number."""
        return self.terms.order_by('term_number')


class Term(models.Model):
    """
    One of 4 terms in an academic year.
    DBE publishes official term dates annually - these are entered here by admin.
    The current term controls which marks window is open for capture.
    """

    TERM_CHOICES = [
        (1, 'Term 1'),
        (2, 'Term 2'),
        (3, 'Term 3'),
        (4, 'Term 4'),
    ]

    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name='terms'
    )
    term_number = models.PositiveSmallIntegerField(
        choices=TERM_CHOICES,
        help_text="Term 1-4 within the academic year"
    )
    start_date = models.DateField()
    end_date = models.DateField()

    # Mark capture controls
    marks_open = models.BooleanField(
        default=False,
        help_text="When True, teachers can capture marks for this term"
    )
    marks_deadline = models.DateField(
        null=True,
        blank=True,
        help_text="Submission deadline - shown as warning to teachers approaching this date"
    )

    # Report window controls
    reports_released = models.BooleanField(
        default=False,
        help_text="When True, parents can view reports for this term in the portal"
    )

    is_current = models.BooleanField(
        default=False,
        db_index=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'terms'
        verbose_name = 'Term'
        verbose_name_plural = 'Terms'
        unique_together = [['academic_year', 'term_number']]
        ordering = ['academic_year', 'term_number']
        indexes = [
            models.Index(fields=['is_current'], name='idx_term_current'),
            models.Index(fields=['academic_year', 'term_number'], name='idx_term_year_number'),
        ]

    def __str__(self):
        return f"Term {self.term_number} ({self.academic_year.year})"

    def save(self, *args, **kwargs):
        """Setting a term as current clears is_current on all other terms."""
        if self.is_current:
            Term.objects.exclude(pk=self.pk).update(is_current=False)
        super().save(*args, **kwargs)

    @classmethod
    def get_current(cls):
        """Fetch the active term. Returns None if between terms or not configured."""
        return cls.objects.filter(is_current=True).select_related('academic_year').first()

    def get_label(self):
        """Short label for UI display e.g. 'T1 2026'."""
        return f"T{self.term_number} {self.academic_year.year}"

    def is_marks_open(self):
        """Check if marks can be captured - used in template guards."""
        return self.marks_open and self.is_current


class Department(models.Model):
    """
    Subject department within the school.
    e.g. Mathematics, Languages, Natural Sciences, Commerce, Life Orientation.
    Each department has an HOD who manages subject teachers and marks moderation.

    Note: This is a school administrative department, completely separate from
    the LMS Subject model in accounts which is used for course cataloguing.
    """

    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(
        max_length=10,
        unique=True,
        help_text="Short identifier used in reports e.g. MATH, LANG, NS"
    )
    description = models.TextField(blank=True)

    hod = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='department_hod_of',
        limit_choices_to={'role': 'hod'},
        help_text="Head of Department - must have the HOD role assigned"
    )

    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.IntegerField(
        default=0,
        help_text="Display order in admin lists and reports"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'departments'
        verbose_name = 'Department'
        verbose_name_plural = 'Departments'
        ordering = ['sort_order', 'name']
        indexes = [
            models.Index(fields=['is_active', 'sort_order'], name='idx_dept_active_sort'),
        ]

    def __str__(self):
        return self.name

    def get_teachers(self):
        """Return active teachers assigned to this department."""
        return self.staff.filter(is_active=True).select_related('user')

    def has_hod(self):
        """Quick check used in admin warnings."""
        return self.hod is not None


class SchoolStaff(models.Model):
    """
    Links a User account to the school as a staff member.
    Stores SA-specific employment details (PERSAL, SACE) alongside
    the user's role which is managed on the User model itself.

    One staff record per user - a teacher cannot be both a teacher and an admin clerk.
    Role changes are made on User.role; this model holds the employment metadata.
    """

    EMPLOYMENT_PERMANENT = 'permanent'
    EMPLOYMENT_TEMPORARY = 'temporary'
    EMPLOYMENT_SUBSTITUTE = 'substitute'
    EMPLOYMENT_CONTRACT = 'contract'

    EMPLOYMENT_CHOICES = [
        (EMPLOYMENT_PERMANENT, 'Permanent'),
        (EMPLOYMENT_TEMPORARY, 'Temporary'),
        (EMPLOYMENT_SUBSTITUTE, 'Substitute'),
        (EMPLOYMENT_CONTRACT, 'Contract'),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='staff_profile',
        help_text="Staff member's user account - role is set on the User model"
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='staff',
        help_text="Primary department - teachers can be allocated to subjects across departments"
    )

    # SA government staff identifiers
    persal_number = models.CharField(
        max_length=20,
        blank=True,
        help_text="PERSAL payroll number - required for public school government employees"
    )
    sace_number = models.CharField(
        max_length=20,
        blank=True,
        help_text="SACE registration number - legally required to teach in SA"
    )

    # Employment
    employment_type = models.CharField(
        max_length=20,
        choices=EMPLOYMENT_CHOICES,
        default=EMPLOYMENT_PERMANENT
    )
    date_joined = models.DateField(
        null=True,
        blank=True,
        help_text="Date this staff member joined the school"
    )

    # Qualifications (stored here, not on User, as they're school-context specific)
    highest_qualification = models.CharField(
        max_length=200,
        blank=True,
        help_text="e.g. BEd (Hons), PGCE, M.Ed"
    )
    subjects_qualified = models.TextField(
        blank=True,
        help_text="Free text - subjects this teacher is formally qualified to teach"
    )

    is_active = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'school_staff'
        verbose_name = 'School Staff Member'
        verbose_name_plural = 'School Staff'
        indexes = [
            models.Index(fields=['is_active'], name='idx_staff_active'),
            models.Index(fields=['department', 'is_active'], name='idx_staff_dept_active'),
            models.Index(fields=['employment_type'], name='idx_staff_employment'),
        ]

    def __str__(self):
        return f"{self.user.get_full_name()} ({self.user.get_role_display()})"

    def get_role(self):
        """Proxy to the user role - single source of truth stays on User."""
        return self.user.role

    def is_teacher(self):
        return self.user.role == self.user.TEACHER

    def is_hod(self):
        return self.user.role == self.user.HOD

    def is_principal(self):
        return self.user.role == self.user.PRINCIPAL