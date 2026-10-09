"""
The staff map, each location's position and directions page, location events and trip sheets.
"""

import uuid

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView

from partners.exceptions import PartnersError
from partners.forms import LocationEventForm, LocationPlaceForm
from partners.mixins import LocationMixin, ProgrammeStaffRequiredMixin
from partners.models import LocationAccessLog, LocationEvent, TrainingLocation
from partners.services import places, scope
from partners.utils import client_ip


def place_url(location):
    return reverse('partners:location_place', kwargs={'location_id': location.public_id})


def map_config(**extra):
    return {
        'tileUrl': settings.PARTNERS_MAP_TILE_URL,
        'attribution': settings.PARTNERS_MAP_ATTRIBUTION,
        'maxZoom': settings.PARTNERS_MAP_MAX_ZOOM,
        'approximateRadius': places.APPROXIMATE_RADIUS_M,
        **extra,
    }


# ==================== STAFF MAP ====================

class MapView(ProgrammeStaffRequiredMixin, TemplateView):
    template_name = 'partners/staff/map.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        events = list(
            places.upcoming_events(today).select_related('location__organisation', 'cohort')
            .prefetch_related('attendees')[:12]
        )
        context.update({
            'config': map_config(
                dataUrl=reverse('partners:map_data'),
                tripUrl=reverse('partners:map_trip'),
                maxStops=places.MAX_TRIP_STOPS,
            ),
            'statuses': TrainingLocation.Status.choices,
            'events': events,
        })
        return context


class MapDataView(ProgrammeStaffRequiredMixin, View):
    def get(self, request):
        response = JsonResponse(places.map_data())
        response['Cache-Control'] = 'private, no-store'
        return response


class MapPanelView(ProgrammeStaffRequiredMixin, TemplateView):
    """The details shown beside the map for one dot. Sensitive locations get only a pointer to their page."""

    template_name = 'partners/staff/_map_panel.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        location = get_object_or_404(
            TrainingLocation.objects.select_related('organisation__partnership', 'licence')
            .prefetch_related('organisation__contacts'),
            public_id=self.kwargs['location_id'],
        )
        context['location'] = location
        if not location.hides_identities:
            context.update(places.location_brief(location))
            context['nearby'] = places.nearby(location)
            context['nearby_km'] = places.NEARBY_KM
        return context

    def render_to_response(self, context, **kwargs):
        response = super().render_to_response(context, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response


class TripSheetView(ProgrammeStaffRequiredMixin, View):
    """A printable trip with every stop's contacts, directions and events, for travel without signal."""

    def get(self, request):
        from weasyprint import HTML

        ids = []
        for value in request.GET.getlist('location'):
            try:
                ids.append(str(uuid.UUID(value)))
            except ValueError:
                continue
        try:
            trip = places.plan_trip(ids, request.user, nearest_first=request.GET.get('order') == 'nearest')
        except PartnersError as exc:
            messages.error(request, exc.message)
            return redirect('partners:map')

        html = render_to_string('partners/print/trip.html', {
            'brand': settings.SITE_BRAND,
            'trip': trip,
            'traveller': request.user,
            'generated_at': timezone.localtime(),
        }, request=request)
        pdf = HTML(string=html, base_url=request.build_absolute_uri('/')).write_pdf()
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="metp-trip-{timezone.localdate():%Y-%m-%d}.pdf"'
        response['Cache-Control'] = 'private, no-store'
        return response


# ==================== LOCATION PLACE ====================

class PlaceEditorMixin(LocationMixin):
    """The location's coordinators and programme staff only."""

    def check_editor(self):
        if not places.can_edit_place(self.request.user, self.location):
            raise PermissionDenied('Only the location coordinator or programme staff can change this.')


class LocationPlaceView(LocationMixin, TemplateView):
    template_name = 'partners/portal/place.html'

    def get(self, request, *args, **kwargs):
        places.record_access(self.location, request.user, LocationAccessLog.Kind.DETAILS, client_ip(request))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        location = self.location
        context.update(places.location_brief(location, event_limit=20))
        context.update({
            'can_edit_place': places.can_edit_place(self.request.user, location),
            'config': map_config(position=places.exact_position(location)),
            'nearby': [] if location.hides_identities else places.nearby(location),
            'nearby_km': places.NEARBY_KM,
            'access_log': (
                location.access_logs.select_related('user')[:8]
                if location.hides_identities and self.portal_role == scope.STAFF else []
            ),
        })
        return context


class LocationPlaceUpdateView(PlaceEditorMixin, UpdateView):
    template_name = 'partners/portal/place_form.html'
    form_class = LocationPlaceForm

    def get_object(self, queryset=None):
        self.check_editor()
        return self.location

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['config'] = map_config(
            searchUrl=settings.PARTNERS_MAP_SEARCH_URL,
            countryCode=(self.location.country or '').lower(),
            position=places.exact_position(self.location),
            fallback=places.starting_view(self.location),
        )
        return context

    def form_valid(self, form):
        location = form.save(commit=False)
        try:
            places.save_place(
                location, self.request.user,
                position_changed=form.position_changed,
                source=form.cleaned_data['position_source'],
            )
        except PartnersError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)
        messages.success(self.request, 'Position and directions saved.')
        return redirect(place_url(location))


# ==================== EVENTS ====================

class EventFormMixin(PlaceEditorMixin):
    template_name = 'partners/portal/event_form.html'
    form_class = LocationEventForm
    model = LocationEvent

    def get_form_kwargs(self):
        self.check_editor()
        kwargs = super().get_form_kwargs()
        kwargs['location'] = self.location
        kwargs['allow_attendees'] = scope.is_programme_staff(self.request.user)
        return kwargs

    def form_valid(self, form):
        event = form.save(commit=False)
        try:
            places.save_event(event, self.request.user, attendees=form.cleaned_data.get('attendees'))
        except PartnersError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)
        messages.success(self.request, f'{event.title} saved.')
        return redirect(place_url(self.location))


class EventCreateView(EventFormMixin, CreateView):
    def get_initial(self):
        initial = super().get_initial()
        initial['title'] = f'Graduation at {self.location.name}'
        return initial


class EventUpdateView(EventFormMixin, UpdateView):
    context_object_name = 'event'

    def get_object(self, queryset=None):
        return get_object_or_404(LocationEvent, public_id=self.kwargs['event_id'], location=self.location)


class EventCancelView(PlaceEditorMixin, View):
    http_method_names = ['post']

    def post(self, request, location_id, event_id):
        self.check_editor()
        event = get_object_or_404(LocationEvent, public_id=event_id, location=self.location)
        try:
            places.cancel_event(event, request.user)
        except PartnersError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, f'{event.title} cancelled.')
        return redirect(place_url(self.location))
