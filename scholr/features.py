"""
Feature flags for Scholr's optional modules.

Each function reads from Django settings (which reads from .env).
Import these functions wherever feature-gating is needed — views,
templates (via context processor), tasks, and middleware.
"""
from django.conf import settings


def lms_enabled() -> bool:
    """Commercial LMS: course catalogue, shopping cart, Paystack, Moodle sync."""
    return getattr(settings, 'SCHOLR_LMS_ENABLED', True)


def academics_enabled() -> bool:
    """School Management System: learners, marks, attendance, reports."""
    return getattr(settings, 'SCHOLR_ACADEMICS_ENABLED', True)


def bridge_enabled() -> bool:
    """LMS Bridge: connects school learners to Moodle online courses."""
    return lms_enabled() and academics_enabled()


def enrollment_enabled() -> bool:
    """Enrollment module: application intake, grade promotion, re-enrollment."""
    return getattr(settings, 'SCHOLR_ENROLLMENT_ENABLED', False)


def bursary_enabled() -> bool:
    """
    Bursary/financial aid sub-feature within enrollment.
    Requires both SCHOLR_ENROLLMENT_ENABLED and SCHOLR_BURSARY_ENABLED to be True.
    """
    return (
        getattr(settings, 'SCHOLR_ENROLLMENT_ENABLED', False)
        and getattr(settings, 'SCHOLR_BURSARY_ENABLED', False)
    )