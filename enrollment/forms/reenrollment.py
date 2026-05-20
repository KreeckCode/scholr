"""
Forms for the parent re-enrollment portal.

Used by parents/guardians to confirm their child's enrollment
for the upcoming academic year.
"""
from django import forms

from academics.models.learner import Learner
from academics.models.curriculum import SubjectOffering
from academics.models.school import AcademicYear

class LearnerDetailsConfirmationForm(forms.ModelForm):
    """
    Pre-populated form for parents to confirm/update learner details.
    Name, DOB, gender are displayed as text in template (not included in form).
    Only medical/language info is editable.
    """

    class Meta:
        model = Learner
        fields = [
            # Only the editable fields
            'home_language',
            'allergies',
            'chronic_conditions',
            'current_medication',
        ]
        widgets = {
            'home_language': forms.Select(attrs={'class': 'field'}),
            'allergies': forms.Textarea(attrs={
                'class': 'field',
                'rows': 2,
                'placeholder': 'List any allergies (food, medication, environmental)…',
            }),
            'chronic_conditions': forms.Textarea(attrs={
                'class': 'field',
                'rows': 3,
                'placeholder': 'List any chronic conditions we should be aware of…',
            }),
            'current_medication': forms.Textarea(attrs={
                'class': 'field',
                'rows': 3,
                'placeholder': 'Medication taken at school (include dosage and schedule)…',
            }),
        }

    confirm_details = forms.BooleanField(
        required=True,
        label='I confirm that the above details are correct and up to date.',
        error_messages={'required': 'Please confirm the details before continuing.'},
    )

    
class SubjectSelectionForm(forms.Form):
    """
    Subject selection for grade transitions requiring electives.
    Dynamically generated based on available subject offerings.
    """

    def __init__(self, *args, **kwargs):
        grade = kwargs.pop('grade', None)
        kwargs.pop('academic_year', None)  # Remove it but don't use it
        required_count = kwargs.pop('required_count', 4)
        
        super().__init__(*args, **kwargs)

        if grade and AcademicYear:
            # Get all available subject offerings for this grade/year
            offerings = SubjectOffering.objects.filter(
                grade=grade,
                is_active=True,
            ).select_related('subject').order_by('subject__name')

            # Separate compulsory and elective subjects
            compulsory = offerings.filter(is_compulsory=True)
            electives = offerings.filter(is_compulsory=False)

            # Add compulsory subjects as read-only checkboxes (pre-checked)
            for offering in compulsory:
                field_name = f'subject_{offering.pk}'
                self.fields[field_name] = forms.BooleanField(
                    label=offering.subject.name,
                    required=False,
                    initial=True,
                    disabled=True,
                    widget=forms.CheckboxInput(attrs={
                        'class': 'rounded border-slate-600 bg-slate-800 text-indigo-600 disabled:opacity-60',
                    }),
                )

            # Add elective subjects as checkboxes
            for offering in electives:
                field_name = f'subject_{offering.pk}'
                self.fields[field_name] = forms.BooleanField(
                    label=offering.subject.name,
                    required=False,
                    widget=forms.CheckboxInput(attrs={
                        'class': 'rounded border-slate-600 bg-slate-800 text-indigo-600',
                    }),
                )

            self.compulsory_count = compulsory.count()
            self.required_count = required_count
            self.offerings_map = {f'subject_{o.pk}': o for o in offerings}

    def clean(self):
        cleaned = super().clean()
        
        # Collect all selected offerings (compulsory + electives)
        selected_offerings = []
        
        for field_name, offering in self.offerings_map.items():
            if cleaned.get(field_name):  # If checkbox is checked
                selected_offerings.append(offering)
        
        # Check minimum count
        if len(selected_offerings) < self.required_count:
            raise forms.ValidationError(
                f'Please select at least {self.required_count} subjects in total. '
                f'You have selected {len(selected_offerings)}.'
            )
        
        # Store for view to access
        self.selected_offerings = selected_offerings
        
        return cleaned


class FeeAgreementConsentForm(forms.Form):
    """
    Digital consent form for fee agreement.
    ECTA-compliant with timestamp and IP capture.
    """

    agree_to_fees = forms.BooleanField(
        required=True,
        label=(
            'I have read and agree to the fee schedule for the upcoming academic year. '
            'I understand my obligation to pay school fees as outlined.'
        ),
        error_messages={
            'required': 'You must agree to the fee schedule to complete re-enrollment.',
        },
    )

    agree_to_terms = forms.BooleanField(
        required=True,
        label=(
            'I agree to the school policies, code of conduct, and terms and conditions '
            'as published on the school website.'
        ),
        error_messages={
            'required': 'You must agree to the school policies to complete re-enrollment.',
        },
    )

    signature_name = forms.CharField(
        max_length=200,
        label='Full name (digital signature)',
        widget=forms.TextInput(attrs={
            'class': 'field',
            'placeholder': 'Type your full name as it appears on your ID',
        }),
        help_text='By typing your name, you are providing a legally binding digital signature.',
    )

class GuardianContactUpdateForm(forms.Form):
    """
    Allow guardian to update their own contact details.
    Separate from learner details confirmation.
    """

    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'field',
            'placeholder': 'guardian@example.com',
        }),
    )

    cell_phone = forms.CharField(
        max_length=20,
        required=True,
        label='Primary phone',
        widget=forms.TextInput(attrs={
            'class': 'field',
            'placeholder': '+27 XX XXX XXXX',
        }),
        help_text='This maps to phone_primary on Guardian model',
    )

    work_phone = forms.CharField(
        max_length=20,
        required=False,
        label='Secondary phone',
        widget=forms.TextInput(attrs={
            'class': 'field',
            'placeholder': '+27 XX XXX XXXX',
        }),
        help_text='This maps to phone_secondary on Guardian model',
    )

    physical_address = forms.CharField(
        required=False,
        label='Physical address',
        widget=forms.Textarea(attrs={
            'class': 'field',
            'rows': 3,
            'placeholder': 'Street address\nCity, Province\nPostal code',
        }),
        help_text='This maps to street_address on Guardian model',
    )