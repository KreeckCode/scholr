from scholr.features import get_feature_context


def features(request):
    """
    Injects SCHOLR_LMS_ENABLED, SCHOLR_ACADEMICS_ENABLED, SCHOLR_BRIDGE_ENABLED
    into every template context. Used to conditionally show/hide nav items.
    """
    return get_feature_context()