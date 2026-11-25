"""
One sign-in box for username, email or phone number, with the same password.

Without SCHOLR_EMAIL_SIGN_IN_ENABLED this behaves exactly as sign-in always has: a
username, or a phone number written with its country code. With it, an email address
or a phone number written any way it was saved also works.

An email or phone number shared by more than one account is never used to sign in,
because there is no way to tell which account the person means; they use their
username instead.
"""
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q

from accounts.models import User
from scholr.features import email_sign_in_enabled


def _only(queryset):
    """The single match, or None when there is none or more than one."""
    found = list(queryset[:2])
    return found[0] if len(found) == 1 else None


def resolve(identifier):
    """The account a sign-in name points to, or None."""
    identifier = (identifier or '').strip()
    if not identifier:
        return None

    # An exact username always wins, even one that looks like an email address
    exact = User.objects.filter(username=identifier).first()
    if exact:
        return exact

    if not email_sign_in_enabled():
        if identifier.startswith('+'):
            return _only(User.objects.filter(phone_number=identifier))
        return None

    if '@' in identifier:
        return _only(User.objects.filter(email__iexact=identifier))

    by_username = _only(User.objects.filter(username__iexact=identifier))
    if by_username:
        return by_username

    compact = ''.join(character for character in identifier if character.isdigit() or character == '+')
    return _only(User.objects.filter(Q(phone_number=identifier) | Q(phone_number=compact)))


class IdentifierBackend(ModelBackend):

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if username is None or password is None:
            return None

        user = resolve(username)
        if user is None:
            # Same password work as a real check, so a missing account takes as long to refuse
            User().set_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
