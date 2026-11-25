from django.dispatch import receiver

from accounts.roles import role_changed
from partners.services.reach import clear_unused_reach


@receiver(role_changed, dispatch_uid='partners_clear_unused_reach')
def drop_reach_the_new_role_does_not_use(sender, user, new_role, **kwargs):
    clear_unused_reach(user, new_role)
