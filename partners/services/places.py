"""
Where training locations are, how to reach them, and what is happening there.

A location marked sensitive never leaves this module with its real position for the map:
the map gets the centre of the 0.1 degree grid square it falls in (about 11 km across),
with no name, partner, contacts or events. The exact position and directions are shown only
on the location's own page, and every programme staff view of that page is recorded.
"""

import math
import re
from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.urls import reverse
from django.utils import timezone

from partners.exceptions import PlaceError
from partners.models import (
    Cohort,
    CohortSession,
    LocationAccessLog,
    LocationEvent,
    LocationMembership,
    TrainingLocation,
)

from . import scope

GRID = Decimal('0.1')
PLACES = Decimal('0.000001')
APPROXIMATE_RADIUS_M = 8000
EARTH_RADIUS_KM = 6371.0088
NEARBY_KM = 150
MAX_TRIP_STOPS = 10
EVENT_HORIZON_DAYS = 365

SENSITIVE = Q(is_sensitive=True) | Q(organisation__is_sensitive=True)

STATUS_COLOURS = {
    TrainingLocation.Status.ACTIVE: 'teal',
    TrainingLocation.Status.PREPARING: 'orange',
    TrainingLocation.Status.PAUSED: 'slate',
    TrainingLocation.Status.CLOSED: 'navy',
}

PARSE_HELP = (
    'Paste coordinates such as -23.904512, 29.468901, or a full Google Maps, Apple Maps or '
    'OpenStreetMap link.'
)


# ==================== POSITIONS ====================

def _coordinate(value):
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number.quantize(PLACES) if number.is_finite() else None


def _checked(lat, lng):
    lat, lng = _coordinate(lat), _coordinate(lng)
    if lat is None or lng is None or not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return None
    if lat == 0 and lng == 0:
        return None
    return lat, lng


PAIR = r'(-?\d{1,3}(?:\.\d+)?)\s*[,\s]\s*(-?\d{1,3}(?:\.\d+)?)'
PLACE_PIN = re.compile(r'!3d(-?\d{1,3}\.\d+)!4d(-?\d{1,3}\.\d+)')
VIEW_PATTERNS = [
    re.compile(r'@(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)'),
    re.compile(r'#map=\d+(?:\.\d+)?/(-?\d{1,3}\.\d+)/(-?\d{1,3}\.\d+)'),
    re.compile(r'^geo:' + PAIR),
    re.compile(r'^\s*' + PAIR + r'\s*$'),
]
QUERY_KEYS = ('destination', 'daddr', 'q', 'query', 'll', 'sll', 'center')
SHORT_LINK_HOSTS = ('goo.gl', 'maps.app.goo.gl', 'g.co')


def _from_query(parsed):
    query = parse_qs(parsed.query)
    if 'mlat' in query and 'mlon' in query:
        position = _checked(query['mlat'][0], query['mlon'][0])
        if position:
            return position
    for key in QUERY_KEYS:
        for value in query.get(key, []):
            match = re.match(r'^\s*' + PAIR + r'\s*$', value)
            if match and (position := _checked(*match.groups())):
                return position
    return None


def parse_position(text):
    """
    Read a latitude and longitude from what people paste: plain coordinates, a geo: URI, or a
    Google Maps, Apple Maps or OpenStreetMap address. A marked place wins over the map's view
    centre. Short links are refused rather than followed, so the server never makes requests
    on a user's behalf. Returns (Decimal, Decimal) or raises PlaceError.
    """
    raw = unquote((text or '').strip())
    if not raw:
        raise PlaceError(PARSE_HELP)

    parsed = urlparse(raw) if raw.lower().startswith(('http://', 'https://')) else None
    if parsed and parsed.hostname and parsed.hostname.lower() in SHORT_LINK_HOSTS:
        raise PlaceError(
            'Short links cannot be read. Open the link, then copy the full address from the browser, '
            'or copy the coordinates instead.'
        )

    match = PLACE_PIN.search(raw)
    if match and (position := _checked(*match.groups())):
        return position
    if parsed and (position := _from_query(parsed)):
        return position
    for pattern in VIEW_PATTERNS:
        match = pattern.search(raw)
        if match and (position := _checked(*match.groups())):
            return position
    raise PlaceError(PARSE_HELP)


def _snap(value):
    cell = (Decimal(value) / GRID).to_integral_value(rounding=ROUND_FLOOR)
    return (cell * GRID + GRID / 2).quantize(PLACES)


def approximate(lat, lng):
    """The centre of the 0.1 degree square holding the point. The same point always lands in the same square."""
    return _snap(lat), _snap(lng)


def distance_km(lat_a, lng_a, lat_b, lng_b):
    lat_a, lng_a, lat_b, lng_b = map(math.radians, (float(lat_a), float(lng_a), float(lat_b), float(lng_b)))
    h = math.sin((lat_b - lat_a) / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin((lng_b - lng_a) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def map_position(location):
    """What the map may show for a location: exact for ordinary ones, a grid square for sensitive ones."""
    if not location.has_position:
        return None
    if location.hides_identities:
        lat, lng = approximate(location.latitude, location.longitude)
        return {'lat': float(lat), 'lng': float(lng), 'approximate': True}
    return {'lat': float(location.latitude), 'lng': float(location.longitude), 'approximate': False}


def exact_position(location):
    if not location.has_position:
        return None
    return {'lat': float(location.latitude), 'lng': float(location.longitude)}


def starting_view(location):
    """Where the pin picker opens when a location has no position: the middle of its country's other locations."""
    others = list(
        TrainingLocation.objects.filter(country=location.country, latitude__isnull=False)
        .exclude(pk=location.pk).exclude(SENSITIVE).values_list('latitude', 'longitude')[:50]
    )
    if others:
        return {
            'lat': sum(float(lat) for lat, _ in others) / len(others),
            'lng': sum(float(lng) for _, lng in others) / len(others),
            'zoom': 7,
        }
    return {'lat': 2.0, 'lng': 22.0, 'zoom': 3}


# ==================== LINKS ====================

def _digits(phone):
    return re.sub(r'\D', '', phone or '')


def whatsapp_url(phone):
    digits = _digits(phone)
    return f'https://wa.me/{digits}' if len(digits) >= 9 else ''


def directions_links(lat, lng, label=''):
    coordinates = f'{Decimal(lat).quantize(PLACES)},{Decimal(lng).quantize(PLACES)}'
    google = 'https://www.google.com/maps/dir/?' + urlencode({'api': 1, 'destination': coordinates})
    share_text = f'{label}: {google}' if label else google
    return {
        'coordinates': coordinates,
        'google': google,
        'apple': 'https://maps.apple.com/?' + urlencode({'daddr': coordinates}),
        'osm': f'https://www.openstreetmap.org/?mlat={lat}&mlon={lng}#map=16/{lat}/{lng}',
        'share': 'https://wa.me/?' + urlencode({'text': share_text}),
    }


def route_url(stops):
    """One Google Maps route through every stop, starting from wherever the traveller is."""
    if not stops:
        return ''
    points = [f"{stop['lat']},{stop['lng']}" for stop in stops]
    params = {'api': 1, 'destination': points[-1], 'travelmode': 'driving'}
    if len(points) > 1:
        params['waypoints'] = '|'.join(points[:-1])
    return 'https://www.google.com/maps/dir/?' + urlencode(params)


# ==================== PERMISSIONS ====================

def can_edit_place(user, location):
    """The location's coordinators and programme staff keep the position, directions and events up to date."""
    return scope.role_at(user, location) in (scope.STAFF, scope.COORDINATOR)


def _require_editor(user, location):
    if not can_edit_place(user, location):
        raise PermissionDenied('Only the location coordinator or programme staff can change this.')


def record_access(location, user, kind, ip_address=None):
    """Programme staff opening a sensitive location's exact details leave a record. Its own team does not."""
    if location.hides_identities and scope.role_at(user, location) == scope.STAFF:
        LocationAccessLog.objects.create(location=location, user=user, kind=kind, ip_address=ip_address or None)


# ==================== EDITING ====================

@transaction.atomic
def save_place(location, actor, position_changed=False, source=''):
    """Save directions and position. A position set by a person is confirmed; an estimated one is not."""
    _require_editor(actor, location)
    if not location.has_position:
        location.latitude = location.longitude = None
        location.position_source = ''
        location.position_confirmed_at = None
        location.position_confirmed_by = None
    elif position_changed:
        location.position_source = source or TrainingLocation.PositionSource.PIN
        if location.position_source == TrainingLocation.PositionSource.GEOCODED:
            location.position_confirmed_at = None
            location.position_confirmed_by = None
        else:
            location.position_confirmed_at = timezone.now()
            location.position_confirmed_by = actor
    location.save()
    return location


@transaction.atomic
def save_event(event, actor, attendees=None):
    """Create or update an event. Only programme staff choose which METP staff travel to it."""
    _require_editor(actor, event.location)
    if event.cohort_id and event.cohort.location_id != event.location_id:
        raise PlaceError('That cohort belongs to another location.')
    if event.ends_on and event.ends_on < event.starts_on:
        raise PlaceError('The event cannot end before it starts.')
    if not event.pk:
        event.created_by = actor
    event.save()
    if attendees is not None and scope.is_programme_staff(actor):
        event.attendees.set([user for user in attendees if scope.is_programme_staff(user)])
    return event


@transaction.atomic
def cancel_event(event, actor):
    _require_editor(actor, event.location)
    if event.status == LocationEvent.Status.CANCELLED:
        raise PlaceError('This event is already cancelled.')
    event.status = LocationEvent.Status.CANCELLED
    event.save(update_fields=['status', 'updated_at'])
    return event


# ==================== READING ====================

def upcoming_events(today=None):
    today = today or timezone.localdate()
    return LocationEvent.objects.filter(
        status__in=[LocationEvent.Status.PLANNED, LocationEvent.Status.CONFIRMED],
        starts_on__gte=today,
        starts_on__lte=today + timedelta(days=EVENT_HORIZON_DAYS),
    ).order_by('starts_on', 'starts_at')


def _event_summary(event):
    return {
        'kind': event.kind,
        'kind_label': event.get_kind_display(),
        'title': event.title,
        'date': event.starts_on.isoformat(),
        'date_label': f'{event.starts_on:%d %b %Y}',
    }


def map_data(today=None):
    """Everything the staff map draws, in one payload of plain values. Two queries in total."""
    today = today or timezone.localdate()
    locations = (
        TrainingLocation.objects.select_related('organisation__partnership')
        .annotate(running=Count('cohorts', filter=Q(cohorts__status=Cohort.Status.RUNNING), distinct=True))
        .order_by('country', 'name')
    )
    next_event = {}
    for event in upcoming_events(today):
        next_event.setdefault(event.location_id, event)

    features, missing = [], []
    for location in locations:
        hidden = location.hides_identities
        partnership = getattr(location.organisation, 'partnership', None) if not hidden else None
        place_url = reverse('partners:location_place', kwargs={'location_id': location.public_id})
        position = map_position(location)
        if position is None:
            missing.append({
                'name': f'Sensitive location in {location.country_name}' if hidden else location.name,
                'partner': '' if hidden else location.organisation.name,
                'country_name': location.country_name,
                'url': reverse('partners:location_place_edit', kwargs={'location_id': location.public_id}),
            })
            continue
        event = None if hidden else next_event.get(location.id)
        features.append({
            'id': str(location.public_id),
            **position,
            'sensitive': hidden,
            'confirmed': location.position_is_confirmed,
            'name': 'Sensitive location' if hidden else location.name,
            'partner': '' if hidden else location.organisation.name,
            'partner_id': '' if hidden else str(location.organisation.public_id),
            'country': location.country,
            'country_name': location.country_name,
            'region': '' if hidden else location.region,
            'city': '' if hidden else location.city,
            'status': location.status,
            'status_label': location.get_status_display(),
            'colour': STATUS_COLOURS.get(location.status, 'slate'),
            'stage': partnership.stage if partnership else '',
            'stage_label': partnership.get_stage_display() if partnership else '',
            'running': location.running,
            'next_event': _event_summary(event) if event else None,
            'panel_url': reverse('partners:map_panel', kwargs={'location_id': location.public_id}),
            'place_url': place_url,
        })
    return {'features': features, 'missing': missing, 'today': today.isoformat()}


def nearby(location, radius_km=NEARBY_KM, limit=5):
    """Other ordinary locations within reach, nearest first. Sensitive locations are never listed."""
    if not location.has_position:
        return []
    lat, lng = float(location.latitude), float(location.longitude)
    lat_span = radius_km / 111.0
    lng_span = radius_km / max(1.0, 111.0 * math.cos(math.radians(lat)))
    candidates = (
        TrainingLocation.objects.exclude(pk=location.pk).exclude(SENSITIVE)
        .exclude(status=TrainingLocation.Status.CLOSED)
        .filter(
            latitude__gte=lat - lat_span, latitude__lte=lat + lat_span,
            longitude__gte=lng - lng_span, longitude__lte=lng + lng_span,
        )
        .select_related('organisation')
    )
    found = []
    for other in candidates:
        km = distance_km(lat, lng, other.latitude, other.longitude)
        if km <= radius_km:
            found.append({'location': other, 'km': round(km)})
    return sorted(found, key=lambda item: item['km'])[:limit]


def team(location):
    return (
        LocationMembership.objects.filter(location=location, is_active=True)
        .select_related('user').order_by('role', 'user__first_name')
    )


def location_brief(location, today=None, event_limit=3):
    """The facts a traveller needs about one location: people, activity, events and how to get there."""
    today = today or timezone.localdate()
    members = list(team(location))
    running = list(
        location.cohorts.filter(status=Cohort.Status.RUNNING)
        .prefetch_related(Prefetch(
            'sessions',
            queryset=CohortSession.objects.filter(session_date__gte=today)
            .exclude(status=CohortSession.Status.CANCELLED).select_related('course').order_by('session_date'),
            to_attr='upcoming_sessions',
        ))
        .order_by('name')
    )
    for cohort in running:
        cohort.next_session = cohort.upcoming_sessions[0] if cohort.upcoming_sessions else None
    events = list(
        upcoming_events(today).filter(location=location)
        .select_related('cohort').prefetch_related('attendees')[:event_limit]
    )
    links = directions_links(location.latitude, location.longitude, location.name) if location.has_position else None
    return {
        'coordinators': [m for m in members if m.role == LocationMembership.Role.COORDINATOR],
        'facilitators': [m for m in members if m.role == LocationMembership.Role.FACILITATOR],
        'running_cohorts': running,
        'events': events,
        'links': links,
        'contact': location.organisation.primary_contact,
    }


# ==================== TRIPS ====================

def order_nearest(stops):
    """Greedy nearest-neighbour from the first stop. Good enough for ten stops."""
    if len(stops) < 3:
        return stops
    remaining = stops[1:]
    route = [stops[0]]
    while remaining:
        here = route[-1]
        nearest = min(remaining, key=lambda stop: distance_km(here['lat'], here['lng'], stop['lat'], stop['lng']))
        route.append(nearest)
        remaining.remove(nearest)
    return route


def plan_trip(public_ids, user, nearest_first=False, today=None):
    """
    Stops for a trip, in the order chosen. Sensitive locations and locations without a position
    are left out and reported, because a trip sheet would print their exact details.
    """
    if not scope.is_programme_staff(user):
        raise PermissionDenied('Programme staff only.')
    today = today or timezone.localdate()
    seen, ids = set(), []
    for value in public_ids:
        if value not in seen:
            seen.add(value)
            ids.append(value)
    if not ids:
        raise PlaceError('Choose at least one location for the trip.')
    if len(ids) > MAX_TRIP_STOPS:
        raise PlaceError(f'A trip can have up to {MAX_TRIP_STOPS} stops.')

    by_id = {
        str(location.public_id): location
        for location in TrainingLocation.objects.filter(public_id__in=ids).select_related('organisation')
    }
    stops, skipped = [], []
    for public_id in ids:
        location = by_id.get(public_id)
        if location is None:
            continue
        if location.hides_identities:
            skipped.append({'name': f'Sensitive location in {location.country_name}', 'reason': 'sensitive'})
        elif not location.has_position:
            skipped.append({'name': location.name, 'reason': 'no position'})
        else:
            stops.append({
                'location': location,
                'lat': float(location.latitude),
                'lng': float(location.longitude),
            })
    if not stops:
        raise PlaceError('None of the chosen locations can go on a trip sheet.')
    if nearest_first:
        stops = order_nearest(stops)

    total = 0.0
    for index, stop in enumerate(stops):
        if index:
            previous = stops[index - 1]
            stop['leg_km'] = round(distance_km(previous['lat'], previous['lng'], stop['lat'], stop['lng']))
            total += stop['leg_km']
        else:
            stop['leg_km'] = None
        stop['brief'] = location_brief(stop['location'], today, event_limit=5)
    return {'stops': stops, 'skipped': skipped, 'total_km': round(total), 'route_url': route_url(stops)}
