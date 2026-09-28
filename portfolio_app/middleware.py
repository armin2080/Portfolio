"""Middleware that records anonymous page views for the private dashboard."""

import logging

from django.conf import settings

from .analytics import (
    country_from_request,
    is_bot,
    parse_user_agent,
    referrer_domain,
    visitor_hash,
)
from .utils import client_ip

logger = logging.getLogger(__name__)

# Paths that must never be counted. Override with ANALYTICS_EXCLUDED_PREFIXES.
DEFAULT_EXCLUDED_PREFIXES = (
    '/admin/',
    '/static/',
    '/media/',
    '/dashboard/',
)

# Individual files that are not page views.
DEFAULT_EXCLUDED_EXACT = (
    '/favicon.ico',
    '/robots.txt',
    '/sitemap.xml',
)


class PageViewMiddleware:
    """Store a coarse record of each anonymous page view.

    Deliberately conservative:

    * Only GET requests answered with a 2xx status are recorded.
    * Signed-in staff (you) are skipped, so browsing the site while logged in
      does not distort the numbers.
    * The raw IP is passed through :func:`visitor_hash` and never persisted.
    * Failures are logged and swallowed — stats must never break the site.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.excluded_prefixes = tuple(
            getattr(settings, 'ANALYTICS_EXCLUDED_PREFIXES', DEFAULT_EXCLUDED_PREFIXES)
        )
        self.excluded_exact = tuple(
            getattr(settings, 'ANALYTICS_EXCLUDED_EXACT', DEFAULT_EXCLUDED_EXACT)
        )

    def __call__(self, request):
        response = self.get_response(request)

        try:
            self._record(request, response)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Could not record page view for %s", request.path)

        return response

    def _should_skip_for_staff(self, request):
        # Avoid the extra session/user lookup for anonymous visitors: only look
        # when a session cookie is actually present.
        if settings.SESSION_COOKIE_NAME not in request.COOKIES:
            return False
        user = getattr(request, 'user', None)
        return bool(user and user.is_authenticated and user.is_staff)

    def _record(self, request, response):
        if not getattr(settings, 'ANALYTICS_ENABLED', False):
            return

        if request.method != 'GET':
            return
        if response.status_code >= 400:
            return

        path = request.path
        if path in self.excluded_exact:
            return
        if path.startswith(self.excluded_prefixes):
            return
        if self._should_skip_for_staff(request):
            return

        # Imported here so the app registry is always ready.
        from .models import PageView

        user_agent = request.META.get('HTTP_USER_AGENT', '')
        device, browser, operating_system = parse_user_agent(user_agent)

        PageView.objects.create(
            path=path[:255],
            country=country_from_request(request),
            device_type=device,
            browser=browser,
            os=operating_system,
            referrer=referrer_domain(request),
            visitor_hash=visitor_hash(client_ip(request)),
            is_bot=is_bot(user_agent),
        )
