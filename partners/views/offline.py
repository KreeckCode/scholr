"""
The offline register app shell, its service worker and its web app manifest.

The service worker is served from /partners/sw.js rather than /static/ so its scope
covers /partners/; a worker can only control paths at or below its own URL.
"""

import json

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.templatetags.static import static
from django.urls import reverse
from django.views.generic import TemplateView

OFFLINE_CACHE_VERSION = 'metp-offline-v1'


def offline_assets():
    return [
        static('partners/offline.css'),
        static('partners/offline-core.js'),
        static('partners/offline-app.js'),
        static('partners/icon.svg'),
    ]


class OfflineAppView(LoginRequiredMixin, TemplateView):
    template_name = 'partners/offline/app.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'brand': settings.SITE_BRAND,
            'config': {
                'appUrl': reverse('partners:offline_app'),
                'swUrl': reverse('partners:service_worker'),
                'meUrl': reverse('partners:api_me'),
                'syncUrl': reverse('partners:api_sync'),
                'packUrlTemplate': reverse(
                    'partners:api_pack', kwargs={'cohort_id': '00000000-0000-0000-0000-000000000000'},
                ),
                'loginUrl': reverse('accounts:login'),
                'portalUrl': reverse('partners:portal_home'),
            },
        })
        return context


class ServiceWorkerView(TemplateView):
    template_name = 'partners/offline/sw.js'
    content_type = 'application/javascript'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # I'm serialising app-generated URLs as JSON so the script never needs escaping
        context['worker_config'] = json.dumps({
            'cache': OFFLINE_CACHE_VERSION,
            'appUrl': reverse('partners:offline_app'),
            'syncUrl': reverse('partners:api_sync'),
            'assets': offline_assets() + [reverse('partners:manifest')],
        })
        context['core_url'] = json.dumps(static('partners/offline-core.js'))
        return context

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        response['Cache-Control'] = 'no-cache'
        return response


class ManifestView(TemplateView):
    template_name = 'partners/offline/manifest.webmanifest'
    content_type = 'application/manifest+json'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'brand': settings.SITE_BRAND,
            'app_url': reverse('partners:offline_app'),
            'scope_url': reverse('partners:offline_app'),
            'icon_url': static('partners/icon.svg'),
        })
        return context
