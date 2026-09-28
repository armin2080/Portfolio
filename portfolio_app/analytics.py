"""Privacy-conscious request classification for the built-in visitor stats.

Design constraints (see README — "Visitor statistics"):

* No cookies, no local storage and no device identifiers are involved.
* IP addresses are never written to the database. They are only used, together
  with a salt that is re-derived every day, to produce a rotating hash. That
  makes it possible to count unique visitors per day without being able to
  follow one person across days.
* Only coarse, non-identifying attributes are stored: country (from Cloudflare),
  device class, browser and operating system family, and the referring domain.
"""

import hashlib
import re
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

# Requests from these are not people. Kept deliberately broad: a link-preview
# fetcher or monitoring probe would otherwise inflate the numbers.
_BOT_PATTERN = re.compile(
    r'bot\b|bots?\b|crawler|spider|slurp|crawl|'
    r'curl/|wget|python-requests|python-urllib|httpx|aiohttp|go-http-client|'
    r'java/|okhttp|axios|node-fetch|libwww|http-client|'
    r'headlesschrome|phantomjs|puppeteer|playwright|selenium|'
    r'facebookexternalhit|whatsapp|telegrambot|discordbot|slackbot|twitterbot|'
    r'linkedinbot|embedly|quora|pinterest|redditbot|applebot|'
    r'uptimerobot|pingdom|statuscake|site24x7|monitoring|healthcheck|'
    r'ahrefs|semrush|mj12bot|dotbot|petalbot|megaindex|screaming frog',
    re.IGNORECASE,
)

# Order matters: Edge and Chrome both advertise "Chrome"/"Safari" in their UA.
_BROWSERS = (
    ('Edge', ('edg/', 'edgios', 'edga')),
    ('Opera', ('opr/', 'opera')),
    ('Samsung Internet', ('samsungbrowser',)),
    ('Firefox', ('firefox/', 'fxios', 'focus/')),
    ('Chrome', ('chrome/', 'crios')),
    ('Safari', ('safari/',)),
)

_OPERATING_SYSTEMS = (
    ('iOS', ('iphone', 'ipad', 'ipod')),
    ('Android', ('android',)),
    ('Windows', ('windows',)),
    ('macOS', ('mac os x', 'macintosh')),
    ('Linux', ('linux',)),
)

# Cloudflare uses these for "unknown" and for Tor exit nodes.
_UNKNOWN_COUNTRIES = {'', 'XX', 'T1'}


def is_bot(user_agent):
    """Return True when the User-Agent looks like a crawler or a script."""
    if not user_agent:
        return True  # No UA at all is almost always a script.
    return bool(_BOT_PATTERN.search(user_agent))


def parse_user_agent(user_agent):
    """Extract (device_type, browser, os) from a User-Agent string.

    Coarse on purpose — these are families, not versions, so the result is not
    a fingerprint that could single out one visitor.
    """
    ua = (user_agent or '').lower()

    if not ua:
        return 'unknown', 'unknown', 'unknown'

    if 'ipad' in ua or 'tablet' in ua or ('android' in ua and 'mobile' not in ua):
        device = 'tablet'
    elif 'mobi' in ua or 'iphone' in ua or 'ipod' in ua or 'android' in ua:
        device = 'mobile'
    else:
        device = 'desktop'

    browser = 'Other'
    for name, needles in _BROWSERS:
        if any(needle in ua for needle in needles):
            browser = name
            break

    operating_system = 'Other'
    for name, needles in _OPERATING_SYSTEMS:
        if any(needle in ua for needle in needles):
            operating_system = name
            break

    return device, browser, operating_system


def country_from_request(request):
    """ISO 3166-1 alpha-2 country from Cloudflare, or '' when unknown.

    Cloudflare adds ``CF-IPCountry`` on every request, so no GeoIP database is
    needed on the server. Returns '' for Cloudflare's unknown markers.
    """
    country = (request.META.get('HTTP_CF_IPCOUNTRY') or '').strip().upper()
    if country in _UNKNOWN_COUNTRIES:
        return ''
    if len(country) != 2 or not country.isalpha():
        return ''
    return country


def referrer_domain(request):
    """Referring domain, excluding self-referrals, or '' when direct."""
    raw = request.META.get('HTTP_REFERER', '')
    if not raw:
        return ''

    host = urlparse(raw).netloc.lower().split(':')[0]
    if host.startswith('www.'):
        host = host[4:]
    if not host:
        return ''

    # Internal navigation (clicking around the site) is not a referrer.
    # Read Host from META rather than request.get_host(), which raises
    # DisallowedHost for unexpected hosts and would abort the whole record.
    own_hosts = {urlparse(settings.SITE_URL).netloc.lower().removeprefix('www.')}
    request_host = request.META.get('HTTP_HOST', '').lower().split(':')[0]
    if request_host:
        own_hosts.add(request_host.removeprefix('www.'))
    if host in own_hosts:
        return ''

    return host[:100]


def visitor_hash(ip_address, day=None):
    """A pseudonym that is stable for one day and then changes.

    This is what makes "unique visitors today" possible without storing the IP
    or being able to recognise the same person tomorrow. The salt is part of
    ``ANALYTICS_SALT`` (a secret), so the hash cannot be reversed by guessing.
    """
    if not ip_address:
        return ''
    day = day or timezone.localdate()
    raw = f"{settings.ANALYTICS_SALT}:{day.isoformat()}:{ip_address}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
