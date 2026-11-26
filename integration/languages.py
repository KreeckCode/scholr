"""Which Moodle language pack someone's account should use."""
from django.conf import settings

from scholr.features import multilingual_enabled


def moodle_language(user):
    """The Moodle language for this person: their chosen language where switched on, else English."""
    if not multilingual_enabled():
        return 'en'
    code = (getattr(user, 'preferred_language', '') or '').split('-')[0]
    return settings.MOODLE_LANGUAGES.get(code, 'en')
