import shutil
import tempfile

from django.core.cache import caches
from django.test import TestCase, override_settings

PRIVATE_ROOT = tempfile.mkdtemp(prefix='partners-private-')

LOCAL_CACHES = {
    alias: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'partners-tests-{alias}'}
    for alias in ('default', 'sessions', 'moodle')
}


@override_settings(PARTNERS_PRIVATE_ROOT=PRIVATE_ROOT, SCHOLR_PARTNERS_ENABLED=True, CACHES=LOCAL_CACHES)
class PartnersTestCase(TestCase):
    """Keeps uploads out of the project, uses in-memory caches, and turns the feature on."""

    def setUp(self):
        super().setUp()
        for alias in LOCAL_CACHES:
            caches[alias].clear()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(PRIVATE_ROOT, ignore_errors=True)
