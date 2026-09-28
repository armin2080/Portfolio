from datetime import date, timedelta
from io import StringIO

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .analytics import (
    country_from_request,
    is_bot,
    parse_user_agent,
    referrer_domain,
    visitor_hash,
)
from .models import PageView

# ---------------------------------------------------------------------------
# Visitor statistics: analytics helpers
# ---------------------------------------------------------------------------
class UserAgentParsingTests(TestCase):
    UA_IPHONE = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    )
    UA_WINDOWS_CHROME = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    UA_IPAD = (
        "Mozilla/5.0 (iPad; CPU OS 16_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
    )
    UA_ANDROID_TABLET = "Mozilla/5.0 (Linux; Android 13; SM-X200) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    UA_EDGE = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
    )

    def test_mobile_device(self):
        device, browser, os_name = parse_user_agent(self.UA_IPHONE)
        self.assertEqual(device, "mobile")
        self.assertEqual(browser, "Safari")
        self.assertEqual(os_name, "iOS")

    def test_desktop_device(self):
        device, browser, os_name = parse_user_agent(self.UA_WINDOWS_CHROME)
        self.assertEqual(device, "desktop")
        self.assertEqual(browser, "Chrome")
        self.assertEqual(os_name, "Windows")

    def test_tablet_detected_from_ipad(self):
        self.assertEqual(parse_user_agent(self.UA_IPAD)[0], "tablet")

    def test_android_without_mobile_is_a_tablet(self):
        self.assertEqual(parse_user_agent(self.UA_ANDROID_TABLET)[0], "tablet")

    def test_edge_is_not_reported_as_chrome(self):
        # Edge advertises "Chrome" in its UA, so ordering matters.
        self.assertEqual(parse_user_agent(self.UA_EDGE)[1], "Edge")

    def test_unknown_user_agent(self):
        self.assertEqual(parse_user_agent(""), ("unknown", "unknown", "unknown"))


class BotDetectionTests(TestCase):
    def test_human_user_agents_are_not_bots(self):
        for ua in (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1",
        ):
            with self.subTest(ua=ua):
                self.assertFalse(is_bot(ua))

    def test_known_bots_and_scripts_are_detected(self):
        for ua in (
            "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
            "Mozilla/5.0 (compatible; bingbot/2.0)",
            "curl/8.4.0",
            "python-requests/2.31.0",
            "Mozilla/5.0 (compatible; AhrefsBot/7.0)",
            "WhatsApp/2.23",
        ):
            with self.subTest(ua=ua):
                self.assertTrue(is_bot(ua))

    def test_missing_user_agent_counts_as_bot(self):
        self.assertTrue(is_bot(""))


class CountryTests(TestCase):
    def _request(self, **meta):
        request = RequestFactory().get("/")
        request.META.update(meta)
        return request

    def test_reads_country_from_cloudflare_header(self):
        self.assertEqual(country_from_request(self._request(HTTP_CF_IPCOUNTRY="DE")), "DE")

    def test_normalises_lowercase(self):
        self.assertEqual(country_from_request(self._request(HTTP_CF_IPCOUNTRY="de")), "DE")

    def test_unknown_markers_become_blank(self):
        for value in ("XX", "T1", "", "  "):
            with self.subTest(value=value):
                self.assertEqual(country_from_request(self._request(HTTP_CF_IPCOUNTRY=value)), "")

    def test_rejects_malformed_values(self):
        for value in ("DEU", "D3", "12"):
            with self.subTest(value=value):
                self.assertEqual(country_from_request(self._request(HTTP_CF_IPCOUNTRY=value)), "")


class ReferrerTests(TestCase):
    def _request(self, referer=None, host="armin2080.de"):
        request = RequestFactory().get("/", HTTP_HOST=host)
        if referer:
            request.META["HTTP_REFERER"] = referer
        return request

    def test_extracts_referring_domain(self):
        request = self._request("https://www.google.com/search?q=armin")
        self.assertEqual(referrer_domain(request), "google.com")

    def test_no_referrer_is_blank(self):
        self.assertEqual(referrer_domain(self._request()), "")

    def test_internal_navigation_is_not_a_referrer(self):
        self.assertEqual(
            referrer_domain(self._request("https://armin2080.de/skills/")), ""
        )

    def test_www_variant_of_own_site_is_not_a_referrer(self):
        self.assertEqual(
            referrer_domain(self._request("https://www.armin2080.de/skills/")), ""
        )


class VisitorHashTests(TestCase):
    def test_is_stable_within_the_same_day(self):
        day = date(2026, 1, 1)
        self.assertEqual(
            visitor_hash("203.0.113.7", day=day),
            visitor_hash("203.0.113.7", day=day),
        )

    def test_rotates_between_days(self):
        # This is what prevents tracking a visitor across days.
        first = visitor_hash("203.0.113.7", day=date(2026, 1, 1))
        second = visitor_hash("203.0.113.7", day=date(2026, 1, 2))
        self.assertNotEqual(first, second)

    def test_different_visitors_get_different_hashes(self):
        day = date(2026, 1, 1)
        self.assertNotEqual(
            visitor_hash("203.0.113.7", day=day),
            visitor_hash("198.51.100.9", day=day),
        )

    def test_does_not_contain_the_ip(self):
        self.assertNotIn("203", visitor_hash("203.0.113.7"))

    def test_blank_ip_gives_blank_hash(self):
        self.assertEqual(visitor_hash(""), "")


# ---------------------------------------------------------------------------
# Visitor statistics: middleware
# ---------------------------------------------------------------------------
@override_settings(ANALYTICS_ENABLED=True, ANALYTICS_SALT="test-salt")
class AnalyticsMiddlewareTests(TestCase):
    BROWSER_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    def _get(self, path="/", **meta):
        meta.setdefault("HTTP_USER_AGENT", self.BROWSER_UA)
        meta.setdefault("REMOTE_ADDR", "203.0.113.7")
        return self.client.get(path, **meta)

    def test_records_an_anonymous_page_view(self):
        self._get(reverse("index"), HTTP_CF_IPCOUNTRY="DE")

        view = PageView.objects.get()
        self.assertEqual(view.path, "/")
        self.assertEqual(view.country, "DE")
        self.assertEqual(view.device_type, "desktop")
        self.assertEqual(view.browser, "Chrome")
        self.assertEqual(view.is_bot, False)

    def test_never_stores_the_ip_address(self):
        self._get(reverse("index"))

        view = PageView.objects.get()
        for value in (view.path, view.visitor_hash, view.referrer, view.country):
            self.assertNotIn("203.0.113.7", value)

    def test_staff_visits_are_not_recorded(self):
        user = User.objects.create_user("owner", password="x", is_staff=True)
        self.client.force_login(user)

        self._get(reverse("index"))

        self.assertEqual(PageView.objects.count(), 0)

    def test_non_staff_logged_in_user_is_still_counted(self):
        user = User.objects.create_user("visitor", password="x")
        self.client.force_login(user)

        self._get(reverse("index"))

        self.assertEqual(PageView.objects.count(), 1)

    def test_admin_and_static_paths_are_excluded(self):
        self._get("/admin/")
        self._get("/dashboard/")
        self._get("/static/css/tailwind.css")
        self._get("/favicon.ico")

        self.assertEqual(PageView.objects.count(), 0)

    def test_post_requests_are_not_recorded(self):
        self.client.post(reverse("contact"), {})

        self.assertEqual(PageView.objects.count(), 0)

    def test_error_responses_are_not_recorded(self):
        self._get("/this-page-does-not-exist/")

        self.assertEqual(PageView.objects.count(), 0)

    def test_bot_traffic_is_recorded_but_flagged(self):
        self._get(reverse("index"), HTTP_USER_AGENT="Googlebot/2.1")

        self.assertTrue(PageView.objects.get().is_bot)

    def test_referrer_is_stored_as_a_domain_only(self):
        self._get(reverse("index"), HTTP_REFERER="https://www.google.com/search?q=me")

        self.assertEqual(PageView.objects.get().referrer, "google.com")

    @override_settings(ANALYTICS_ENABLED=False)
    def test_recording_can_be_disabled(self):
        self._get(reverse("index"))

        self.assertEqual(PageView.objects.count(), 0)


# ---------------------------------------------------------------------------
# Visitor statistics: dashboard
# ---------------------------------------------------------------------------
@override_settings(ANALYTICS_RETENTION_DAYS=90)
class DashboardTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("owner", password="x", is_staff=True)

    def test_anonymous_visitor_cannot_open_the_dashboard(self):
        response = self.client.get(reverse("dashboard"))
        self.assertIn(response.status_code, (302, 403))

    def test_signed_in_non_staff_cannot_open_the_dashboard(self):
        self.client.force_login(User.objects.create_user("someone", password="x"))
        self.assertIn(self.client.get(reverse("dashboard")).status_code, (302, 403))

    def test_staff_can_open_the_dashboard(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_dashboard_reports_counts_and_ignores_bots(self):
        PageView.objects.create(path="/", visitor_hash="aaa")
        PageView.objects.create(path="/", visitor_hash="aaa")
        PageView.objects.create(path="/skills/", visitor_hash="bbb")
        PageView.objects.create(path="/", visitor_hash="ccc", is_bot=True)

        self.client.force_login(self.staff)
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.context["views_window"], 3)
        self.assertEqual(response.context["unique_window"], 2)
        self.assertEqual(response.context["bot_views"], 1)

    def test_dashboard_chart_covers_every_day_in_the_window(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("dashboard"))

        chart = response.context["chart"]
        self.assertEqual(len(chart), 30)
        self.assertEqual(chart[-1]["day"], timezone.localdate())


# ---------------------------------------------------------------------------
# Visitor statistics: retention
# ---------------------------------------------------------------------------
class PurgePageViewsTests(TestCase):
    def _old(self, days):
        return PageView.objects.create(
            path="/",
            viewed_at=timezone.now() - timedelta(days=days),
        )

    @override_settings(ANALYTICS_RETENTION_DAYS=90)
    def test_deletes_only_rows_past_the_retention_window(self):
        stale = self._old(120)
        fresh = self._old(10)

        call_command("purge_pageviews", stdout=StringIO())

        self.assertFalse(PageView.objects.filter(pk=stale.pk).exists())
        self.assertTrue(PageView.objects.filter(pk=fresh.pk).exists())

    @override_settings(ANALYTICS_RETENTION_DAYS=90)
    def test_dry_run_deletes_nothing(self):
        stale = self._old(120)

        out = StringIO()
        call_command("purge_pageviews", "--dry-run", stdout=out)

        self.assertTrue(PageView.objects.filter(pk=stale.pk).exists())
        self.assertIn("Would delete", out.getvalue())

    def test_days_argument_overrides_the_setting(self):
        stale = self._old(10)

        call_command("purge_pageviews", "--days", "5", stdout=StringIO())

        self.assertFalse(PageView.objects.filter(pk=stale.pk).exists())


# ---------------------------------------------------------------------------
# Privacy notice
# ---------------------------------------------------------------------------
class PrivacyPageTests(TestCase):
    def test_privacy_page_is_publicly_readable(self):
        response = self.client.get(reverse("privacy"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Privacy Notice")

    def test_privacy_page_discloses_the_statistics_retention_window(self):
        response = self.client.get(reverse("privacy"))
        self.assertContains(response, str(settings.ANALYTICS_RETENTION_DAYS))

    def test_footer_links_to_the_privacy_page(self):
        response = self.client.get(reverse("index"))
        self.assertContains(response, reverse("privacy"))
