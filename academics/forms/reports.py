from django import forms

from ..models.reports import (
    ConductRating, ReportCardTemplate, TeacherComment,
)
from ..models.structure import GradeStream
from ..models.school import AcademicYear

_SELECT_CLS = (
    "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white "
    "focus:outline-none focus:ring-2 focus:ring-blue-500"
)
_TEXT_CLS = (
    "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm "
    "focus:outline-none focus:ring-2 focus:ring-blue-500"
)
_TEXTAREA_CLS = (
    "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm "
    "focus:outline-none focus:ring-2 focus:ring-blue-500"
)


class GenerateReportCardsForm(forms.Form):
    """Select which stream and term to generate report cards for."""

    stream = forms.ModelChoiceField(
        queryset=GradeStream.objects.none(),
        widget=forms.Select(attrs={"class": _SELECT_CLS}),
    )
    term_number = forms.ChoiceField(
        choices=[(1, "Term 1"), (2, "Term 2"), (3, "Term 3"), (4, "Term 4")],
        widget=forms.Select(attrs={"class": _SELECT_CLS}),
    )
    template = forms.ModelChoiceField(
        queryset=ReportCardTemplate.objects.filter(is_active=True),
        required=False,
        help_text="Leave blank to use the default template",
        widget=forms.Select(attrs={"class": _SELECT_CLS}),
    )

    def __init__(self, *args, **kwargs):
        academic_year = kwargs.pop("academic_year", None)
        super().__init__(*args, **kwargs)
        if academic_year:
            self.fields["stream"].queryset = GradeStream.objects.filter(
                academic_year=academic_year, is_active=True
            ).select_related("grade").order_by("grade__grade_number", "name")


class ConductRatingForm(forms.ModelForm):
    """
    Conduct and effort rating form for one learner.
    Used inline in the bulk conduct capture view.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            w = field.widget
            if isinstance(w, forms.Select):
                w.attrs.setdefault("class", _SELECT_CLS)
            elif isinstance(w, forms.TextInput) or isinstance(w, forms.NumberInput):
                w.attrs.setdefault("class", _TEXT_CLS)
            elif isinstance(w, forms.Textarea):
                w.attrs.setdefault("class", _TEXTAREA_CLS)
                w.attrs.setdefault("rows", "2")

    class Meta:
        model = ConductRating
        fields = [
            "effort_rating", "conduct_rating",
            "days_absent", "days_late",
            "homeroom_comment", "principal_comment",
        ]