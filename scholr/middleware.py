from django.conf import settings
from django.http import Http404


class FeatureFlagMiddleware:
    """
    Blocks URL prefixes for disabled product modules.
    Only active when the corresponding flag is False.
    """

    # The shop is mounted at /courses/ in scholr/urls.py
    LMS_PREFIXES = ('/courses/',)
    ACADEMICS_PREFIXES = ('/academics/',)
    PARTNERS_PREFIXES = ('/partners/',)

    # Buying pages only. My Learning, bundles and the admin pages stay, because
    # partner-funded access is delivered through the online programme bundle.
    PURCHASE_EXACT = ('/courses/',)
    PURCHASE_PREFIXES = (
        '/courses/product/',
        '/courses/cart/',
        '/courses/checkout/',
        '/courses/payment/',
        '/courses/orders/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        if not getattr(settings, 'SCHOLR_ACADEMICS_ENABLED', True):
            if path.startswith(self.ACADEMICS_PREFIXES):
                raise Http404

        if not getattr(settings, 'SCHOLR_LMS_ENABLED', True):
            if path.startswith(self.LMS_PREFIXES):
                raise Http404

        if not getattr(settings, 'SCHOLR_PARTNERS_ENABLED', False):
            if path.startswith(self.PARTNERS_PREFIXES):
                raise Http404

        if not getattr(settings, 'SCHOLR_PURCHASES_ENABLED', True):
            if path in self.PURCHASE_EXACT or path.startswith(self.PURCHASE_PREFIXES):
                raise Http404

        return self.get_response(request)
