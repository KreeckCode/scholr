from django import forms
from django.core.exceptions import ValidationError

from ..models.learner import (
    Learner,
    Guardian,
    LearnerGuardian,
    LearnerEnrollment,
)
from ..models.school import AcademicYear
from ..models.structure import Grade, GradeStream
from ..utils.sa_id import validate_sa_id

# Shared Tailwind input classes - I'm keeping these as constants
# so a designer can change them in one place
_TEXT_INPUT_CLS = (
    "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm "
    "focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
)
_SELECT_CLS = (
    "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white "
    "focus:outline-none focus:ring-2 focus:ring-blue-500"
)
_TEXTAREA_CLS = (
    "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm "
    "focus:outline-none focus:ring-2 focus:ring-blue-500"
)


def _apply_tailwind(form):
    """Apply Tailwind classes to all widgets in a form."""
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, (forms.TextInput, forms.EmailInput, forms.URLInput,
                               forms.NumberInput, forms.PasswordInput)):
            widget.attrs.setdefault("class", _TEXT_INPUT_CLS)
        elif isinstance(widget, forms.Select):
            widget.attrs.setdefault("class", _SELECT_CLS)
        elif isinstance(widget, forms.Textarea):
            widget.attrs.setdefault("class", _TEXTAREA_CLS)
            widget.attrs.setdefault("rows", "3")
        elif isinstance(widget, forms.DateInput):
            widget.attrs.setdefault("class", _TEXT_INPUT_CLS)
            widget.attrs.setdefault("type", "date")
        elif isinstance(widget, forms.CheckboxInput):
            widget.attrs.setdefault(
                "class",
                "h-4 w-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500",
            )


class LearnerForm(forms.ModelForm):
    """
    Learner registration and edit form.
    SA ID auto-populates date of birth and gender when valid.
    The user_role kwarg from the view restricts fields for admin clerks.
    """

    def __init__(self, *args, **kwargs):
        self.user_role = kwargs.pop("user_role", None)
        super().__init__(*args, **kwargs)
        _apply_tailwind(self)

        # Clerks can edit demographic and medical data but cannot
        # touch the admission number or link a portal account
        if self.user_role == "admin_clerk":
            for field_name in ("admission_number", "user", "emis_learner_number"):
                if field_name in self.fields:
                    self.fields[field_name].disabled = True

        # Make admission_number optional in the form - it's auto-generated
        self.fields["admission_number"].required = False
        self.fields["admission_number"].help_text = (
            "Leave blank - auto-generated on registration."
        )

    def clean_sa_id_number(self):
        id_number = self.cleaned_data.get("sa_id_number", "").strip()
        if not id_number:
            return id_number

        valid, result = validate_sa_id(id_number)
        if not valid:
            raise ValidationError(result)

        # Uniqueness check - exclude self on edit
        qs = Learner.objects.filter(sa_id_number=id_number)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError(
                "A learner with this SA ID number is already registered."
            )

        return id_number

    def clean(self):
        cleaned = super().clean()
        id_number = cleaned.get("sa_id_number", "")
        birth_cert = cleaned.get("birth_cert_number", "")

        if not id_number and not birth_cert:
            raise ValidationError(
                "At least one identifier is required: SA ID number or Birth Certificate number."
            )

        # Auto-populate DOB and gender from a valid SA ID
        # if the user didn't already fill them in
        if id_number:
            valid, result = validate_sa_id(id_number)
            if valid:
                if not cleaned.get("date_of_birth"):
                    cleaned["date_of_birth"] = result["date_of_birth"]
                if not cleaned.get("gender"):
                    cleaned["gender"] = result["gender"]

        return cleaned

    class Meta:
        model = Learner
        fields = [
            # Identity
            "first_name", "last_name", "preferred_name",
            "sa_id_number", "birth_cert_number",
            "admission_number", "emis_learner_number",
            # Demographics
            "date_of_birth", "gender", "home_language",
            "nationality", "classification",
            # Medical
            "allergies", "chronic_conditions", "current_medication",
            "medical_aid_scheme", "medical_aid_number",
            # Transport
            "transport_mode", "transport_notes",
            # Address
            "street_address", "suburb", "city", "postal_code",
            # Photo
            "photo",
        ]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
        }


class GuardianForm(forms.ModelForm):
    """Create or edit a guardian record."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_tailwind(self)

    class Meta:
        model = Guardian
        fields = [
            "first_name", "last_name", "sa_id_number",
            "phone_primary", "phone_secondary",
            "email", "whatsapp_number",
            "street_address", "suburb", "city",
            "employer", "work_phone",
        ]


class LearnerGuardianForm(forms.ModelForm):
    """Link a guardian to a learner with relationship and contact preferences."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_tailwind(self)

    class Meta:
        model = LearnerGuardian
        fields = [
            "relationship", "is_primary_contact",
            "can_collect", "receives_reports", "receives_sms",
            "has_court_restriction", "court_restriction_notes",
        ]


class LearnerEnrollmentForm(forms.ModelForm):
    """
    Enroll a learner in a grade for an academic year.
    Prevents duplicate enrollment in the same year.
    """

    def __init__(self, *args, **kwargs):
        self.learner = kwargs.pop("learner", None)
        super().__init__(*args, **kwargs)
        _apply_tailwind(self)

        current_year = AcademicYear.get_current()
        self.fields["academic_year"].queryset = AcademicYear.objects.order_by("-year")
        if current_year:
            self.fields["academic_year"].initial = current_year

        self.fields["grade"].queryset = Grade.objects.filter(
            is_active=True
        ).order_by("grade_number")

        self.fields["stream"].queryset = GradeStream.objects.filter(
            is_active=True
        ).select_related("grade", "academic_year").order_by(
            "grade__grade_number", "name"
        )
        self.fields["stream"].required = False

    def clean(self):
        cleaned = super().clean()
        academic_year = cleaned.get("academic_year")

        if self.learner and academic_year:
            qs = LearnerEnrollment.objects.filter(
                learner=self.learner,
                academic_year=academic_year,
            )
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError(
                    f"This learner already has an enrollment record for {academic_year.year}. "
                    f"Edit the existing enrollment instead."
                )
        return cleaned

    class Meta:
        model = LearnerEnrollment
        fields = ["academic_year", "grade", "stream", "admission_date", "status"]
        widgets = {
            "admission_date": forms.DateInput(attrs={"type": "date"}),
        }