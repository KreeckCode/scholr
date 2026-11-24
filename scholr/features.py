"""Feature flags for Scholr's optional modules, read from Django settings."""
from django.conf import settings


def lms_enabled() -> bool:
    """Commercial LMS: course catalogue, cart, payments, Moodle sync."""
    return getattr(settings, 'SCHOLR_LMS_ENABLED', True)


def academics_enabled() -> bool:
    """School management: learners, marks, attendance, reports."""
    return getattr(settings, 'SCHOLR_ACADEMICS_ENABLED', True)


def bridge_enabled() -> bool:
    """LMS bridge: connects school learners to Moodle courses."""
    return (
        getattr(settings, 'SCHOLR_BRIDGE_ENABLED', False)
        and lms_enabled()
        and academics_enabled()
    )


def enrollment_enabled() -> bool:
    """Enrollment module: applications, promotion, re-enrolment."""
    return getattr(settings, 'SCHOLR_ENROLLMENT_ENABLED', False)


def bursary_enabled() -> bool:
    """Bursaries, a sub-feature of enrollment."""
    return enrollment_enabled() and getattr(settings, 'SCHOLR_BURSARY_ENABLED', False)


def partners_enabled() -> bool:
    """Partner network: pipeline, licences, training location portal, offline registers."""
    return getattr(settings, 'SCHOLR_PARTNERS_ENABLED', False)


def self_registration_enabled() -> bool:
    """Public sign-up. Off for school deployments, where staff create accounts."""
    return getattr(settings, 'SCHOLR_SELF_REGISTRATION', True)


def purchases_enabled() -> bool:
    """Public catalogue, cart and checkout. Off where access only comes through partners."""
    return getattr(settings, 'SCHOLR_LMS_ENABLED', True) and getattr(settings, 'SCHOLR_PURCHASES_ENABLED', True)


def cohort_groups_enabled() -> bool:
    """One Moodle group per partner cohort, so participants only see their own cohort."""
    return partners_enabled() and getattr(settings, 'SCHOLR_COHORT_GROUPS_ENABLED', False)


def staff_lms_portal_enabled() -> bool:
    """LMS Portal button that signs staff into Moodle as teachers."""
    return getattr(settings, 'SCHOLR_LMS_ENABLED', True) and getattr(settings, 'SCHOLR_STAFF_LMS_PORTAL_ENABLED', False)


def online_results_enabled() -> bool:
    """Online quiz results pulled from Moodle, and the results overviews."""
    return partners_enabled() and getattr(settings, 'SCHOLR_ONLINE_RESULTS_ENABLED', False)
