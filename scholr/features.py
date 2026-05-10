"""
Product feature flags for Scholr multi-deployment architecture.

Three deployment modes:
  LMS only:  SCHOLR_LMS_ENABLED=True,  SCHOLR_ACADEMICS_ENABLED=False
  SMS only:  SCHOLR_LMS_ENABLED=False, SCHOLR_ACADEMICS_ENABLED=True
  Full:      SCHOLR_LMS_ENABLED=True,  SCHOLR_ACADEMICS_ENABLED=True

I check settings at call time so test overrides work without cache issues.
"""

from django.conf import settings


def lms_enabled() -> bool:
    """True when the shop/integration/LMS stack is installed and active."""
    return getattr(settings, 'SCHOLR_LMS_ENABLED', True)


def academics_enabled() -> bool:
    """True when the academics (SMS) module is installed and active."""
    return getattr(settings, 'SCHOLR_ACADEMICS_ENABLED', True)


def bridge_enabled() -> bool:
    """True when both stacks are active and school learners can be enrolled online."""
    return lms_enabled() and academics_enabled()


def get_feature_context() -> dict:
    """Context dict for templates. Passed via context processor."""
    return {
        'SCHOLR_LMS_ENABLED': lms_enabled(),
        'SCHOLR_ACADEMICS_ENABLED': academics_enabled(),
        'SCHOLR_BRIDGE_ENABLED': bridge_enabled(),
    }