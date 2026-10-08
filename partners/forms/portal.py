import csv
import io

from django import forms
from django.core.exceptions import ValidationError

from accounts.models import User
from partners.models import (
    Cohort,
    CohortSession,
    LocationMembership,
    Participant,
    ProgrammeCourse,
)
from partners.models.base import phone_validator

from .base import DateInput, StyledFormMixin

IMPORT_MAX_BYTES = 1024 * 1024
IMPORT_MAX_ROWS = 500
IMPORT_COLUMNS = ('first_name', 'last_name', 'phone', 'email', 'church_name', 'position_title')


def location_team(location):
    return User.objects.filter(
        location_memberships__location=location, location_memberships__is_active=True,
    ).distinct().order_by('first_name', 'last_name')


def _team_label(user):
    return user.get_full_name()


class CohortForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Cohort
        fields = ['name', 'language', 'delivery', 'pace_months', 'starts_on', 'ends_on', 'facilitators']
        widgets = {
            'starts_on': DateInput(),
            'ends_on': DateInput(),
            'facilitators': forms.CheckboxSelectMultiple,
        }
        labels = {'pace_months': 'Pace', 'ends_on': 'Planned end date'}

    def __init__(self, *args, location, **kwargs):
        super().__init__(*args, **kwargs)
        self.location = location
        self.instance.location = location
        self.fields['facilitators'].queryset = location_team(location)
        self.fields['facilitators'].label_from_instance = _team_label
        if not self.instance.pk:
            self.fields['language'].initial = location.language

    def clean(self):
        cleaned = super().clean()
        starts_on, ends_on = cleaned.get('starts_on'), cleaned.get('ends_on')
        if starts_on and ends_on and ends_on <= starts_on:
            self.add_error('ends_on', 'The end date must be after the start date.')
        return cleaned


class ParticipantForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Participant
        fields = ['first_name', 'last_name', 'phone', 'email', 'church_name', 'position_title', 'country']
        help_texts = {'phone': 'International format, for example +27821234567.'}

    def __init__(self, *args, location, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.home_location = location
        if not self.instance.pk:
            self.fields['country'].initial = location.country


class SessionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = CohortSession
        fields = ['course', 'session_date', 'hours', 'facilitator', 'status', 'notes']
        widgets = {'session_date': DateInput(), 'notes': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, cohort, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.cohort = cohort
        self.fields['course'].queryset = ProgrammeCourse.objects.filter(is_active=True)
        self.fields['facilitator'].queryset = location_team(cohort.location)
        self.fields['facilitator'].label_from_instance = _team_label
        if not self.instance.pk:
            self.fields['status'].initial = CohortSession.Status.PLANNED


class MemberInviteForm(StyledFormMixin, forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)
    phone = forms.CharField(
        max_length=20, required=False, validators=[phone_validator],
        help_text='International format, for example +27821234567.',
    )
    role = forms.ChoiceField(choices=LocationMembership.Role.choices)
    trained_on = forms.DateField(
        label='Training completed on', required=False, widget=DateInput(),
        help_text='METP 100a for coordinators, METP 100b for facilitators.',
    )

    def __init__(self, *args, allowed_roles, **kwargs):
        super().__init__(*args, **kwargs)
        labels = dict(LocationMembership.Role.choices)
        self.fields['role'].choices = [(role, labels[role]) for role in allowed_roles]

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('email') and not cleaned.get('phone'):
            raise ValidationError('Give an email address or a phone number.')
        return cleaned


class ResultEntryForm(forms.Form):
    """
    One score field per participant for one course, built at runtime.
    Each score carries the version the editor saw, so a concurrent change is caught.
    """

    def __init__(self, *args, memberships, results, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = []
        for membership in memberships:
            result = results.get(membership.pk)
            score_name = f'score_{membership.public_id}'
            version_name = f'version_{membership.public_id}'
            self.fields[score_name] = forms.DecimalField(
                required=False, min_value=0, max_value=100, decimal_places=2, max_digits=5,
                initial=result.exam_score if result else None,
                disabled=bool(result and result.is_signed_off),
                widget=forms.NumberInput(attrs={
                    'class': 'w-24 rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm text-right',
                    'step': '0.01', 'inputmode': 'decimal',
                }),
            )
            self.fields[version_name] = forms.IntegerField(
                required=False, widget=forms.HiddenInput, initial=result.version if result else None,
            )
            self.rows.append((membership, result, score_name, version_name))

    def changed_rows(self):
        """(membership, score, version) for rows whose score was changed."""
        for membership, result, score_name, version_name in self.rows:
            field = self.fields[score_name]
            if field.disabled or score_name not in self.changed_data:
                continue
            yield membership, self.cleaned_data.get(score_name), self.cleaned_data.get(version_name)


class ParticipantImportForm(StyledFormMixin, forms.Form):
    file = forms.FileField(
        label='CSV file',
        help_text='Columns: first_name, last_name, phone, email, church_name, position_title. '
                  f'Up to {IMPORT_MAX_ROWS} rows.',
        widget=forms.ClearableFileInput(attrs={'accept': '.csv,text/csv'}),
    )

    def clean_file(self):
        upload = self.cleaned_data['file']
        if upload.size > IMPORT_MAX_BYTES:
            raise ValidationError('The file must be 1 MB or smaller.')
        try:
            text = upload.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ValidationError('Save the file as CSV with UTF-8 encoding and try again.')
        reader = csv.DictReader(io.StringIO(text))
        headers = [(name or '').strip().lower() for name in (reader.fieldnames or [])]
        missing = {'first_name', 'last_name'} - set(headers)
        if missing:
            raise ValidationError('The file needs first_name and last_name columns.')
        rows = []
        for index, raw in enumerate(reader, start=2):
            if len(rows) >= IMPORT_MAX_ROWS:
                raise ValidationError(f'Import at most {IMPORT_MAX_ROWS} rows at a time.')
            row = {
                key: (raw.get(original) or '').strip()
                for original, key in zip(reader.fieldnames, headers) if key in IMPORT_COLUMNS
            }
            if not any(row.values()):
                continue
            rows.append({'line': index, **{column: row.get(column, '') for column in IMPORT_COLUMNS}})
        if not rows:
            raise ValidationError('The file has no participant rows.')
        return rows


def validate_import_row(row):
    """Field errors for one imported row, using the model's own validators."""
    participant = Participant(
        first_name=row['first_name'][:100], last_name=row['last_name'][:100],
        phone=row['phone'], email=row['email'], church_name=row['church_name'][:200],
        position_title=row['position_title'][:100],
    )
    try:
        participant.full_clean(exclude=['home_location', 'user', 'created_by', 'country', 'client_uuid'])
    except ValidationError as exc:
        return [f'{field}: {" ".join(messages)}' for field, messages in exc.message_dict.items()]
    return []
