from django.conf import settings
from django.core.checks import Warning, register

# Values that mean "not configured yet".
_PLACEHOLDERS = {"", "email", "app password", "your-email@gmail.com", "your-app-password"}

_SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"


@register()
def email_settings_check(app_configs, **kwargs):
    """Warn when the SMTP backend is used without real credentials.

    Without this, the contact form silently fails (or raises) in production
    because EMAIL_HOST_USER / EMAIL_HOST_PASSWORD were left at placeholders.
    """
    issues = []

    if settings.EMAIL_BACKEND != _SMTP_BACKEND:
        return issues

    if settings.EMAIL_HOST_USER in _PLACEHOLDERS:
        issues.append(
            Warning(
                "EMAIL_HOST_USER is not configured; contact form emails will fail.",
                obj=settings,
                id="portfolio_app.W001",
            )
        )

    if settings.EMAIL_HOST_PASSWORD in _PLACEHOLDERS:
        issues.append(
            Warning(
                "EMAIL_HOST_PASSWORD is not configured; contact form emails will fail.",
                obj=settings,
                id="portfolio_app.W002",
            )
        )

    return issues
