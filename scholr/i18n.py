"""
Translating text that reaches pages from Python: form labels, help text, choices and status names.

They are translated as they are shown, against the catalogue, so models and forms keep plain
English and need no migrations. manage.py build_i18n_catalogue lists them for translators.
"""
from django import forms
from django.forms.utils import pretty_name
from django.utils.translation import gettext


def translated(value):
    """The translation of a piece of English text, or the text itself when there is none."""
    if value is None or value == '':
        return value
    return gettext(str(value))


def translate_form(form):
    for name, field in form.fields.items():
        field.label = translated(field.label if field.label is not None else pretty_name(name))
        if field.help_text:
            field.help_text = translated(field.help_text)
        if isinstance(field, forms.ChoiceField) and not isinstance(field, forms.ModelChoiceField):
            field.choices = [
                (value, translated(label) if isinstance(label, str) else label) for value, label in field.choices
            ]
    return form


class TranslatedFormMixin:
    """For forms outside the partner portal: labels, help text and choices in the reader's language."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        translate_form(self)
