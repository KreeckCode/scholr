from django.db import models
from django.core.validators import URLValidator
from django.utils import timezone
from accounts.models import User
import json


class MoodleInstance(models.Model):
    """
    Stores Moodle server configuration
    Single instance for now, expandable to multiple instances later
    """
    
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Identifier for this Moodle instance (e.g., 'Local Server', 'Cloud Backup')"
    )
    
    base_url = models.URLField(
        validators=[URLValidator()],
        help_text="Full URL to Moodle installation (e.g., http://localhost:8888/moodle501)"
    )
    
    ws_token = models.CharField(
        max_length=255,
        help_text="Web service token from Moodle"
    )
    
    ws_service_name = models.CharField(
        max_length=100,
        default='judeacas',
        help_text="Name of the external service in Moodle"
    )
    
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this instance is currently in use"
    )
    
    is_primary = models.BooleanField(
        default=True,
        help_text="Primary instance to use first"
    )
    
    # Health monitoring
    last_health_check = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time connection was verified"
    )
    
    health_status = models.CharField(
        max_length=20,
        choices=[
            ('healthy', 'Healthy'),
            ('degraded', 'Degraded'),
            ('offline', 'Offline'),
            ('unknown', 'Unknown'),
        ],
        default='unknown'
    )
    
    last_error = models.TextField(
        blank=True,
        help_text="Most recent error message"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'moodle_instances'
        verbose_name = 'Moodle Instance'
        verbose_name_plural = 'Moodle Instances'
        ordering = ['-is_primary', 'name']
    
    def __str__(self):
        status = f"[{self.health_status.upper()}]" if self.health_status != 'unknown' else ''
        return f"{self.name} {status}"
    
    def mark_healthy(self):
        """Update health status to healthy"""
        self.health_status = 'healthy'
        self.last_health_check = timezone.now()
        self.last_error = ''
        self.save(update_fields=['health_status', 'last_health_check', 'last_error'])
    
    def mark_offline(self, error_message=''):
        """Update health status to offline"""
        self.health_status = 'offline'
        self.last_health_check = timezone.now()
        self.last_error = error_message
        self.save(update_fields=['health_status', 'last_health_check', 'last_error'])


class CourseCategory(models.Model):
    """
    Course categories matching the 5 disciplines from scholr
    These map to Moodle course categories
    """
    
    DISCIPLINE_CHOICES = [
        ('Higher Education', 'Higher Education'),
        ('Science', 'Science'),
        ('Arts', 'Arts'),
        ('Social Sciences', 'Social Sciences'),
        ('Reading Writing', 'Reading Writing'),
        ('Foundation Phase', 'Foundation Phase'),
        ('Secondary Education', 'Secondary Education'),
        ('primary', 'Primary'),
        ('High School', 'High School'),
        ('history', 'History'),
        ('theology', 'Theology'),
    ]
    
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Category name"
    )
    
    discipline = models.CharField(
        max_length=50,
        choices=DISCIPLINE_CHOICES,
        unique=True,
        db_index=True,
        help_text="Which of the 5 core disciplines this represents"
    )
    
    description = models.TextField(
        blank=True,
        help_text="Category description"
    )
    
    moodle_category_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="Corresponding category ID in Moodle"
    )
    
    sort_order = models.IntegerField(
        default=0,
        help_text="Display order"
    )
    
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this category is currently in use"
    )
    
    last_synced = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time this category was synced to Moodle"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'course_categories'
        verbose_name = 'Course Category'
        verbose_name_plural = 'Course Categories'
        ordering = ['sort_order', 'name']
    
    def __str__(self):
        return f"{self.name} ({self.get_discipline_display()})"


class Course(models.Model):
    """
    Courses managed by CAS and synced to Moodle
    Content editing happens on Moodle, but CAS controls metadata
    """
    
    SYNC_STATUS_CHOICES = [
        ('pending', 'Pending Sync'),
        ('synced', 'Synced'),
        ('modified', 'Modified (Needs Sync)'),
        ('failed', 'Sync Failed'),
    ]
    
    # Core course information
    title = models.CharField(
        max_length=255,
        help_text="Course title"
    )
    
    short_name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Unique short identifier (e.g., BIB101)"
    )
    
    code = models.CharField(
        max_length=50,
        blank=True,
        help_text="Course code for reference"
    )
    
    category = models.ForeignKey(
        CourseCategory,
        on_delete=models.PROTECT,
        related_name='courses',
        help_text="Course category/discipline"
    )
    
    summary = models.TextField(
        blank=True,
        help_text="Course description/summary"
    )
    
    # Dates and availability
    start_date = models.DateField(
        null=True,
        blank=True,
        help_text="Course start date"
    )
    
    end_date = models.DateField(
        null=True,
        blank=True,
        help_text="Course end date"
    )
    
    visible = models.BooleanField(
        default=True,
        help_text="Whether course is visible to students"
    )
    
    # Enrollment settings
    auto_enroll_enabled = models.BooleanField(
        default=True,
        help_text="Automatically enroll verified students in this course"
    )
    
    enrollment_capacity = models.IntegerField(
        null=True,
        blank=True,
        help_text="Maximum number of students (null = unlimited)"
    )
    
    # Moodle integration
    moodle_course_id = models.IntegerField(
        null=True,
        blank=True,
        unique=True,
        help_text="Corresponding course ID in Moodle"
    )
    
    sync_status = models.CharField(
        max_length=20,
        choices=SYNC_STATUS_CHOICES,
        default='pending',
        db_index=True,
        help_text="Current synchronization status"
    )
    
    last_synced = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last successful sync to Moodle"
    )
    
    sync_error = models.TextField(
        blank=True,
        help_text="Most recent sync error if any"
    )
    
    # Metadata
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='courses_created',
        help_text="User who created this course"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'courses'
        verbose_name = 'Course'
        verbose_name_plural = 'Courses'
        ordering = ['category', 'title']
        indexes = [
            models.Index(fields=['short_name'], name='idx_course_shortname'),
            models.Index(fields=['category'], name='idx_course_category'),
            models.Index(fields=['moodle_course_id'], name='idx_course_moodle_id'),
            models.Index(fields=['sync_status'], name='idx_course_sync_status'),
            models.Index(fields=['visible'], name='idx_course_visible'), 
        ]
    
    def __str__(self):
        return f"{self.short_name}: {self.title}"
    
    def mark_modified(self):
        """Mark course as needing sync after changes"""
        if self.moodle_course_id and self.sync_status == 'synced':
            self.sync_status = 'modified'
            self.save(update_fields=['sync_status'])
    
    def mark_synced(self, moodle_id=None):
        """Mark course as successfully synced"""
        if moodle_id:
            self.moodle_course_id = moodle_id
        self.sync_status = 'synced'
        self.last_synced = timezone.now()
        self.sync_error = ''
        self.save(update_fields=['moodle_course_id', 'sync_status', 'last_synced', 'sync_error'])
    
    def mark_failed(self, error_message):
        """Mark course sync as failed"""
        self.sync_status = 'failed'
        self.sync_error = error_message
        self.save(update_fields=['sync_status', 'sync_error'])
    
    @property
    def needs_sync(self):
        """Check if course needs to be synced"""
        return self.sync_status in ['pending', 'modified', 'failed']
    
    @property
    def current_enrollment_count(self):
        """Get current number of enrolled students"""
        return self.enrollments.filter(is_active=True).count()


class MoodleUserMapping(models.Model):
    """
    Maps CAS users to their Moodle user IDs
    Tracks sync status for each user
    """
    
    SYNC_STATUS_CHOICES = [
        ('pending', 'Pending Sync'),
        ('synced', 'Synced'),
        ('modified', 'Modified (Needs Sync)'),
        ('failed', 'Sync Failed'),
    ]
    
    cas_user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='moodle_mapping',
        help_text="CAS user account"
    )
    
    moodle_user_id = models.IntegerField(
        null=True,
        blank=True,
        unique=True,
        help_text="User ID in Moodle"
    )
    
    moodle_username = models.CharField(
        max_length=100,
        unique=True,
        help_text="Username in Moodle (usually same as CAS username)"
    )
    
    sync_status = models.CharField(
        max_length=20,
        choices=SYNC_STATUS_CHOICES,
        default='pending',
        db_index=True
    )
    
    last_synced = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last successful sync to Moodle"
    )
    
    sync_error = models.TextField(
        blank=True,
        help_text="Most recent sync error"
    )
    
    # SSO token tracking
    last_sso_token = models.CharField(
        max_length=255,
        blank=True,
        help_text="Most recent SSO token generated"
    )
    
    last_sso_access = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time user accessed Moodle via SSO"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'moodle_user_mappings'
        verbose_name = 'Moodle User Mapping'
        verbose_name_plural = 'Moodle User Mappings'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['cas_user'], name='idx_moodle_user'),
            models.Index(fields=['moodle_user_id'], name='idx_moodle_id'),
            models.Index(fields=['sync_status'], name='idx_moodle_sync'),
        ]
    
    def __str__(self):
        return f"{self.cas_user.username} → Moodle ID: {self.moodle_user_id or 'Not synced'}"
    
    def mark_synced(self, moodle_id=None):
        """Mark user as successfully synced"""
        if moodle_id:
            self.moodle_user_id = moodle_id
        self.sync_status = 'synced'
        self.last_synced = timezone.now()
        self.sync_error = ''
        self.save(update_fields=['moodle_user_id', 'sync_status', 'last_synced', 'sync_error'])
    
    def mark_failed(self, error_message):
        """Mark user sync as failed"""
        self.sync_status = 'failed'
        self.sync_error = error_message
        self.save(update_fields=['sync_status', 'sync_error'])


class CourseEnrollment(models.Model):
    """
    Tracks student enrollments in courses
    Manages sync status to Moodle
    """
    
    SYNC_STATUS_CHOICES = [
        ('pending', 'Pending Sync'),
        ('synced', 'Synced'),
        ('failed', 'Sync Failed'),
    ]
    
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='course_enrollments',
        help_text="Enrolled student"
    )
    
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name='enrollments',
        help_text="Course enrolled in"
    )
    
    enrolled_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When enrollment was created on CAS"
    )
    
    is_active = models.BooleanField(
        default=True,
        help_text="Whether enrollment is currently active"
    )

    progress_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00,
        help_text="Course completion percentage (0-100)"
    )
    
    # Moodle integration
    moodle_enrollment_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="Enrollment ID in Moodle (if available)"
    )
    
    sync_status = models.CharField(
        max_length=20,
        choices=SYNC_STATUS_CHOICES,
        default='pending',
        db_index=True
    )
    
    last_synced = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last successful sync to Moodle"
    )
    
    sync_error = models.TextField(
        blank=True,
        help_text="Most recent sync error"
    )
    
    # Metadata
    enrolled_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='enrollments_created',
        help_text="User who created this enrollment (null = auto-enrolled)"
    )
    
    unenrolled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When student was unenrolled"
    )
    
    unenrolled_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='unenrollments_created',
        help_text="User who unenrolled the student"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'course_enrollments'
        verbose_name = 'Course Enrollment'
        verbose_name_plural = 'Course Enrollments'
        ordering = ['-enrolled_at']
        unique_together = [['user', 'course']]
        indexes = [
            models.Index(fields=['is_active'], name='idx_enrollment_active'),
            models.Index(fields=['sync_status'], name='idx_enrollment_sync_status'),
            models.Index(fields=['user'], name='idx_enrollment_user'),
            models.Index(fields=['course'], name='idx_enrollment_course'),
        ]
    
    def __str__(self):
        status = "✓" if self.is_active else "✗"
        return f"{status} {self.user.get_full_name()} → {self.course.short_name}"
    
    def mark_synced(self, moodle_enrollment_id=None):
        """Mark enrollment as successfully synced"""
        if moodle_enrollment_id:
            self.moodle_enrollment_id = moodle_enrollment_id
        self.sync_status = 'synced'
        self.last_synced = timezone.now()
        self.sync_error = ''
        self.save(update_fields=['moodle_enrollment_id', 'sync_status', 'last_synced', 'sync_error'])
    
    def mark_failed(self, error_message):
        """Mark enrollment sync as failed"""
        self.sync_status = 'failed'
        self.sync_error = error_message
        self.save(update_fields=['sync_status', 'sync_error'])
    
    def unenroll(self, unenrolled_by=None):
        """Unenroll student from course"""
        self.is_active = False
        self.unenrolled_at = timezone.now()
        self.unenrolled_by = unenrolled_by
        self.sync_status = 'pending'  # Needs to sync unenrollment to Moodle
        self.save(update_fields=['is_active', 'unenrolled_at', 'unenrolled_by', 'sync_status'])


class SyncLog(models.Model):
    """
    Comprehensive logging of all sync operations
    Tracks what was synced, when, and results
    """
    
    SYNC_TYPE_CHOICES = [
        ('users', 'Users Only'),
        ('courses', 'Courses Only'),
        ('enrollments', 'Enrollments Only'),
        ('categories', 'Categories Only'),
        ('full', 'Full Sync (All)'),
    ]
    
    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('partial', 'Partially Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]
    
    # Sync details
    sync_type = models.CharField(
        max_length=20,
        choices=SYNC_TYPE_CHOICES,
        db_index=True,
        help_text="Type of sync performed"
    )
    
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='queued',
        db_index=True,
        help_text="Current status of sync"
    )
    
    # Timing
    started_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When sync started"
    )
    
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When sync finished"
    )
    
    duration_seconds = models.IntegerField(
        null=True,
        blank=True,
        help_text="How long sync took"
    )
    
    # Results
    total_items = models.IntegerField(
        default=0,
        help_text="Total items to sync"
    )
    
    successful_items = models.IntegerField(
        default=0,
        help_text="Items successfully synced"
    )
    
    failed_items = models.IntegerField(
        default=0,
        help_text="Items that failed to sync"
    )
    
    skipped_items = models.IntegerField(
        default=0,
        help_text="Items skipped (already synced)"
    )
    
    # Detailed results stored as JSON
    details = models.JSONField(
        default=dict,
        blank=True,
        help_text="Detailed sync results and errors"
    )
    
    error_summary = models.TextField(
        blank=True,
        help_text="Summary of errors encountered"
    )
    
    # User who initiated
    initiated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='sync_logs',
        help_text="User who triggered this sync"
    )
    
    # Moodle instance used
    moodle_instance = models.ForeignKey(
        MoodleInstance,
        on_delete=models.SET_NULL,
        null=True,
        help_text="Which Moodle instance was synced to"
    )
    
    class Meta:
        db_table = 'sync_logs'
        verbose_name = 'Sync Log'
        verbose_name_plural = 'Sync Logs'
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['sync_type', 'status'], name='idx_synclog_type_status'),
            models.Index(fields=['started_at'], name='idx_synclog_started'),
            models.Index(fields=['status'], name='idx_synclog_status'),
        ]
    
    def __str__(self):
        return f"{self.get_sync_type_display()} - {self.status} ({self.started_at.strftime('%Y-%m-%d %H:%M')})"
    
    def mark_running(self):
        """Mark sync as currently running"""
        self.status = 'running'
        self.save(update_fields=['status'])
    
    def mark_completed(self):
        """Mark sync as successfully completed"""
        self.status = 'completed' if self.failed_items == 0 else 'partial'
        self.completed_at = timezone.now()
        self.duration_seconds = int((self.completed_at - self.started_at).total_seconds())
        self.save(update_fields=['status', 'completed_at', 'duration_seconds'])
    
    def mark_failed(self, error_message):
        """Mark sync as failed"""
        self.status = 'failed'
        self.completed_at = timezone.now()
        self.duration_seconds = int((self.completed_at - self.started_at).total_seconds())
        self.error_summary = error_message
        self.save(update_fields=['status', 'completed_at', 'duration_seconds', 'error_summary'])
    
    def add_detail(self, key, value):
        """Add a detail to the JSON details field"""
        self.details[key] = value
        self.save(update_fields=['details'])
    
    @property
    def success_rate(self):
        """Calculate success rate percentage"""
        if self.total_items == 0:
            return 0
        return (self.successful_items / self.total_items) * 100