"""
Business rules for the partner network.

Views stay thin and call these functions. Every function that changes data takes the
acting user, and date-dependent rules take an optional `today` so tests never need to
freeze the clock.

Nothing in this package may import integration.moodle_client or integration.services.
Online access reaches Moodle only through the existing shop order pipeline (see access.py).
"""
