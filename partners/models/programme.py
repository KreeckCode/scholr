from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from .base import TimeStampedModel


class ProgrammeCourse(TimeStampedModel):
    """
    One of the twelve classroom courses (METP 101 to 112).
    Deliberately separate from integration.Course: saving that model creates products,
    auto-enrols students and queues Moodle syncs, none of which a classroom course should do.
    """

    code = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=200)
    area = models.CharField(max_length=200, blank=True)
    sequence = models.PositiveSmallIntegerField(unique=True)
    classroom_hours = models.DecimalField(
        max_digits=5, decimal_places=1, default=Decimal('12.0'), validators=[MinValueValidator(Decimal('0.5'))],
    )
    online_course = models.ForeignKey(
        'integration.Course', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='programme_courses',
        help_text='The Moodle version of this course, for blended cohorts.',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'programme_courses'
        ordering = ['sequence']

    def __str__(self):
        return f'{self.code}: {self.title}'
