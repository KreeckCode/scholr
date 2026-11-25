from django.apps import AppConfig


class PartnersConfig(AppConfig):
    """Partner network: pipeline, licences, training locations and offline registers."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'partners'
    verbose_name = 'Partner network'

    def ready(self):
        import partners.signals  # noqa: F401
