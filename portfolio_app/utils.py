from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_ipv46_address
from django.db.models import Max

from .models import Certificate, Education, Profile, Project, Skill, WorkExperience

# Every content model that carries an ``updated_at`` timestamp.
CONTENT_MODELS = (Skill, Project, Education, WorkExperience, Certificate, Profile)


def latest_content_update():
    """Return the most recent ``updated_at`` across all portfolio content.

    Used for the footer's "last updated" label and for the sitemap ``lastmod``.
    Costs one aggregate query per model, so callers should cache the result
    where it runs on every request.
    """
    latest = None
    for model in CONTENT_MODELS:
        last = model.objects.aggregate(Max("updated_at"))["updated_at__max"]
        if last and (latest is None or last > latest):
            latest = last
    return latest


def client_ip(request):
    """Return the real client IP, accounting for the proxy in front of the app.

    Behind Cloudflare ``REMOTE_ADDR`` is Cloudflare's edge address, so using it
    directly would put every visitor into the same rate-limit bucket. Cloudflare
    sets ``CF-Connecting-IP`` to the originating client address; ``X-Forwarded-For``
    is the fallback for other proxies.

    Configured as ``RATELIMIT_IP_META_KEY``, so it must always return a non-empty
    string that parses as an IP address — django-ratelimit turns the value into a
    network object and would raise otherwise.
    """
    candidates = []

    if getattr(settings, 'TRUST_PROXY_HEADERS', True):
        candidates.append(request.META.get('HTTP_CF_CONNECTING_IP'))
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            candidates.append(forwarded.split(',')[0])

    candidates.append(request.META.get('REMOTE_ADDR', ''))

    for candidate in candidates:
        ip = (candidate or '').strip()
        if not ip:
            continue
        try:
            validate_ipv46_address(ip)
        except ValidationError:
            # Ignore malformed/spoofed header values and try the next source.
            continue
        return ip

    # No usable address: bucket everything together instead of raising a 500.
    return '0.0.0.0'
