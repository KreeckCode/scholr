"""
JSON endpoints for the offline register app.

Authentication is the normal Django session; CSRF is enforced as for any POST, with the
token sent in the X-CSRFToken header. Responses are never cached.
"""

import json
import logging

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from partners.exceptions import PartnersError
from partners.services import scope, sync
from partners.utils import rate_limited

logger = logging.getLogger(__name__)

PACK_LIMIT_PER_MINUTE = 30
SYNC_LIMIT_PER_MINUTE = 60


def error(status, code, message):
    return JsonResponse({'error': code, 'message': message}, status=status)


@method_decorator(never_cache, name='dispatch')
class ApiView(View):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return error(401, 'not_authenticated', 'Sign in again to continue.')
        return super().dispatch(request, *args, **kwargs)


class MeView(ApiView):
    """Who is signed in, so the app can tell whether local data belongs to this person."""

    def get(self, request):
        return JsonResponse({
            'id': request.user.pk,
            'name': request.user.get_full_name(),
            'server_time': timezone.now().isoformat(),
        })


class CohortPackView(ApiView):
    def get(self, request, cohort_id):
        if rate_limited(f'pack:{request.user.pk}', PACK_LIMIT_PER_MINUTE, 60):
            return error(429, 'rate_limited', 'Too many downloads. Wait a minute and try again.')
        cohort = get_object_or_404(
            scope.cohorts_for(request.user).select_related('location__licence', 'location__organisation'),
            public_id=cohort_id,
        )
        if request.session.get_expire_at_browser_close():
            # I'm requiring remember-me because a browser-length session would strand queued changes
            return error(
                409, 'remember_me_required',
                'Sign out, then sign in again with "Keep me signed in" ticked before preparing for offline use.',
            )
        return JsonResponse(sync.build_pack(cohort, request.user))


class SyncView(ApiView):
    http_method_names = ['post']

    def post(self, request):
        if rate_limited(f'sync:{request.user.pk}', SYNC_LIMIT_PER_MINUTE, 60):
            return error(429, 'rate_limited', 'Too many requests. Changes will be sent again shortly.')
        if not (request.content_type or '').startswith('application/json'):
            return error(415, 'unsupported_media_type', 'Send JSON.')
        try:
            body = json.loads(request.body or b'{}')
        except (ValueError, UnicodeDecodeError):
            return error(400, 'invalid_json', 'The request body is not valid JSON.')
        if not isinstance(body, dict):
            return error(400, 'invalid_body', 'The request body must be an object.')
        try:
            results = sync.apply_batch(request.user, body.get('device_id', ''), body.get('operations'))
        except PartnersError as exc:
            return error(400, 'invalid_batch', exc.message)
        return JsonResponse({'results': results, 'server_time': timezone.now().isoformat()})
