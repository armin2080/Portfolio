from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .utils import latest_content_update


class StaticViewSitemap(Sitemap):
    priority = 0.8
    changefreq = "weekly"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Computed once per sitemap request instead of once per URL.
        self._lastmod = None

    def items(self):
        return ["index", "skills", "projects", "resume", "contact", "privacy"]

    def location(self, item):
        return reverse(item)

    def lastmod(self, item):
        if self._lastmod is None:
            self._lastmod = latest_content_update()
        return self._lastmod
