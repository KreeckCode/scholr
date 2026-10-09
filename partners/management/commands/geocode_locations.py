"""
Put training locations that have no map position at the middle of their town, using
OpenStreetMap's place search. A position found this way is marked as estimated, so the map
asks someone to place the pin exactly.

    python manage.py geocode_locations             # dry run: shows what would be found
    python manage.py geocode_locations --apply
    python manage.py geocode_locations --apply --limit 20

OpenStreetMap allows one search a second, so the command waits between searches.
Locations that already have a position are never changed.
"""

import time
from decimal import Decimal

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from partners.models import TrainingLocation
from partners.services.places import PLACES


class Command(BaseCommand):
    help = 'Estimate map positions for training locations from their town, region and country.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true', help='Save the positions found. Without it, nothing is saved.',
        )
        parser.add_argument('--limit', type=int, default=0, help='Stop after this many locations.')
        parser.add_argument('--pause', type=float, default=1.1, help='Seconds between searches. Default 1.1.')

    def handle(self, *args, **options):
        locations = TrainingLocation.objects.filter(latitude__isnull=True).order_by('country', 'name')
        if options['limit']:
            locations = locations[:options['limit']]
        locations = list(locations)
        self.stdout.write(f'{len(locations)} location(s) without a map position.')

        session = requests.Session()
        session.headers.update({'User-Agent': settings.PARTNERS_MAP_USER_AGENT, 'Accept': 'application/json'})
        found = 0
        for position, location in enumerate(locations):
            if position:
                time.sleep(options['pause'])
            label = 'a sensitive location' if location.hides_identities else location.name
            query = ', '.join(part for part in (location.city, location.region, location.country_name) if part)
            if not query:
                self.stdout.write(f'  skipped {label}: no town, region or country')
                continue
            try:
                response = session.get(settings.PARTNERS_MAP_SEARCH_URL, params={
                    'q': query, 'format': 'jsonv2', 'limit': 1, 'countrycodes': (location.country or '').lower(),
                }, timeout=15)
                response.raise_for_status()
                results = response.json()
            except (requests.RequestException, ValueError) as exc:
                self.stdout.write(self.style.WARNING(f'  search failed for {label}: {exc}'))
                continue
            if not results:
                self.stdout.write(f'  nothing found for {label} ({query})')
                continue
            lat = Decimal(str(results[0]['lat'])).quantize(PLACES)
            lng = Decimal(str(results[0]['lon'])).quantize(PLACES)
            found += 1
            self.stdout.write(f'  {label}: {lat}, {lng} from "{query}"')
            if options['apply']:
                location.latitude, location.longitude = lat, lng
                location.position_source = TrainingLocation.PositionSource.GEOCODED
                location.position_confirmed_at = None
                location.position_confirmed_by = None
                location.save(update_fields=[
                    'latitude', 'longitude', 'position_source', 'position_confirmed_at',
                    'position_confirmed_by', 'updated_at',
                ])

        if options['apply']:
            self.stdout.write(self.style.SUCCESS(
                f'{found} estimated position(s) saved. Ask coordinators to place each pin exactly.'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f'Dry run: {found} position(s) found, nothing saved. Add --apply to save them.'
            ))
