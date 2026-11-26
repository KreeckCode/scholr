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
        'SCHOLR_ONLINE_RESULTS_ENABLED': settings.SCHOLR_PARTNERS_ENABLED and settings.SCHOLR_ONLINE_RESULTS_ENABLED,
        'SCHOLR_EMAIL_SIGN_IN_ENABLED': settings.SCHOLR_EMAIL_SIGN_IN_ENABLED,
        'SCHOLR_MULTILINGUAL_ENABLED': settings.SCHOLR_MULTILINGUAL_ENABLED,
        # Leaders see the network but change nothing; templates hide every edit control for them
        'NETWORK_READ_ONLY': getattr(getattr(request, 'user', None), 'role', None) == 'leader',
        **_network_permissions(getattr(request, 'user', None)),
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


def _network_permissions(user):
    """What the partner network pages may offer this person, for showing and hiding controls."""
    if not (settings.SCHOLR_PARTNERS_ENABLED and user is not None and getattr(user, 'is_authenticated', False)):
        return {}
    from partners.services import scope
    staff = scope.is_programme_staff(user)
    role = scope.network_role(user)
    return {
        'NETWORK_CAN_EDIT': scope.can_edit_network(user),
        'NETWORK_CAN_LOG': scope.can_log_network(user),
        'NETWORK_CAN_ADD_PARTNER': scope.can_add_partner(user),
        'NETWORK_CAN_MANAGE_LICENCES': scope.can_manage_licences(user),
        'NETWORK_CAN_ENTER_PORTAL': scope.can_enter_portal(user),
        'NETWORK_CAN_EXPORT': staff,
        'NETWORK_SEES_MAP': staff or role is not None,
        'NETWORK_SEES_RESULTS': staff or role in ('leader', 'hub_admin'),
    }
