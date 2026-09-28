from django.apps import AppConfig


class PortfolioAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'portfolio_app'

    def ready(self):
        # Register custom system checks (email configuration warnings).
        from . import checks  # noqa: F401
