"""
Changing a person's role, with the guard rails a permission change needs.

Only system administrators change roles, and never their own, which means the last
active administrator can never be demoted. Changes are limited to staff roles: students, learners, parents and
partner staff carry enrolments and memberships a role change would strand.

Every change is recorded in RoleChange and announced with role_changed, so other apps
tidy up after it without this module knowing about them.
"""
from django.db import transaction
from django.dispatch import Signal

from .models import RoleChange, User

# Sent after the change is saved, with user, old_role, new_role and actor
role_changed = Signal()


class RoleChangeError(Exception):
    """A change that is not allowed, with a sentence the administrator can read."""


@transaction.atomic
def change_role(user, new_role, actor, note=''):
    if not (actor and actor.is_authenticated and actor.is_active and actor.role == User.ADMIN):
        raise RoleChangeError('Only system administrators can change roles.')
    if user.pk == actor.pk:
        raise RoleChangeError('You cannot change your own role. Ask another administrator.')
    if new_role not in dict(User.ROLE_CHOICES):
        raise RoleChangeError('That is not a role.')

    # I lock every active admin row and re-check the actor against the database, because the
    # actor's copy may be stale: two administrators demoting each other at the same moment
    # would otherwise both succeed. As the actor must be another active administrator and
    # nobody changes their own role, the last administrator can never be demoted.
    admins = set(
        User.objects.select_for_update().filter(role=User.ADMIN, is_active=True).values_list('pk', flat=True)
    )
    if actor.pk not in admins:
        raise RoleChangeError('Only system administrators can change roles.')

    user = User.objects.select_for_update().get(pk=user.pk)
    old_role = user.role
    if new_role == old_role:
        raise RoleChangeError(f'{user.get_full_name() or user.username} already has that role.')
    if old_role not in User.ASSIGNABLE_STAFF_ROLES or new_role not in User.ASSIGNABLE_STAFF_ROLES:
        raise RoleChangeError(
            'Only staff roles can be changed here. Students, learners, parents and partner staff '
            'have studies or memberships tied to their role.'
        )

    user.role = new_role
    user.save(update_fields=['role'])
    change = RoleChange.objects.create(
        user=user, from_role=old_role, to_role=new_role, changed_by=actor, note=note[:255],
    )
    role_changed.send(sender=User, user=user, old_role=old_role, new_role=new_role, actor=actor)
    return change
