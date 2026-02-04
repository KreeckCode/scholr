from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    """
    Custom user model for Judea Harvest CAS
    Extends Django's AbstractUser to support role-based access and African context needs
    Phone numbers are primary contact method since many pastors lack email
    """
    
    # Role choices for the system
    STUDENT = 'student'
    DEAN = 'dean'
    ADMIN = 'admin'
    
    ROLE_CHOICES = [
        (STUDENT, 'Student (Pastor)'),
        (DEAN, 'Dean (Facilitator)'),
        (ADMIN, 'System Administrator'),
    ]
    
    # Phone validator for international format
    phone_regex = RegexValidator(
        regex=r'^\+?1?\d{9,15}$',
        message="Phone must be in format: '+999999999'. Up to 15 digits allowed."
    )
    
    # Core fields
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default=STUDENT,
        db_index=True,
        help_text="User's role in the system determines permissions"
    )
    
    phone_number = models.CharField(
        validators=[phone_regex],
        max_length=17,
        unique=True,
        help_text="Primary contact number - required for all users"
    )
    
    # Email is optional in African rural context
    email = models.EmailField(
        _('email address'),
        blank=True,
        null=True,
        help_text="Email is optional - many pastors don't have regular access"
    )
    
    # Account status fields
    is_verified = models.BooleanField(
        default=False,
        help_text="Whether dean/admin has verified this account"
    )
    
    verified_by = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_users',
        help_text="Admin/Dean who verified this account"
    )
    
    verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the account was verified"
    )
    
    # Tracking fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_sync = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time this user's data was synced from offline device"
    )
    
    class Meta:
        db_table = 'users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['role', 'is_verified']),
            models.Index(fields=['phone_number']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"
    
    def get_full_name(self):
        """
        Return full name or username if name not set
        Handles cases where pastors may only provide one name
        """
        full_name = f"{self.first_name} {self.last_name}".strip()
        return full_name if full_name else self.username
    
    def can_verify_users(self):
        """Check if user can verify other accounts"""
        return self.role in [self.DEAN, self.ADMIN] and self.is_active
    
    def can_manage_content(self):
        """Check if user can upload/manage course content"""
        return self.role == self.ADMIN and self.is_active
    
    def get_students(self):
        """
        For deans: get students under their supervision
        For admins: get all students
        """
        if self.role == self.DEAN:
            # Will be filtered by region once profile relationships are set up
            return User.objects.filter(
                role=self.STUDENT,
                profile__region=self.profile.region
            ).select_related('profile')
        elif self.role == self.ADMIN:
            return User.objects.filter(role=self.STUDENT).select_related('profile')
        return User.objects.none()


class UserProfile(models.Model):
    """
    Extended profile information for users
    Stores demographic and ministry-related data for analytics and reporting
    One-to-one relationship with User model
    """
    
    # Education level choices
    EDUCATION_CHOICES = [
        ('none', 'No Formal Education'),
        ('primary', 'Primary Education'),
        ('secondary', 'Secondary Education'),
        ('certificate', 'Certificate/Diploma'),
        ('undergraduate', 'Undergraduate Degree'),
        ('postgraduate', 'Postgraduate Degree'),
        ('theological', 'Theological Training'),
    ]
    
    # Church size categories
    CHURCH_SIZE_CHOICES = [
        ('small', 'Small (1-50 members)'),
        ('medium', 'Medium (51-200 members)'),
        ('large', 'Large (201-500 members)'),
        ('mega', 'Mega Church (500+ members)'),
    ]
    
    # Core relationship
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile',
        primary_key=True
    )
    
    # Geographic information - critical for offline deployment and analytics
    country = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Country where pastor ministers"
    )
    
    region = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Region/Province/State within country"
    )
    
    village_town = models.CharField(
        max_length=100,
        blank=True,
        help_text="Village or town name"
    )
    
    # Ministry information
    church_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Name of church or ministry"
    )
    
    church_affiliation = models.CharField(
        max_length=200,
        blank=True,
        help_text="Denomination or network affiliation"
    )
    
    church_size = models.CharField(
        max_length=20,
        choices=CHURCH_SIZE_CHOICES,
        blank=True,
        help_text="Approximate size of congregation"
    )
    
    years_in_ministry = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Total years serving in ministry"
    )
    
    position_title = models.CharField(
        max_length=100,
        blank=True,
        help_text="Current ministry position (e.g., Senior Pastor, Youth Pastor)"
    )
    
    # Personal information
    date_of_birth = models.DateField(
        null=True,
        blank=True,
        help_text="Used for age analytics, not displayed publicly"
    )
    
    education_level = models.CharField(
        max_length=20,
        choices=EDUCATION_CHOICES,
        blank=True,
        help_text="Highest level of formal education completed"
    )
    
    languages_spoken = models.CharField(
        max_length=200,
        blank=True,
        help_text="Comma-separated list of languages (e.g., English, Swahili, French)"
    )
    
    # Additional contact
    whatsapp_number = models.CharField(
        max_length=17,
        blank=True,
        help_text="WhatsApp number if different from phone number"
    )
    
    # Profile completion and engagement
    profile_photo = models.ImageField(
        upload_to='profile_photos/%Y/%m/',
        blank=True,
        null=True,
        help_text="Optional profile photo"
    )
    
    bio = models.TextField(
        blank=True,
        max_length=500,
        help_text="Brief biography or ministry background"
    )
    
    # Administrative fields
    registration_notes = models.TextField(
        blank=True,
        help_text="Internal notes from dean/admin during verification"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'user_profiles'
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'
        indexes = [
            models.Index(fields=['country', 'region']),
            models.Index(fields=['church_affiliation']),
        ]
    
    def __str__(self):
        return f"Profile: {self.user.get_full_name()}"
    
    def get_age(self):
        """Calculate age from date of birth"""
        if not self.date_of_birth:
            return None
        from datetime import date
        today = date.today()
        return today.year - self.date_of_birth.year - (
            (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
        )
    
    def get_languages_list(self):
        """Return languages as a list"""
        if not self.languages_spoken:
            return []
        return [lang.strip() for lang in self.languages_spoken.split(',')]
    
    def is_profile_complete(self):
        """
        Check if profile has minimum required information
        Useful for prompting users to complete registration
        """
        required_fields = [
            self.country,
            self.region,
            self.church_name,
            self.education_level,
        ]
        return all(required_fields)


class LoginAttempt(models.Model):
    """
    Track login attempts for security monitoring
    Helps identify brute force attacks or suspicious activity
    Important for offline-sync security where devices may be shared
    """
    
    username = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=17, blank=True)
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField(blank=True)
    success = models.BooleanField(default=False)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'login_attempts'
        verbose_name = 'Login Attempt'
        verbose_name_plural = 'Login Attempts'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['username', 'timestamp']),
            models.Index(fields=['ip_address', 'timestamp']),
            models.Index(fields=['success', 'timestamp']),
        ]
    
    def __str__(self):
        status = "Success" if self.success else "Failed"
        return f"{status}: {self.username} at {self.timestamp}"