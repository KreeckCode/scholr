from django.apps import AppConfig


class AcademicsConfig(AppConfig):
    """
    School Management System app.
    Designed to run standalone or alongside the LMS apps.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'academics'
    verbose_name = 'School Management'