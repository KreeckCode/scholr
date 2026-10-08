from django import forms

INPUT_CLASSES = (
    'block w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 '
    'shadow-sm transition focus:border-teal-500 focus:outline-none focus:ring-2 focus:ring-teal-500/30 '
    'disabled:bg-slate-50 disabled:text-slate-500'
)
CHECKBOX_CLASSES = 'h-4 w-4 rounded border-slate-300 text-teal-600 focus:ring-teal-500'
FILE_CLASSES = (
    'block w-full text-sm text-slate-600 file:mr-4 file:rounded-full file:border-0 '
    'file:bg-slate-100 file:px-4 file:py-2 file:text-sm file:font-medium file:text-slate-700 '
    'hover:file:bg-slate-200'
)


class StyledFormMixin:
    """Applies the portal's Tailwind classes to every widget so templates stay markup-only."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple, forms.RadioSelect)):
                widget.attrs.setdefault('class', CHECKBOX_CLASSES)
            elif isinstance(widget, forms.ClearableFileInput):
                widget.attrs.setdefault('class', FILE_CLASSES)
            else:
                widget.attrs.setdefault('class', INPUT_CLASSES)
            if isinstance(widget, forms.Textarea):
                widget.attrs.setdefault('rows', 4)


class DateInput(forms.DateInput):
    input_type = 'date'

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format='%Y-%m-%d')
