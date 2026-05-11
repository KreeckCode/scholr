from django.http import Http404
from django.conf import settings


class FeatureFlagMiddleware:
    """
    Blocks URL prefixes for disabled product modules.
    Only active when the corresponding flag is False.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        if not getattr(settings, 'SCHOLR_ACADEMICS_ENABLED', True):
            if path.startswith('/academics/'):
                raise Http404

        if not getattr(settings, 'SCHOLR_LMS_ENABLED', True):
            if path.startswith('/shop/') or path.startswith('/my-courses/'):
                raise Http404

        return self.get_response(request)