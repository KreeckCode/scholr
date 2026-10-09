"""
Domain errors raised by partners services.

Views catch PartnersError and show its message; nothing here is an HTTP concern.
"""


class PartnersError(Exception):
    """Base class: a business rule refused the action. The message is safe to show users."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class InvalidTransition(PartnersError):
    """A pipeline or status move that the rules do not allow."""


class GateNotMet(PartnersError):
    """A pipeline gate's required records do not exist yet."""


class LicenceStateError(PartnersError):
    """The licence status does not permit this action."""


class RosterError(PartnersError):
    """Cohort, participant, session or result rule violation."""


class AccessError(PartnersError):
    """Online access could not be granted or withdrawn."""


class PlaceError(PartnersError):
    """A map position, trip or location event could not be accepted."""


class SyncConflict(PartnersError):
    """An offline change was based on data someone else has since changed."""

    def __init__(self, message, server_state=None):
        super().__init__(message)
        self.server_state = server_state or {}
