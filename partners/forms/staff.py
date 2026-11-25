from django import forms
from django.db.models import Q

from accounts.models import User
from partners.models import (
    Licence,
    PartnerContact,
    PartnerOrganisation,
    Partnership,
    PartnershipEvent,
    Stage,
    TrainingLocation,
)
from partners.services import pipeline

from .base import DateInput, StyledFormMixin


class OrganisationForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = PartnerOrganisation
        fields = ['name', 'kind', 'country', 'region', 'city', 'website', 'is_sensitive', 'notes']


class PartnershipForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Partnership
        fields = ['owner', 'next_action', 'next_action_due', 'source', 'expected_participants']
        widgets = {'next_action_due': DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        owners = Q(role__in=[User.PROGRAMME_STAFF, User.ADMIN])
        if self.instance.pk:
            # Hub administrators and ambassadors who cover this partner can own its next step
            from partners.services.reach import coverage_for
            coverage = coverage_for(self.instance.organisation)
            owners |= Q(pk__in=[person.pk for person in coverage['by_country'] + coverage['assigned']])
        self.fields['owner'].queryset = User.objects.filter(owners, is_active=True).order_by(
            'first_name', 'last_name',
        )
        self.fields['owner'].label_from_instance = lambda user: user.get_full_name()


class PartnershipMoveForm(StyledFormMixin, forms.Form):
    to_stage = forms.ChoiceField(label='Move to')
    note = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2}), required=False, max_length=2000,
        help_text='Required when skipping a stage, stepping back, or marking as lost.',
    )

    def __init__(self, *args, partnership, actor, **kwargs):
        super().__init__(*args, **kwargs)
        labels = dict(Stage.choices)
        self.fields['to_stage'].choices = [
            (stage, labels[stage]) for stage in pipeline.allowed_targets(partnership, actor)
        ]


class PartnershipNoteForm(StyledFormMixin, forms.Form):
    KIND_CHOICES = [
        (PartnershipEvent.Kind.NOTE, 'Note'),
        (PartnershipEvent.Kind.CALL, 'Call'),
        (PartnershipEvent.Kind.EMAIL, 'Email'),
        (PartnershipEvent.Kind.DOCUMENT, 'Document'),
    ]
    kind = forms.ChoiceField(choices=KIND_CHOICES, initial=PartnershipEvent.Kind.NOTE)
    body = forms.CharField(label='Details', widget=forms.Textarea(attrs={'rows': 3}), max_length=4000)


class ContactForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = PartnerContact
        fields = ['name', 'role_title', 'email', 'phone', 'is_primary']
        help_texts = {'phone': 'International format, for example +27821234567.'}


class PrivateFileInput(forms.ClearableFileInput):
    """Never links to the stored file: private files have no public URL, and showing one would leak it."""

    def is_initial(self, value):
        return False


class LicenceForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Licence
        fields = [
            'starts_on', 'ends_on', 'signed_on', 'agreement_file',
            'fee_amount', 'fee_currency', 'fee_status', 'max_locations', 'notes',
        ]
        widgets = {
            'starts_on': DateInput(),
            'ends_on': DateInput(),
            'signed_on': DateInput(),
            'agreement_file': PrivateFileInput(attrs={'accept': 'application/pdf'}),
        }
        help_texts = {'agreement_file': 'The signed agreement, as a PDF of up to 10 MB.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.agreement_file:
            self.fields['agreement_file'].help_text = (
                'An agreement is already uploaded. Choose a PDF only if you want to replace it.'
            )

    def clean_fee_currency(self):
        value = (self.cleaned_data.get('fee_currency') or '').strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise forms.ValidationError('Use a three-letter currency code, such as CAD or ZAR.')
        return value

    def clean_agreement_file(self):
        upload = self.cleaned_data.get('agreement_file')
        # I'm checking the %PDF- signature so a renamed executable is refused
        if upload and hasattr(upload, 'read') and getattr(upload, 'content_type', None) is not None:
            head = upload.read(5)
            upload.seek(0)
            if head != b'%PDF-':
                raise forms.ValidationError('That file is not a PDF.')
        return upload


class LicenceReasonForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), max_length=2000, required=False)


class LocationForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = TrainingLocation
        fields = ['name', 'licence', 'country', 'region', 'city', 'language', 'status', 'is_sensitive']

    def __init__(self, *args, organisation, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['licence'].queryset = organisation.licences.exclude(
            status=Licence.Status.TERMINATED,
        ).order_by('-starts_on')
        self.fields['licence'].label_from_instance = lambda licence: (
            f'{licence.number} ({licence.get_status_display()}, ends {licence.ends_on:%d %b %Y})'
        )
        if not self.instance.pk:
            self.fields['country'].initial = organisation.country
