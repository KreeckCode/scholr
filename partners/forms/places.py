from decimal import Decimal

from django import forms

from accounts.models import User
from partners.exceptions import PlaceError
from partners.models import LocationEvent, TrainingLocation
from partners.services.places import PLACES, parse_position
from partners.services.scope import PROGRAMME_STAFF_ROLES

from .base import DateInput, StyledFormMixin

SOURCES = TrainingLocation.PositionSource


class TimeInput(forms.TimeInput):
    input_type = 'time'

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format='%H:%M')


class LocationPlaceForm(StyledFormMixin, forms.ModelForm):
    """Position and travel details. The map picker fills latitude, longitude and source."""

    latitude = forms.DecimalField(
        required=False, max_digits=9, decimal_places=6, min_value=Decimal('-90'), max_value=Decimal('90'),
        widget=forms.NumberInput(attrs={'step': 'any', 'inputmode': 'decimal', 'data-place': 'lat'}),
    )
    longitude = forms.DecimalField(
        required=False, max_digits=9, decimal_places=6, min_value=Decimal('-180'), max_value=Decimal('180'),
        widget=forms.NumberInput(attrs={'step': 'any', 'inputmode': 'decimal', 'data-place': 'lng'}),
    )
    pasted = forms.CharField(
        required=False, max_length=2000, label='Or paste coordinates or a map link',
        widget=forms.TextInput(attrs={'placeholder': '-23.904512, 29.468901', 'autocomplete': 'off'}),
        help_text='Plain coordinates, or a full Google Maps, Apple Maps or OpenStreetMap address.',
    )
    position_source = forms.ChoiceField(
        required=False, choices=[('', '')] + [(value, label) for value, label in SOURCES.choices],
        widget=forms.HiddenInput(attrs={'data-place': 'source'}),
    )
    clear_position = forms.BooleanField(required=False, label='Remove the map position')

    class Meta:
        model = TrainingLocation
        fields = [
            'address', 'directions', 'nearest_airport', 'travel_notes',
            'arrival_contact_name', 'arrival_contact_phone',
        ]
        widgets = {
            'directions': forms.Textarea(attrs={'rows': 5}),
            'travel_notes': forms.Textarea(attrs={'rows': 4}),
            'arrival_contact_phone': forms.TextInput(attrs={'placeholder': '+27821234567', 'inputmode': 'tel'}),
        }
        labels = {'directions': 'How to get there'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['latitude'].initial = self.instance.latitude
        self.fields['longitude'].initial = self.instance.longitude
        self.position_changed = False

    def clean(self):
        cleaned = super().clean()
        lat, lng = cleaned.get('latitude'), cleaned.get('longitude')
        source = cleaned.get('position_source') or SOURCES.PIN

        if cleaned.get('pasted'):
            try:
                lat, lng = parse_position(cleaned['pasted'])
            except PlaceError as exc:
                self.add_error('pasted', exc.message)
                return cleaned
            source = SOURCES.PASTED

        if cleaned.get('clear_position'):
            lat = lng = None
        elif (lat is None) != (lng is None):
            self.add_error('latitude', 'Give both latitude and longitude, or neither.')
            return cleaned

        if lat is not None:
            lat, lng = Decimal(lat).quantize(PLACES), Decimal(lng).quantize(PLACES)
        before = (self.instance.latitude, self.instance.longitude)
        self.position_changed = (lat, lng) != before
        if source not in (SOURCES.PIN, SOURCES.DEVICE, SOURCES.PASTED):
            source = SOURCES.PIN
        cleaned.update({'latitude': lat, 'longitude': lng, 'position_source': source})
        return cleaned

    def save(self, commit=True):
        location = super().save(commit=False)
        location.latitude = self.cleaned_data['latitude']
        location.longitude = self.cleaned_data['longitude']
        if commit:
            location.save()
        return location


class LocationEventForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = LocationEvent
        fields = [
            'kind', 'title', 'status', 'cohort', 'starts_on', 'ends_on', 'starts_at', 'venue', 'notes', 'attendees',
        ]
        widgets = {
            'starts_on': DateInput(),
            'ends_on': DateInput(),
            'starts_at': TimeInput(),
            'notes': forms.Textarea(attrs={'rows': 4}),
            'attendees': forms.CheckboxSelectMultiple,
        }
        labels = {'starts_on': 'Date', 'ends_on': 'Last day (if more than one)', 'starts_at': 'Start time'}

    def __init__(self, *args, location, allow_attendees, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.location = location
        self.fields['cohort'].queryset = location.cohorts.order_by('-starts_on')
        self.fields['cohort'].empty_label = 'No particular cohort'
        if allow_attendees:
            self.fields['attendees'].queryset = (
                User.objects.filter(role__in=PROGRAMME_STAFF_ROLES, is_active=True).order_by('first_name', 'last_name')
            )
            self.fields['attendees'].label_from_instance = lambda user: user.get_full_name() or user.username
        else:
            del self.fields['attendees']
        if not self.instance.pk:
            del self.fields['status']

    def clean(self):
        cleaned = super().clean()
        starts_on, ends_on = cleaned.get('starts_on'), cleaned.get('ends_on')
        if starts_on and ends_on and ends_on < starts_on:
            self.add_error('ends_on', 'The last day cannot be before the first.')
        return cleaned
