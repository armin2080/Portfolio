"""
URL configuration for Personal_Portfolio project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve
from django.contrib.sitemaps.views import sitemap
from django.http import HttpResponse
from portfolio_app.sitemaps import StaticViewSitemap

sitemaps = {
    "static": StaticViewSitemap,
}


def robots_txt(request):
    lines = [
        "User-agent: *",
        "Disallow: /admin/",
        "Disallow: /dashboard/",
        "Allow: /",
        f"Sitemap: {settings.SITE_URL}/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


# Media is served by this small single-service deployment. Add a browser cache
# lifetime so repeat visits do not re-download every uploaded image.
MEDIA_CACHE_SECONDS = 60 * 60 * 24  # one day


def serve_media(request, path):
    response = serve(request, path, document_root=settings.MEDIA_ROOT)
    if response.status_code == 200:
        response["Cache-Control"] = f"public, max-age={MEDIA_CACHE_SECONDS}"
    return response


urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('portfolio_app.urls')),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='sitemap'),
    path('robots.txt', robots_txt, name='robots_txt'),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

# This small, single-service deployment serves uploaded media through Django.
urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve_media),
]
