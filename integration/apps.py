from django.apps import AppConfig


class IntegrationConfig(AppConfig):
    name = 'integration'
    
    def ready(self):
        """Import signals when app is ready"""
        import integration.signals  # Register integration signals