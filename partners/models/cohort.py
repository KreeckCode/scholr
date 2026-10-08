from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from .base import CountryField, PublicIdModel, TimeStampedModel, phone_validator
from .location import TrainingLocation
from .programme import ProgrammeCourse


class Cohort(PublicIdModel, TimeStampedModel):
    """A class of participants moving through the programme together at one location."""

    class Delivery(models.TextChoices):
        CLASSROOM = 'classroom', 'Classroom only'
        BLENDED = 'blended', 'Classroom and online'

    class Pace(models.IntegerChoices):
        TWELVE_MONTHS = 12, '12 months (one course a month)'
        SIX_MONTHS = 6, '6 months (two courses a month)'
        THREE_MONTHS = 3, '3 months (one course a week)'

    class Status(models.TextChoices):
        PLANNED = 'planned', 'Planned'
        RUNNING = 'running', 'Running'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    location = models.ForeignKey(TrainingLocation, on_delete=models.PROTECT, related_name='cohorts')
    name = models.CharField(max_length=150)
    language = models.CharField(max_length=50, default='English')
    delivery = models.CharField(max_length=20, choices=Delivery.choices, default=Delivery.CLASSROOM)
    pace_months = models.PositiveSmallIntegerField(choices=Pace.choices, default=Pace.TWELVE_MONTHS)
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED, db_index=True)
    facilitators = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name='facilitated_cohorts',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'partner_cohorts'
        ordering = ['-starts_on', 'name']
        constraints = [
            models.UniqueConstraint(fields=['location', 'name'], name='unique_cohort_name_per_location'),
        ]

    def __str__(self):
        return f'{self.name} ({self.location})'

    @property
    def is_open(self):
        return self.status in (self.Status.PLANNED, self.Status.RUNNING)


class Participant(PublicIdModel, TimeStampedModel):
    """
    A person in training at a location.
    Not a User: classroom participants have no login and never sync to Moodle.
    The user link is set only when online access is granted.
    """

    home_location = models.ForeignKey(TrainingLocation, on_delete=models.PROTECT, related_name='participants')
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    # I'm leaving this non-unique because families and ministry teams often share one phone
    phone = models.CharField(max_length=20, blank=True, validators=[phone_validator], db_index=True)
    email = models.EmailField(blank=True)
    church_name = models.CharField(max_length=200, blank=True)
    position_title = models.CharField(max_length=100, blank=True)
    country = CountryField(blank=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='partner_participant',
    )
    client_uuid = models.UUIDField(
        null=True, blank=True, unique=True,
        help_text='Set when the participant was registered on an offline device.',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'partner_participants'
        ordering = ['last_name', 'first_name']
        indexes = [
            models.Index(fields=['home_location', 'last_name'], name='idx_participant_loc_name'),
        ]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'.strip()

    @property
    def guarded_name(self):
        """First name and initial, for packs and exports from sensitive locations."""
        initial = f' {self.last_name[:1]}.' if self.last_name else ''
        return f'{self.first_name}{initial}'

    def display_name_for(self, location):
        return self.guarded_name if location.hides_identities else self.full_name


class CohortMembership(PublicIdModel, TimeStampedModel):
    """A participant's place in a cohort, and the record online access hangs off."""

    class Status(models.TextChoices):
        ENROLLED = 'enrolled', 'Enrolled'
        WITHDRAWN = 'withdrawn', 'Withdrawn'
        COMPLETED = 'completed', 'Completed'

    cohort = models.ForeignKey(Cohort, on_delete=models.CASCADE, related_name='memberships')
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name='cohort_memberships')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ENROLLED, db_index=True)
    joined_on = models.DateField(default=timezone.localdate)
    completed_on = models.DateField(null=True, blank=True)
    withdrawn_on = models.DateField(null=True, blank=True)
    access_order = models.ForeignKey(
        'accounts.Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        help_text='The licence-funded order that gave this participant online access.',
    )
    access_granted_at = models.DateTimeField(null=True, blank=True)
    access_granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    access_withdrawn_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'partner_cohort_memberships'
        ordering = ['participant__last_name', 'participant__first_name']
        constraints = [
            models.UniqueConstraint(fields=['cohort', 'participant'], name='unique_participant_per_cohort'),
        ]

    def __str__(self):
        return f'{self.participant} in {self.cohort.name}'

    @property
    def has_online_access(self):
        return bool(self.access_order_id and not self.access_withdrawn_at)


class CohortSession(PublicIdModel, TimeStampedModel):
    """One class meeting for one course."""

    class Status(models.TextChoices):
        PLANNED = 'planned', 'Planned'
        HELD = 'held', 'Held'
        CANCELLED = 'cancelled', 'Cancelled'

    cohort = models.ForeignKey(Cohort, on_delete=models.CASCADE, related_name='sessions')
    course = models.ForeignKey(ProgrammeCourse, on_delete=models.PROTECT, related_name='sessions')
    session_date = models.DateField(db_index=True)
    hours = models.DecimalField(
        max_digits=4, decimal_places=1, default=Decimal('3.0'),
        validators=[MinValueValidator(Decimal('0.5')), MaxValueValidator(Decimal('12.0'))],
    )
    facilitator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'partner_cohort_sessions'
        ordering = ['session_date', 'id']
        indexes = [
            models.Index(fields=['cohort', 'session_date'], name='idx_session_cohort_date'),
        ]

    def __str__(self):
        return f'{self.course.code} on {self.session_date:%d %b %Y}'
