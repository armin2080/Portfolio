import ipaddress

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


# Cloudflare's published edge ranges (https://www.cloudflare.com/ips/). Only a
# request whose *direct peer* is one of these addresses can be coming through
# Cloudflare, so only then may its CF-Connecting-IP be believed. A direct
# client can send any header it likes, but it cannot forge the TCP peer address,
# so this closes the spoofing hole without requiring the origin to be firewalled.
# Refresh the list when Cloudflare publishes a new range; an unknown edge only
# means its visitors fall back to REMOTE_ADDR, which shares a rate-limit bucket
# rather than opening one per request.
CLOUDFLARE_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        '173.245.48.0/20',
        '103.21.244.0/22',
        '103.22.200.0/22',
        '103.31.4.0/22',
        '141.101.64.0/18',
        '108.162.192.0/18',
        '190.93.240.0/20',
        '188.114.96.0/20',
        '197.234.240.0/22',
        '198.41.128.0/17',
        '162.158.0.0/15',
        '104.16.0.0/13',
        '104.24.0.0/14',
        '172.64.0.0/13',
        '131.0.72.0/22',
        '2400:cb00::/32',
        '2606:4700::/32',
        '2803:f800::/32',
        '2405:b500::/32',
        '2405:8100::/32',
        '2a06:98c0::/29',
        '2c0f:f248::/32',
    )
)


def is_trusted_proxy(request):
    """Whether this request's proxy headers may be believed.

    ``TRUST_PROXY_HEADERS`` answers "is there a proxy in front of the app?".
    This function answers the stricter question "did *this* request come from
    that proxy?", by checking the direct peer address against Cloudflare's
    ranges (and loopback, for a local reverse proxy or a Cloudflare Tunnel).

    Without that second check, anyone who can reach the origin directly can set
    ``CF-Connecting-IP`` themselves and get a fresh rate-limit bucket per
    request, appear as any country, or attribute a contact message to an IP
    they choose.
    """
    if not getattr(settings, 'TRUST_PROXY_HEADERS', True):
        return False

    remote = (request.META.get('REMOTE_ADDR') or '').strip()
    try:
        address = ipaddress.ip_address(remote)
    except ValueError:
        return False

    # ``::ffff:172.71.0.1`` is an IPv4 address wearing an IPv6 hat.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped

    if address.is_loopback:
        return True

    return any(address in network for network in CLOUDFLARE_NETWORKS)


def client_ip(request):
    """Return the real client IP, accounting for the proxy in front of the app.

    Behind Cloudflare ``REMOTE_ADDR`` is Cloudflare's edge address, so using it
    directly would put every visitor into the same rate-limit bucket. Cloudflare
    sets ``CF-Connecting-IP`` to the originating client address; ``X-Forwarded-For``
    is the fallback for other proxies.

    Proxy headers are only read when :func:`is_trusted_proxy` confirms the direct
    peer, so a client that reaches the origin without going through Cloudflare
    cannot spoof its address.

    Configured as ``RATELIMIT_IP_META_KEY``, so it must always return a non-empty
    string that parses as an IP address — django-ratelimit turns the value into a
    network object and would raise otherwise.
    """
    candidates = []

    if is_trusted_proxy(request):
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
