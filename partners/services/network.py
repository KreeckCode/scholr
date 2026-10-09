"""
Network-wide counts for programme staff: participants by country, region and location.
Each measure is its own aggregate, so joins never multiply rows.
"""

from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone

from partners.models import (
    FORWARD_STAGES,
    Cohort,
    CohortMembership,
    CohortSession,
    Licence,
    PartnerOrganisation,
    Partnership,
    SessionAttendance,
    Stage,
    TrainingLocation,
)
from partners.models.base import country_name

CACHE_KEY = 'partners:network:v1'
CACHE_SECONDS = 600


def _membership_counts():
    rows = CohortMembership.objects.values('cohort__location_id').annotate(
        enrolled=Count('id', filter=Q(status=CohortMembership.Status.ENROLLED)),
        completed=Count('id', filter=Q(status=CohortMembership.Status.COMPLETED)),
        withdrawn=Count('id', filter=Q(status=CohortMembership.Status.WITHDRAWN)),
        online=Count('id', filter=Q(access_order__isnull=False, access_withdrawn_at__isnull=True)),
    )
    return {row['cohort__location_id']: row for row in rows}


def _cohort_counts():
    rows = Cohort.objects.values('location_id').annotate(
        running=Count('id', filter=Q(status=Cohort.Status.RUNNING)),
        total=Count('id'),
    )
    return {row['location_id']: row for row in rows}


def _session_counts():
    rows = CohortSession.objects.filter(status=CohortSession.Status.HELD).values(
        'cohort__location_id',
    ).annotate(held=Count('id'))
    return {row['cohort__location_id']: row['held'] for row in rows}


def _attendance_rates():
    rows = SessionAttendance.objects.values('session__cohort__location_id').annotate(
        marks=Count('id'),
        attended=Count('id', filter=Q(status__in=SessionAttendance.ATTENDED)),
    )
    return {
        row['session__cohort__location_id']: (
            round(100 * row['attended'] / row['marks']) if row['marks'] else None
        )
        for row in rows
    }


def location_rows():
    """One row per location with every measure, for the network table and the CSV export."""
    memberships = _membership_counts()
    cohorts = _cohort_counts()
    sessions = _session_counts()
    attendance = _attendance_rates()
    locations = TrainingLocation.objects.select_related('organisation', 'licence').order_by(
        'country', 'region', 'name',
    )
    rows = []
    for location in locations:
        counts = memberships.get(location.pk, {})
        cohort_counts = cohorts.get(location.pk, {})
        rows.append({
            'location': location,
            'country': location.country,
            'country_name': country_name(location.country),
            'region': location.region or 'Not set',
            'enrolled': counts.get('enrolled', 0),
            'completed': counts.get('completed', 0),
            'withdrawn': counts.get('withdrawn', 0),
            'online': counts.get('online', 0),
            'cohorts_running': cohort_counts.get('running', 0),
            'cohorts_total': cohort_counts.get('total', 0),
            'sessions_held': sessions.get(location.pk, 0),
            'attendance_rate': attendance.get(location.pk),
        })
    return rows


def summarise(rows):
    """Totals by country and by country and region, from location rows."""
    measures = ('enrolled', 'completed', 'withdrawn', 'online', 'cohorts_running', 'sessions_held')
    countries, regions = {}, {}
    for row in rows:
        country = countries.setdefault(row['country'], {
            'country': row['country'], 'country_name': row['country_name'], 'locations': 0,
            **{measure: 0 for measure in measures},
        })
        region = regions.setdefault((row['country'], row['region']), {
            'country': row['country'], 'country_name': row['country_name'], 'region': row['region'],
            'locations': 0, **{measure: 0 for measure in measures},
        })
        for bucket in (country, region):
            bucket['locations'] += 1
            for measure in measures:
                bucket[measure] += row[measure]
    totals = {measure: sum(row[measure] for row in rows) for measure in measures}
    totals['locations'] = len(rows)
    totals['countries'] = len(countries)
    totals['partners'] = PartnerOrganisation.objects.filter(is_archived=False).count()
    by_country = sorted(countries.values(), key=lambda item: (-item['enrolled'], item['country_name']))
    by_region = sorted(regions.values(), key=lambda item: (item['country_name'], -item['enrolled']))
    return {'totals': totals, 'by_country': by_country, 'by_region': by_region}


def network_overview(use_cache=True):
    """Country and region summaries, cached for 10 minutes; location rows are not cached."""
    if use_cache:
        cached = cache.get(CACHE_KEY)
        if cached is not None:
            return cached
    data = summarise(location_rows())
    if use_cache:
        cache.set(CACHE_KEY, data, CACHE_SECONDS)
    return data


def dashboard_snapshot(today=None, licence_window_days=90):
    """Network totals, pipeline counts and licences ending soon, for the administration dashboard."""
    today = today or timezone.localdate()
    open_partnerships = Partnership.objects.filter(organisation__is_archived=False)
    counts = dict(open_partnerships.values_list('stage').annotate(total=Count('id')))
    pipeline = [
        {'stage': stage, 'label': Stage(stage).label, 'count': counts.get(stage, 0)}
        for stage in FORWARD_STAGES
    ]
    licences_ending = (
        Licence.objects.filter(
            status=Licence.Status.ACTIVE,
            ends_on__gte=today,
            ends_on__lte=today + timedelta(days=licence_window_days),
        )
        .select_related('organisation')
        .order_by('ends_on')[:5]
    )
    return {
        'totals': network_overview()['totals'],
        'pipeline': pipeline,
        'paused': counts.get(Stage.PAUSED, 0),
        'overdue': open_partnerships.exclude(stage=Stage.LOST).filter(next_action_due__lt=today).count(),
        'licences_ending': [
            {'licence': licence, 'days_left': (licence.ends_on - today).days} for licence in licences_ending
        ],
        'licence_window_days': licence_window_days,
    }
