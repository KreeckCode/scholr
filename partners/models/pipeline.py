from django.conf import settings
from django.db import models
from django.utils import timezone

from .base import TimeStampedModel
from .organisation import PartnerOrganisation


class Stage(models.TextChoices):
    ENQUIRY = 'enquiry', 'Enquiry'
    REVIEW = 'review', 'Review'
    INTRO_CALL = 'intro_call', 'Intro call'
    STRATEGY = 'strategy', 'Partnership strategy'
    ADMIN_TRAINING = 'admin_training', 'Admin training (METP 100a)'
    FACILITATOR_TRAINING = 'facilitator_training', 'Facilitator training (METP 100b)'
    LAUNCHED = 'launched', 'Launched'
    PAUSED = 'paused', 'Paused'
    LOST = 'lost', 'Lost'


# I'm keeping METP's own launch order here; paused and lost sit outside it
FORWARD_STAGES = (
    Stage.ENQUIRY,
    Stage.REVIEW,
    Stage.INTRO_CALL,
    Stage.STRATEGY,
    Stage.ADMIN_TRAINING,
    Stage.FACILITATOR_TRAINING,
    Stage.LAUNCHED,
)


class Partnership(TimeStampedModel):
    """Where a partner organisation sits on the road to launching METP."""

    class Source(models.TextChoices):
        WEB_ENQUIRY = 'web_enquiry', 'Website enquiry'
        REFERRAL = 'referral', 'Referral'
        EVENT = 'event', 'Event'
        STAFF = 'staff', 'Added by staff'

    organisation = models.OneToOneField(
        PartnerOrganisation, on_delete=models.CASCADE, related_name='partnership',
    )
    stage = models.CharField(max_length=30, choices=Stage.choices, default=Stage.ENQUIRY, db_index=True)
    stage_before_pause = models.CharField(max_length=30, choices=Stage.choices, blank=True)
    stage_changed_at = models.DateTimeField(default=timezone.now)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='owned_partnerships',
        help_text='METP staff member responsible for the next step.',
    )
    next_action = models.CharField(max_length=200, blank=True)
    next_action_due = models.DateField(null=True, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.STAFF)
    expected_participants = models.PositiveIntegerField(null=True, blank=True)
    lost_reason = models.TextField(blank=True)

    class Meta:
        db_table = 'partnerships'
        ordering = ['-stage_changed_at']
        indexes = [
            models.Index(fields=['stage', 'next_action_due'], name='idx_partnership_stage_due'),
        ]

    def __str__(self):
        return f'{self.organisation} ({self.get_stage_display()})'

    @property
    def stage_index(self):
        """Position on the forward path, or None while paused or lost."""
        try:
            return FORWARD_STAGES.index(self.stage)
        except ValueError:
            return None

    def is_overdue(self, today=None):
        today = today or timezone.localdate()
        return bool(self.next_action_due and self.next_action_due < today)


class PartnershipEvent(models.Model):
    """Append-only history of a partnership: stage moves, notes, calls, documents."""

    class Kind(models.TextChoices):
        STAGE = 'stage', 'Stage change'
        NOTE = 'note', 'Note'
        CALL = 'call', 'Call'
        EMAIL = 'email', 'Email'
        DOCUMENT = 'document', 'Document'
        ENQUIRY = 'enquiry', 'Website enquiry'
        LICENCE = 'licence', 'Licence'

    partnership = models.ForeignKey(Partnership, on_delete=models.CASCADE, related_name='events')
    kind = models.CharField(max_length=20, choices=Kind.choices)
    from_stage = models.CharField(max_length=30, choices=Stage.choices, blank=True)
    to_stage = models.CharField(max_length=30, choices=Stage.choices, blank=True)
    body = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'partnership_events'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.get_kind_display()} on {self.partnership.organisation}'
