import datetime

from django import forms

from ..models.attendance import AbsenceReason, AttendanceRecord

_SELECT_CLS = (
    "w-full px-2 py-1.5 border border-gray-300 rounded text-sm bg-white "
    "focus:outline-none focus:ring-2 focus:ring-blue-500"
)
_TEXT_CLS = (
    "w-full px-2 py-1.5 border border-gray-300 rounded text-sm "
    "focus:outline-none focus:ring-2 focus:ring-blue-500"
)
_TIME_CLS = (
    "w-24 px-2 py-1.5 border border-gray-300 rounded text-sm "
    "focus:outline-none focus:ring-2 focus:ring-blue-500"
)


class DateSelectForm(forms.Form):
    """Select which date to take or view the register for."""

    date = forms.DateField(
        widget=forms.DateInput(
            attrs={"type": "date", "class": _TEXT_CLS}
        )
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = datetime.date.today()
        self.fields["date"].label = "Date"