from django import forms

from partners.countries import COUNTRIES
from partners.models import PartnerOrganisation
from partners.models.base import phone_validator

from .base import StyledFormMixin


class EnquiryForm(StyledFormMixin, forms.Form):
    """Website enquiry from a church or organisation interested in becoming a METP location."""

    organisation_name = forms.CharField(label='Church or organisation', max_length=200)
    kind = forms.ChoiceField(label='Type', choices=PartnerOrganisation.Kind.choices)
    country = forms.ChoiceField(choices=[('', 'Select a country')] + list(COUNTRIES))
    city = forms.CharField(label='City or town', max_length=100, required=False)
    contact_name = forms.CharField(label='Your name', max_length=150)
    role_title = forms.CharField(label='Your role', max_length=100, required=False)
    email = forms.EmailField()
    phone = forms.CharField(
        max_length=20, required=False, validators=[phone_validator],
        help_text='International format, for example +27821234567.',
    )
    expected_participants = forms.IntegerField(
        label='How many people would you like to train?', required=False, min_value=1, max_value=100000,
    )
    message = forms.CharField(
        label='Tell us about your ministry', widget=forms.Textarea(attrs={'rows': 5}), max_length=4000,
        required=False,
    )
    # I'm using this as a honeypot: bots fill every field, people never see this one
    website = forms.CharField(required=False, label='Leave this empty', widget=forms.TextInput(attrs={
        'autocomplete': 'off', 'tabindex': '-1',
    }))

    def clean_website(self):
        if self.cleaned_data.get('website'):
            raise forms.ValidationError('Spam check failed.')
        return ''
