from django import forms
from django.core.exceptions import ValidationError

from ..models.curriculum import AssessmentTask, Curriculum, SchoolSubject, SubjectOffering
from ..models.structure import Grade

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


def _apply_tailwind(form):
    for field in form.fields.values():
        w = field.widget
        if isinstance(w, (forms.TextInput, forms.NumberInput)):
            w.attrs.setdefault("class", _TEXT_CLS)
        elif isinstance(w, forms.Select):
            w.attrs.setdefault("class", _SELECT_CLS)
        elif isinstance(w, forms.Textarea):
            w.attrs.setdefault("class", _TEXTAREA_CLS)
            w.attrs.setdefault("rows", "3")
        elif isinstance(w, forms.CheckboxInput):
            w.attrs.setdefault("class", _CHECKBOX_CLS)


class SchoolSubjectForm(forms.ModelForm):
    """
    Create or edit a school subject.
    The default_sba_weighting + default_exam_weighting must sum to 100.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_tailwind(self)
        self.fields["curriculum"].queryset = Curriculum.objects.filter(is_active=True)

    def clean(self):
        cleaned = super().clean()
        sba = cleaned.get("default_sba_weighting", 0)
        exam = cleaned.get("default_exam_weighting", 0)
        if sba + exam != 100:
            raise ValidationError(
                f"Default SBA ({sba}%) and Exam ({exam}%) must sum to 100%."
            )
        return cleaned

    class Meta:
        model = SchoolSubject
        fields = [
            "curriculum", "department", "name", "code",
            "lurits_code", "subject_type", "is_home_language",
            "default_sba_weighting", "default_exam_weighting",
            "default_pass_mark", "sort_order", "is_active",
        ]


class SubjectOfferingForm(forms.ModelForm):
    """
    Add a subject to a grade with assessment weighting.
    The grade is pre-set from the URL and excluded from the form.
    Defaults are populated from the subject's default weightings.
    """

    def __init__(self, *args, **kwargs):
        self.grade = kwargs.pop("grade", None)
        super().__init__(*args, **kwargs)
        _apply_tailwind(self)

        # Only show subjects not already offered at this grade
        if self.grade:
            offered_ids = SubjectOffering.objects.filter(
                grade=self.grade, is_active=True
            ).values_list("subject_id", flat=True)
            self.fields["subject"].queryset = SchoolSubject.objects.filter(
                is_active=True
            ).exclude(id__in=offered_ids).order_by("sort_order", "name")
        else:
            self.fields["subject"].queryset = SchoolSubject.objects.filter(is_active=True)

    class Meta:
        model = SubjectOffering
        fields = ["subject", "is_compulsory", "sba_weighting", "exam_weighting", "pass_mark"]
        help_texts = {
            "sba_weighting": "CAPS FET: 25 | CAPS Senior: 40 | CAPS Foundation: 100",
            "exam_weighting": "Must sum to 100 with SBA weighting",
        }


class SubjectOfferingUpdateForm(forms.ModelForm):
    """Edit weighting and pass mark for an existing offering."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_tailwind(self)

    class Meta:
        model = SubjectOffering
        fields = ["is_compulsory", "sba_weighting", "exam_weighting", "pass_mark", "is_active"]


class AssessmentTaskForm(forms.ModelForm):
    """
    Create or edit an assessment task.
    subject_offering is set from the URL in the view - not shown in the form.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_tailwind(self)
        self.fields["sba_weight"].required = False

    def clean(self):
        cleaned = super().clean()
        is_sba = cleaned.get("is_sba", True)
        task_type = cleaned.get("task_type", "")

        # Examinations should not be marked as SBA
        if task_type == AssessmentTask.TYPE_EXAM and is_sba:
            raise ValidationError(
                "Tasks of type 'Examination' should not be marked as SBA. "
                "Examinations contribute to the exam weighting component, not SBA."
            )

        return cleaned

    class Meta:
        model = AssessmentTask
        fields = [
            "term_number", "name", "task_type",
            "max_marks", "is_sba", "sba_weight",
            "sequence", "typical_week",
        ]