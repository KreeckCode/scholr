"""
Forms for grade promotion and learner migration.
"""
from django import forms

from academics.models.structure import Grade, GradeStream
from academics.models.school import AcademicYear
from ..models.promotion import LearnerPromotion


class CreateBulkPromotionForm(forms.Form):
    """
    Initial form to start a bulk promotion batch.
    Selects which learners to promote (by year/grade/stream).
    """

    label = forms.CharField(
        max_length=200,
        label='Batch name',
        help_text='E.g. "Year-end 2025 — Grade 9A → 10A"',
        widget=forms.TextInput(attrs={
            'class': 'field',
            'placeholder': 'Year-end 2025 — Grade 9A → 10A',
        }),
    )

    from_academic_year = forms.ModelChoiceField(
        queryset=AcademicYear.objects.all().order_by('-year'),
        label='From academic year',
        widget=forms.Select(attrs={'class': 'field'}),
    )

    from_grade = forms.ModelChoiceField(
        queryset=Grade.objects.all().order_by('grade_number'),
        label='From grade',
        widget=forms.Select(attrs={'class': 'field'}),
    )

    from_stream = forms.ModelChoiceField(
        queryset=GradeStream.objects.all(),
        required=False,
        label='From stream (optional)',
        empty_label='— All streams —',
        widget=forms.Select(attrs={'class': 'field'}),
        help_text='Leave blank to promote all learners in the grade.',
    )

    to_academic_year = forms.ModelChoiceField(
        queryset=AcademicYear.objects.all().order_by('-year'),
        label='To academic year',
        widget=forms.Select(attrs={'class': 'field'}),
    )

    notes = forms.CharField(
        required=False,
        label='Notes',
        widget=forms.Textarea(attrs={
            'class': 'field',
            'rows': 3,
            'placeholder': 'Optional notes about this promotion batch…',
        }),
    )


class PromotionDecisionForm(forms.Form):
    """
    Per-learner promotion decision.
    Used in a formset when configuring a batch.
    """

    STATUS_CHOICES = [
        (LearnerPromotion.STATUS_PROMOTED,  'Promote to next grade'),
        (LearnerPromotion.STATUS_RETAINED,  'Retain in current grade'),
        (LearnerPromotion.STATUS_GRADUATED, 'Graduated'),
        (LearnerPromotion.STATUS_WITHDRAWN, 'Withdrawn'),
    ]

    status = forms.ChoiceField(
        choices=STATUS_CHOICES,
        widget=forms.Select(attrs={'class': 'field text-xs'}),
    )

    to_grade = forms.ModelChoiceField(
        queryset=Grade.objects.all().order_by('grade_number'),
        required=False,
        widget=forms.Select(attrs={'class': 'field text-xs'}),
    )

    to_stream = forms.ModelChoiceField(
        queryset=GradeStream.objects.all(),
        required=False,
        empty_label='— Select stream —',
        widget=forms.Select(attrs={'class': 'field text-xs'}),
    )

    notes = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'field text-xs',
            'placeholder': 'Optional notes…',
        }),
    )

    def __init__(self, *args, **kwargs):
        from_grade = kwargs.pop('from_grade', None)
        super().__init__(*args, **kwargs)

        # Default to_grade is the next grade up
        if from_grade:
            next_grade = Grade.objects.filter(
                grade_number=from_grade.grade_number + 1
            ).first()
            if next_grade:
                self.fields['to_grade'].initial = next_grade