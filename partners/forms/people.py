from django import forms

from accounts.models import User
from partners.services.people import CREATABLE_ROLES

from .base import StyledFormMixin

ROLE_LABELS = dict(User.ROLE_CHOICES)


class StaffMemberForm(StyledFormMixin, forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    role = forms.ChoiceField(choices=[(role, ROLE_LABELS[role]) for role in CREATABLE_ROLES])
    email = forms.EmailField(required=False)
    phone = forms.CharField(
        max_length=20, required=False, help_text='For sending the set-password link by WhatsApp or SMS.',
    )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('email') and not cleaned.get('phone'):
            raise forms.ValidationError('Give an email address or a phone number, so they can be sent their link.')
        return cleaned


class RoleChangeForm(StyledFormMixin, forms.Form):
    new_role = forms.ChoiceField(label='New role')
    note = forms.CharField(max_length=255, required=False, label='Reason (optional)',
                           help_text='Kept in the role history, for example "Now covering East Africa".')

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        roles = sorted(User.ASSIGNABLE_STAFF_ROLES - {user.role}, key=lambda role: ROLE_LABELS[role])
        self.fields['new_role'].choices = [(role, ROLE_LABELS[role]) for role in roles]
