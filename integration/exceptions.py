"""
Custom exceptions for Moodle integration
Provides clear error handling and messaging
"""


class MoodleIntegrationError(Exception):
    """Base exception for all Moodle integration errors"""
    pass


class MoodleConnectionError(MoodleIntegrationError):
    """
    Raised when cannot connect to Moodle server
    This is the main error for offline/unreachable scenarios
    """
    def __init__(self, message="Cannot connect to Moodle server"):
        self.message = message
        super().__init__(self.message)


class MoodleAPIError(MoodleIntegrationError):
    """
    Raised when Moodle API returns an error
    Used for API-level errors like missing parameters, invalid data, etc.
    """
    def __init__(self, message="Moodle API error"):
        self.message = message
        super().__init__(self.message)


class MoodleAuthenticationError(MoodleIntegrationError):
    """
    Raised when authentication fails (invalid token, etc.)
    """
    def __init__(self, message="Moodle authentication failed"):
        self.message = message
        super().__init__(self.message)


class MoodleSyncError(MoodleIntegrationError):
    """
    Raised when sync operation fails
    Used for business logic errors during sync
    """
    def __init__(self, message="Sync operation failed", details=None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class UserNotSyncedError(MoodleIntegrationError):
    """
    Raised when trying to perform operation on user not synced to Moodle
    """
    def __init__(self, username):
        self.username = username
        self.message = f"User '{username}' is not synced to Moodle"
        super().__init__(self.message)


class CourseNotSyncedError(MoodleIntegrationError):
    """
    Raised when trying to perform operation on course not synced to Moodle
    """
    def __init__(self, course_name):
        self.course_name = course_name
        self.message = f"Course '{course_name}' is not synced to Moodle"
        super().__init__(self.message)


class EnrollmentError(MoodleIntegrationError):
    """
    Raised when enrollment operation fails
    """
    def __init__(self, message="Enrollment operation failed"):
        self.message = message
        super().__init__(self.message)


class DuplicateRecordError(MoodleIntegrationError):
    """
    Raised when trying to create a duplicate record in Moodle
    """
    def __init__(self, record_type, identifier):
        self.record_type = record_type
        self.identifier = identifier
        self.message = f"Duplicate {record_type}: {identifier} already exists in Moodle"
        super().__init__(self.message)


class ValidationError(MoodleIntegrationError):
    """
    Raised when data validation fails before sync
    """
    def __init__(self, field, message):
        self.field = field
        self.message = f"Validation error on '{field}': {message}"
        super().__init__(self.message)