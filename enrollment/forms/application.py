"""
Application form for the enrollment module.

One form class handles all sections. The view strips fields that are
not required by the template (show_medical_section, etc.) so validation
only runs on fields the template actually shows.

Widget classes use `.field` which is defined in the enrollment base template.
"""
from django import forms
from django.core.exceptions import ValidationError

from ..models.application import Application, ApplicationDocument
from ..validators import validate_sa_id_number


# ---- Widget helpers ----

def _text(placeholder='', **kwargs):
    attrs = {'class': 'field', 'placeholder': placeholder, **kwargs}
    return forms.TextInput(attrs=attrs)


def _email(placeholder=''):
    return forms.EmailInput(attrs={'class': 'field', 'placeholder': placeholder})


def _select():
    return forms.Select(attrs={'class': 'field'})


def _date():
    return forms.DateInput(attrs={'class': 'field', 'type': 'date'})


def _tel(placeholder=''):
    return forms.TextInput(attrs={'class': 'field', 'type': 'tel', 'placeholder': placeholder})


def _textarea(rows=3, placeholder=''):
    return forms.Textarea(attrs={
        'class': 'field resize-none',
        'rows': rows,
        'placeholder': placeholder,
    })


# ==================== MAIN APPLICATION FORM ====================

class ApplicationForm(forms.Form):
    """
    Full application form. Sections are toggled by the template (Alpine.js)
    and conditionally required by the view based on ApplicationTemplate settings.
    """

    # ========================
    # SECTION 1 — Learner
    # ========================
    first_name = forms.CharField(
        max_length=100, label='First name',
        widget=_text('e.g. Thabo'),
    )
    last_name = forms.CharField(
        max_length=100, label='Last name',
        widget=_text('e.g. Nkosi'),
    )
    date_of_birth = forms.DateField(
        label='Date of birth',
        widget=_date(),
    )
    gender = forms.ChoiceField(
        choices=[('', '— Select —')] + [
            ('M', 'Male'),
            ('F', 'Female'),
            ('O', 'Other / Prefer not to say'),
        ],
        widget=_select(),
    )
    home_language = forms.CharField(
        max_length=60, required=False, label='Home language',
        widget=_text('e.g. isiZulu'),
    )
    nationality = forms.CharField(
        max_length=60, required=False, label='Nationality',
        widget=_text('e.g. South African'),
        initial='South African',
    )

    # ---- Identity ----
    id_type = forms.ChoiceField(
        choices=Application.ID_TYPE_CHOICES,
        widget=_select(),
        label='Identity document type',
    )
    id_number = forms.CharField(
        max_length=50, label='ID number / Passport number',
        widget=_text('13-digit SA ID or passport number'),
    )
    id_country_of_issue = forms.CharField(
        max_length=60, required=False,
        label='Country of issue',
        help_text='Required for passports and foreign documents.',
        widget=_text('e.g. Zimbabwe'),
    )

    # ========================
    # SECTION 2 — Guardian
    # ========================
    guardian_first_name = forms.CharField(
        max_length=100, label='First name',
        widget=_text('e.g. Nomsa'),
    )
    guardian_last_name = forms.CharField(
        max_length=100, label='Last name',
        widget=_text('e.g. Nkosi'),
    )
    guardian_relationship = forms.ChoiceField(
        choices=[('', '— Select —')] + [
            ('mother', 'Mother'),
            ('father', 'Father'),
            ('legal_guardian', 'Legal Guardian'),
            ('grandparent', 'Grandparent'),
            ('other', 'Other'),
        ],
        widget=_select(), label='Relationship to learner',
    )
    guardian_email = forms.EmailField(
        label='Email address',
        help_text='All correspondence regarding this application will be sent here.',
        widget=_email('parent@email.com'),
    )
    guardian_phone = forms.CharField(
        max_length=20, label='Mobile number',
        widget=_tel('+27 82 000 0000'),
    )
    guardian_id_number = forms.CharField(
        max_length=50, required=False,
        label='Guardian ID number',
        help_text='Optional but required if applying for bursary.',
        widget=_text('13-digit SA ID'),
    )
    guardian_employer = forms.CharField(
        max_length=100, required=False, label='Employer name',
        widget=_text('Optional'),
    )
    guardian_address_line1 = forms.CharField(
        max_length=200, label='Street address',
        widget=_text('e.g. 15 Main Street'),
    )
    guardian_address_line2 = forms.CharField(
        max_length=200, required=False, label='Suburb / Unit',
        widget=_text('Optional'),
    )
    guardian_city = forms.CharField(
        max_length=100, label='City / Town',
        widget=_text('e.g. Cape Town'),
    )
    guardian_province = forms.CharField(
        max_length=60, required=False, label='Province',
        widget=_text('e.g. Western Cape'),
    )
    guardian_postal_code = forms.CharField(
        max_length=10, required=False, label='Postal code',
        widget=_text('e.g. 8001'),
    )

    # ---- Second guardian (optional) ----
    second_guardian_first_name = forms.CharField(
        max_length=100, required=False, label='First name',
        widget=_text('Optional'),
    )
    second_guardian_last_name = forms.CharField(
        max_length=100, required=False, label='Last name',
        widget=_text('Optional'),
    )
    second_guardian_relationship = forms.CharField(
        max_length=40, required=False, label='Relationship',
        widget=_text('e.g. Father'),
    )
    second_guardian_email = forms.EmailField(
        required=False, label='Email address',
        widget=_email('Optional'),
    )
    second_guardian_phone = forms.CharField(
        max_length=20, required=False, label='Phone number',
        widget=_tel('Optional'),
    )

    # ---- Emergency contact ----
    emergency_contact_name = forms.CharField(
        max_length=100, label='Emergency contact name',
        widget=_text('e.g. John Smith'),
    )
    emergency_contact_phone = forms.CharField(
        max_length=20, label='Emergency contact phone',
        widget=_tel('+27 82 000 0000'),
    )
    emergency_contact_relationship = forms.CharField(
        max_length=40, label='Relationship',
        widget=_text('e.g. Uncle'),
    )

    # ========================
    # SECTION 3 — Previous school
    # ========================
    previous_school_name = forms.CharField(
        max_length=200, required=False, label='School name',
        widget=_text('e.g. Cape Town Primary'),
    )
    previous_school_type = forms.ChoiceField(
        required=False,
        choices=[('', '— Select —')] + [
            ('public', 'Public / Government'),
            ('private', 'Private / Independent'),
            ('homeschool', 'Homeschool'),
            ('other', 'Other'),
        ],
        widget=_select(), label='School type',
    )
    previous_grade_completed = forms.CharField(
        max_length=20, required=False,
        label='Most recent grade completed',
        widget=_text('e.g. Grade 7'),
    )
    previous_school_country = forms.CharField(
        max_length=60, required=False,
        label='Country', initial='South Africa',
        widget=_text('South Africa'),
    )
    reason_for_leaving = forms.CharField(
        required=False, label='Reason for leaving',
        widget=_textarea(rows=3, placeholder='Optional'),
    )

    # ========================
    # SECTION 4 — Medical
    # ========================
    has_medical_conditions = forms.BooleanField(
        required=False,
        label='The learner has medical conditions, allergies, or chronic medication.',
    )
    medical_details = forms.CharField(
        required=False, label='Medical details',
        help_text='Allergies, chronic conditions, daily medication, dietary requirements.',
        widget=_textarea(rows=4, placeholder='Describe any conditions the school should know about…'),
    )
    has_learning_support_needs = forms.BooleanField(
        required=False,
        label='The learner has been assessed for learning support needs (e.g. ADHD, dyslexia).',
    )
    learning_support_details = forms.CharField(
        required=False, label='Learning support details',
        widget=_textarea(rows=3, placeholder='Describe assessments, existing support, or accommodations needed…'),
    )
    medical_aid_provider = forms.CharField(
        max_length=100, required=False, label='Medical aid provider',
        widget=_text('e.g. Discovery Health'),
    )
    medical_aid_number = forms.CharField(
        max_length=50, required=False, label='Medical aid number',
        widget=_text('Optional'),
    )

    # ========================
    # SECTION 5 — Additional
    # ========================
    essay_answer = forms.CharField(
        required=False, label='Essay answer',
        widget=_textarea(rows=8),
    )
    how_did_you_hear = forms.ChoiceField(
        required=False,
        choices=[('', '— Select —')] + list(Application.HOW_HEARD_CHOICES),
        widget=_select(),
        label='How did you hear about us?',
    )

    # ========================
    # SECTION 6 — Declaration
    # ========================
    declaration_accurate = forms.BooleanField(
        required=True,
        label=(
            'I confirm that all information provided in this application is accurate '
            'and complete to the best of my knowledge.'
        ),
        error_messages={'required': 'You must confirm the accuracy of your application.'},
    )
    declaration_popia = forms.BooleanField(
        required=True,
        label=(
            'I consent to the school processing the personal information provided '
            'in this application in accordance with POPIA for the purposes of '
            'enrollment administration.'
        ),
        error_messages={'required': 'POPIA consent is required to submit your application.'},
    )

    # ========================
    # CROSS-FIELD VALIDATION
    # ========================

    def clean_id_number(self):
        id_type   = self.cleaned_data.get('id_type')
        id_number = self.cleaned_data.get('id_number', '').strip()

        if id_type == Application.ID_SA:
            if not validate_sa_id_number(id_number):
                raise ValidationError(
                    'This does not appear to be a valid South African ID number. '
                    'Please check the number and try again.'
                )
        return id_number

    def clean(self):
        cleaned = super().clean()
        id_type   = cleaned.get('id_type')
        id_number = cleaned.get('id_number', '')

        # Passport requires country of issue
        if id_type in (Application.ID_PASSPORT, Application.ID_ASYLUM):
            if not cleaned.get('id_country_of_issue'):
                self.add_error(
                    'id_country_of_issue',
                    'Country of issue is required for non-SA documents.',
                )

        # Medical details required if condition flagged
        if cleaned.get('has_medical_conditions') and not cleaned.get('medical_details'):
            self.add_error(
                'medical_details',
                'Please describe the medical conditions you indicated.',
            )
        if cleaned.get('has_learning_support_needs') and not cleaned.get('learning_support_details'):
            self.add_error(
                'learning_support_details',
                'Please describe the learning support needs you indicated.',
            )

        return cleaned


# ==================== DOCUMENT UPLOAD FORM ====================

class DocumentUploadForm(forms.Form):
    """Single-document upload. Submitted once per required document type."""

    document_type = forms.ChoiceField(
        choices=ApplicationDocument.DOCUMENT_TYPES,
        widget=_select(),
        label='Document type',
    )
    upload = forms.FileField(
        label='File',
        help_text='PDF, JPEG, or PNG. Maximum 8 MB.',
        widget=forms.FileInput(attrs={'class': 'field', 'accept': '.pdf,.jpg,.jpeg,.png,.heic'}),
    )

    def clean_upload(self):
        file = self.cleaned_data.get('upload')
        if not file:
            return file

        # Size check: 8 MB
        if file.size > 8 * 1024 * 1024:
            raise ValidationError('File size must be 8 MB or less.')

        # MIME type check via file extension (magic-byte check requires python-magic)
        name = file.name.lower()
        allowed_extensions = ('.pdf', '.jpg', '.jpeg', '.png', '.heic')
        if not any(name.endswith(ext) for ext in allowed_extensions):
            raise ValidationError(
                'Only PDF, JPEG, PNG, and HEIC files are accepted.'
            )

        return file


# ==================== STATUS CHECK FORM ====================

class StatusCheckForm(forms.Form):
    """Public status check: reference number + guardian email."""

    reference_number = forms.CharField(
        max_length=20, label='Reference number',
        widget=_text('ENR-2026-00001'),
    )
    guardian_email = forms.EmailField(
        label='Guardian email address',
        help_text='The email address used when the application was submitted.',
        widget=_email('parent@email.com'),
    )