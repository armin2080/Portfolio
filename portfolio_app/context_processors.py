from django.conf import settings
from django.core.cache import cache

from .models import Profile
from .utils import latest_content_update

# The "last updated" label needs six aggregate queries, so it is cached briefly.
# A few minutes of staleness on a footer date is not noticeable.
LAST_UPDATED_CACHE_KEY = "portfolio:last-updated"
LAST_UPDATED_CACHE_TIMEOUT = 300  # seconds


def profile_context(request):
    """Make global data available in all templates.

    ``global_profile`` is queried on every request so that admin edits (email,
    phone, social links) appear immediately. Only the aggregate-heavy
    ``last_updated`` value is cached.
    """
    last_updated = cache.get(LAST_UPDATED_CACHE_KEY)
    if last_updated is None:
        last_updated = latest_content_update()
        if last_updated is not None:
            cache.set(LAST_UPDATED_CACHE_KEY, last_updated, LAST_UPDATED_CACHE_TIMEOUT)

    return {
        'global_profile': Profile.objects.first(),
        'last_updated': last_updated,
        'site_url': settings.SITE_URL,
    }
