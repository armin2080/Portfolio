"""Middleware for anonymous page views, proxy trust and the CSP header."""

import logging
import secrets

from django.conf import settings

from .analytics import (
    country_from_request,
    is_bot,
    parse_user_agent,
    referrer_domain,
    visitor_hash,
)
from .utils import client_ip, is_trusted_proxy

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
    # Monitoring probes are not visitors.
    '/healthz/',
    # A generated PDF is a download, not a page. Counting it would inflate the
    # dashboard's page views and put a non-page at the top of "top pages".
    '/resume/download/',
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


class ProxyHeaderMiddleware:
    """Honour ``X-Forwarded-Proto`` only from a proxy we can identify.

    Django's ``SECURE_PROXY_SSL_HEADER`` trusts that header unconditionally, so
    a client reaching the origin directly can send ``X-Forwarded-Proto: https``
    and make ``request.is_secure()`` lie — defeating ``SECURE_SSL_REDIRECT`` and
    changing cookie/CSRF behaviour. Doing the check here means the header is
    ignored unless the direct peer is Cloudflare (or a local reverse proxy).
    Must run before ``SecurityMiddleware``.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        forwarded = request.META.get('HTTP_X_FORWARDED_PROTO', '')
        if forwarded.lower() == 'https' and is_trusted_proxy(request):
            request.META['wsgi.url_scheme'] = 'https'
        return self.get_response(request)


# reCAPTCHA v3 loads its API and a hidden challenge frame from these hosts.
_RECAPTCHA_SCRIPT_SRC = 'https://www.google.com https://www.gstatic.com'
_RECAPTCHA_CONNECT_SRC = 'https://www.google.com'
_RECAPTCHA_FRAME_SRC = 'https://www.google.com https://recaptcha.google.com'
_RECAPTCHA_IMG_SRC = 'https://www.google.com https://www.gstatic.com'


def _csp(script_src):
    """The shared policy for public pages, with a page-specific script source."""
    # The active Theme is emitted as an inline <style> and a few templates use
    # style attributes, so inline CSS has to stay allowed. That is a much smaller
    # risk than inline script and needs no nonce plumbing.
    return (
        "default-src 'self'; "
        f"script-src {script_src}; "
        "style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' data: {_RECAPTCHA_IMG_SRC}; "
        "font-src 'self' data:; "
        f"connect-src 'self' {_RECAPTCHA_CONNECT_SRC}; "
        f"frame-src 'self' {_RECAPTCHA_FRAME_SRC}; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )


class ContentSecurityPolicyMiddleware:
    """Send a Content-Security-Policy header, with a nonce for inline scripts.

    Django escapes every value by default and this codebase uses no ``|safe``,
    so XSS is already unlikely; the policy is the second line of defence, and
    the nonce means an injected ``<script>`` cannot run even if an escaping bug
    appears.

    Two pages cannot use a nonce: the admin (its templates ship inline scripts
    and handlers) and the contact page (django-recaptcha injects its own inline
    bootstrap). Those get ``'unsafe-inline'`` instead, while still restricting
    which external origins may be loaded and forbidding framing, ``<base>``
    rewriting and plugin content.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Set before the view renders so templates can put it on their <script>.
        nonce = secrets.token_urlsafe(16)
        request.csp_nonce = nonce

        response = self.get_response(request)
        if response.has_header('Content-Security-Policy'):
            return response

        if request.path.startswith('/admin/'):
            script_src = "'self' 'unsafe-inline'"
        elif request.path.startswith('/contact/'):
            # The captcha's inline bootstrap has no nonce to attach. Keeping a
            # nonce in the policy would make browsers ignore 'unsafe-inline',
            # so this page deliberately uses one or the other.
            script_src = f"'self' 'unsafe-inline' {_RECAPTCHA_SCRIPT_SRC}"
        else:
            script_src = f"'self' 'nonce-{nonce}' {_RECAPTCHA_SCRIPT_SRC}"

        response['Content-Security-Policy'] = _csp(script_src)
        return response
