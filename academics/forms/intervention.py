from django import forms
from django.utils import timezone

from ..models.intervention import InterventionProgram, LearnerIntervention, ProgressNote
from ..models.curriculum import SubjectOffering
from ..models.school import AcademicYear

_TEXT_CLS = (
    "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm "
    "focus:outline-none focus:ring-2 focus:ring-blue-500"
)
_SELECT_CLS = (
    "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white "
    "focus:outline-none focus:ring-2 focus:ring-blue-500"
)
_TEXTAREA_CLS = (
    "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm "
    "focus:outline-none focus:ring-2 focus:ring-blue-500"
)
_CHECKBOX_CLS = "h-4 w-4 text-blue-600 border-gray-300 rounded"


def _apply(form):
    for field in form.fields.values():
        w = field.widget
        if isinstance(w, forms.TextInput):
            w.attrs.setdefault("class", _TEXT_CLS)
        elif isinstance(w, forms.Select):
            w.attrs.setdefault("class", _SELECT_CLS)
        elif isinstance(w, forms.Textarea):
            w.attrs.setdefault("class", _TEXTAREA_CLS)
            w.attrs.setdefault("rows", "4")
        elif isinstance(w, forms.DateInput):
            w.attrs.setdefault("class", _TEXT_CLS)
            w.attrs.setdefault("type", "date")
        elif isinstance(w, forms.CheckboxInput):
            w.attrs.setdefault("class", _CHECKBOX_CLS)


class LearnerInterventionForm(forms.ModelForm):
    """
    Add or edit an intervention for a learner.
    The learner and academic_year are set from the URL in the view.
    The subject_offering dropdown is filtered to subjects the learner is enrolled in.
    """

    def __init__(self, *args, **kwargs):
        self.learner = kwargs.pop("learner", None)
        super().__init__(*args, **kwargs)
        _apply(self)

        self.fields["program"].queryset = InterventionProgram.objects.filter(
            is_active=True
        ).order_by("sort_order", "name")
        self.fields["program"].empty_label = "Select a program..."

        # Scope subject choices to what this learner is actually enrolled in
        if self.learner:
            from ..models.marks import SubjectEnrollment
            current_year = AcademicYear.get_current()
            offering_ids = SubjectEnrollment.objects.filter(
                learner=self.learner,
                academic_year=current_year,
                is_active=True,
            ).values_list("subject_offering_id", flat=True)
            self.fields["subject_offering"].queryset = SubjectOffering.objects.filter(
                pk__in=offering_ids
            ).select_related("subject", "grade").order_by("subject__name")
        else:
            self.fields["subject_offering"].queryset = SubjectOffering.objects.none()

        self.fields["subject_offering"].required = False
        self.fields["subject_offering"].empty_label = "General (not subject-specific)"
        self.fields["end_date"].required = False

        # Default start date to today
        self.fields["start_date"].initial = timezone.now().date()

    class Meta:
        model = LearnerIntervention
        fields = [
            "program", "subject_offering", "start_date", "end_date",
            "assigned_to", "status", "notes", "outcome_notes",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }


class ProgressNoteForm(forms.ModelForm):
    """Add a progress note for a learner."""

    def __init__(self, *args, **kwargs):
        self.learner = kwargs.pop("learner", None)
        super().__init__(*args, **kwargs)
        _apply(self)

        if self.learner:
            from ..models.marks import SubjectEnrollment
            current_year = AcademicYear.get_current()
            offering_ids = SubjectEnrollment.objects.filter(
                learner=self.learner,
                academic_year=current_year,
                is_active=True,
            ).values_list("subject_offering_id", flat=True)
            self.fields["subject_offering"].queryset = SubjectOffering.objects.filter(
                pk__in=offering_ids
            ).select_related("subject").order_by("subject__name")
        else:
            self.fields["subject_offering"].queryset = SubjectOffering.objects.none()

        self.fields["subject_offering"].required = False
        self.fields["subject_offering"].empty_label = "General observation"

    class Meta:
        model = ProgressNote
        fields = ["subject_offering", "note", "share_with_parent"]