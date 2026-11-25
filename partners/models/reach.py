from django.conf import settings
from django.db import models
from django.db.models import Q

from .base import CountryField
from .organisation import PartnerOrganisation


class StaffAssignment(models.Model):
    """
    One piece of a network staff member's reach: either a whole country or one partner.

    Hub administrators use country rows, plus partner rows for exceptions such as a partner
    across the border. Ambassadors only ever use partner rows; any country row they have
    left over from a previous role is ignored.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='network_assignments',
    )
    country = CountryField(blank=True, default='')
    organisation = models.ForeignKey(
        PartnerOrganisation, on_delete=models.CASCADE, null=True, blank=True, related_name='staff_assignments',
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    (~Q(country='') & Q(organisation__isnull=True))
                    | (Q(country='') & Q(organisation__isnull=False))
                ),
                name='staff_assignment_country_or_partner',
            ),
            models.UniqueConstraint(
                fields=['user', 'country'], condition=~Q(country=''), name='unique_staff_country',
            ),
            models.UniqueConstraint(
                fields=['user', 'organisation'], condition=Q(organisation__isnull=False),
                name='unique_staff_organisation',
            ),
        ]

    def __str__(self):
        return f'{self.user}: {self.organisation or self.get_country_display()}'
