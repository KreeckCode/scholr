"""
Injects feature flags into every template context.
Registered in settings.TEMPLATES.OPTIONS.context_processors.
"""
from scholr.features import (
    lms_enabled,
    academics_enabled,
    bridge_enabled,
    enrollment_enabled,
    bursary_enabled,
)


def features(request):
    """
    Makes all feature flags available as template variables.
    Use: {% if SCHOLR_ENROLLMENT_ENABLED %}...{% endif %}
    """
    return {
        'SCHOLR_LMS_ENABLED':        lms_enabled(),
        'SCHOLR_ACADEMICS_ENABLED':  academics_enabled(),
        'SCHOLR_BRIDGE_ENABLED':     bridge_enabled(),
        'SCHOLR_ENROLLMENT_ENABLED': enrollment_enabled(),
        'SCHOLR_BURSARY_ENABLED':    bursary_enabled(),
    }