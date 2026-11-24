from django.conf import settings


def features(request):
    """Feature flags and the active brand, available in every template."""
    return {
        'SCHOLR_LMS_ENABLED':        settings.SCHOLR_LMS_ENABLED,
        'SCHOLR_ACADEMICS_ENABLED':  settings.SCHOLR_ACADEMICS_ENABLED,
        'SCHOLR_ENROLLMENT_ENABLED': settings.SCHOLR_ENROLLMENT_ENABLED,
        'SCHOLR_BURSARY_ENABLED':    settings.SCHOLR_BURSARY_ENABLED,
        'SCHOLR_BRIDGE_ENABLED':     settings.SCHOLR_BRIDGE_ENABLED,
        'site_profile':              settings.SITE_PROFILE,
        'brand':                     settings.SITE_BRAND,
        'SCHOLR_SELF_REGISTRATION': settings.SCHOLR_SELF_REGISTRATION,
        'SCHOLR_PARTNERS_ENABLED':   settings.SCHOLR_PARTNERS_ENABLED,
        # Templates only need one check: buying needs the LMS and purchases both on
        'SCHOLR_PURCHASES_ENABLED':  settings.SCHOLR_LMS_ENABLED and settings.SCHOLR_PURCHASES_ENABLED,
    }


def cart(request):
    """
    Cart item count, rendered with the page.
    Replaces the old cart_counter.html, which fetched and parsed the whole
    cart page on every single page load.
    """
    if not (settings.SCHOLR_LMS_ENABLED and settings.SCHOLR_PURCHASES_ENABLED):
        return {'cart_count': 0}

    count = request.session.get('cart', {})
    return {'cart_count': len(count) if isinstance(count, dict) else 0}