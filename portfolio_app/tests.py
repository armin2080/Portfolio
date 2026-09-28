from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_recaptcha.client import RecaptchaResponse

from .analytics import (
    country_from_request,
    is_bot,
    parse_user_agent,
    referrer_domain,
    visitor_hash,
)
from .context_processors import profile_context
from .models import Category, ContactMessage, PageView, Profile, Project, Skill
from .templatetags.custom_filters import (
    splitlines,
    years_since,
    years_since_display,
)
from .utils import client_ip

# Use an in-memory cache so django-ratelimit state never leaks between runs.
LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "portfolio-tests",
    }
}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class ProfileSingletonTests(TestCase):
    def test_first_profile_can_be_created(self):
        profile = Profile.objects.create(name="Armin")
        self.assertEqual(Profile.objects.count(), 1)
        self.assertEqual(str(profile), "Armin")

    def test_second_profile_is_rejected(self):
        Profile.objects.create(name="Armin")
        with self.assertRaises(ValueError):
            Profile.objects.create(name="Someone Else")

    def test_existing_profile_can_be_updated(self):
        profile = Profile.objects.create(name="Armin")
        profile.title = "Senior Data Scientist"
        profile.save()
        profile.refresh_from_db()
        self.assertEqual(profile.title, "Senior Data Scientist")


class SkillModelTests(TestCase):
    def test_skill_type_defaults_to_technical(self):
        skill = Skill.objects.create(name="Python", start_date=date(2020, 1, 1))
        self.assertEqual(skill.skill_type, Skill.SkillType.TECHNICAL)

    def test_skills_ordered_by_start_date(self):
        Skill.objects.create(name="B", start_date=date(2022, 1, 1))
        Skill.objects.create(name="A", start_date=date(2018, 1, 1))
        self.assertEqual([s.name for s in Skill.objects.all()], ["A", "B"])


class ProjectModelTests(TestCase):
    def test_projects_ordered_newest_first(self):
        Project.objects.create(name="Old", description="d", link="https://a", date=date(2020, 1, 1))
        Project.objects.create(name="New", description="d", link="https://b", date=date(2024, 1, 1))
        self.assertEqual([p.name for p in Project.objects.all()], ["New", "Old"])


# ---------------------------------------------------------------------------
# Template filters
# ---------------------------------------------------------------------------
class CustomFilterTests(TestCase):
    def test_splitlines_on_multiline_string(self):
        self.assertEqual(splitlines("a\nb\nc"), ["a", "b", "c"])

    def test_splitlines_on_empty_value(self):
        self.assertEqual(splitlines(""), [])
        self.assertEqual(splitlines(None), [])

    def test_years_since(self):
        self.assertEqual(years_since(timezone.now().date() - relativedelta(years=5)), 5)
        self.assertEqual(years_since(None), 0)

    def test_years_since_display_pluralisation(self):
        self.assertEqual(years_since_display(timezone.now().date() - relativedelta(years=3)), "3 years")
        self.assertEqual(years_since_display(timezone.now().date() - relativedelta(years=1)), "1 year")
        self.assertEqual(years_since_display(timezone.now().date()), "Less than a year")


# ---------------------------------------------------------------------------
# Page views
# ---------------------------------------------------------------------------
class PublicPageTests(TestCase):
    def setUp(self):
        self.skill = Skill.objects.create(name="Python", start_date=date(2019, 1, 1))
        self.category = Category.objects.create(name="Data Science", slug="data-science")
        self.other_category = Category.objects.create(name="Web", slug="web")
        self.project = Project.objects.create(
            name="Classified",
            description="A project",
            link="https://example.com",
            category=self.category,
            date=date(2023, 1, 1),
        )
        self.other_project = Project.objects.create(
            name="Other",
            description="Another project",
            link="https://example.org",
            category=self.other_category,
            date=date(2022, 1, 1),
        )

    def test_static_pages_return_200(self):
        for name in ("index", "skills", "projects", "resume", "contact", "privacy"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_category_filter_only_returns_matching_projects(self):
        response = self.client.get(reverse("projects"), {"category": "data-science"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Classified")
        self.assertNotContains(response, "Other")
        self.assertEqual(response.context["active_category"], "data-science")

    def test_unknown_category_returns_no_projects(self):
        response = self.client.get(reverse("projects"), {"category": "does-not-exist"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Classified")
        self.assertContains(response, "No projects found")

    def test_homepage_shows_at_most_three_skills(self):
        for i in range(5):
            Skill.objects.create(name=f"Skill {i}", start_date=date(2015, 1, 1))
        response = self.client.get(reverse("index"))
        self.assertEqual(len(response.context["skills"]), 3)

    # Analytics is disabled here so the assertion measures the projects page
    # itself, not the page-view INSERT added by the tracking middleware.
    @override_settings(ANALYTICS_ENABLED=False, CACHES=LOCMEM_CACHE)
    def test_projects_page_does_not_issue_queries_per_project(self):
        # select_related/prefetch_related keep the query count flat as projects
        # and their skills grow.
        for i in range(5):
            project = Project.objects.create(
                name=f"Project {i}",
                description="d",
                link="https://example.com",
                category=self.category,
                date=date(2023, 1, 1),
            )
            project.skills_used.set([self.skill])

        # Prime the caches first: the active theme and the "last updated" value
        # are cached, so this measures the steady state rather than a cold cache.
        self.client.get(reverse("projects"))

        with self.assertNumQueries(4):
            # 1 profile, 1 categories, 1 projects, 1 skills prefetch.
            # Flat regardless of how many projects/skills exist.
            self.client.get(reverse("projects"))


class SitemapAndRobotsTests(TestCase):
    def test_robots_txt_points_at_the_sitemap(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sitemap: https://armin2080.de/sitemap.xml")

    def test_sitemap_lists_every_page(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        for path in ("/", "/skills/", "/projects/", "/resume/", "/contact/"):
            with self.subTest(path=path):
                self.assertIn(path, content)


class MediaServingTests(TestCase):
    def test_media_is_served_with_a_cache_header(self):
        with TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                (Path(tmp) / "example.txt").write_text("hello")
                response = self.client.get("/media/example.txt")

        self.assertEqual(response.status_code, 200)
        self.assertIn("max-age", response["Cache-Control"])


@override_settings(CACHES=LOCMEM_CACHE)
class ContextProcessorTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_last_updated_is_cached_between_requests(self):
        Skill.objects.create(name="Python", start_date=date(2020, 1, 1))

        first = profile_context(None)["last_updated"]
        self.assertIsNotNone(first)

        # The second call only queries the profile; the six aggregates are cached.
        with self.assertNumQueries(1):
            second = profile_context(None)["last_updated"]

        self.assertEqual(first, second)

    def test_site_url_is_exposed_to_templates(self):
        self.assertEqual(profile_context(None)["site_url"], "https://armin2080.de")


# ---------------------------------------------------------------------------
# Client IP resolution (rate limiting behind Cloudflare)
# ---------------------------------------------------------------------------
class ClientIPTests(TestCase):
    def _request(self, **meta):
        request = RequestFactory().get("/contact/")
        request.META.update(meta)
        return request

    def test_prefers_cloudflare_header_over_remote_addr(self):
        request = self._request(
            HTTP_CF_CONNECTING_IP="203.0.113.7",
            REMOTE_ADDR="172.71.0.1",  # Cloudflare edge address
        )
        self.assertEqual(client_ip(request), "203.0.113.7")

    def test_falls_back_to_first_x_forwarded_for_entry(self):
        request = self._request(
            HTTP_X_FORWARDED_FOR="198.51.100.4, 172.71.0.1",
            REMOTE_ADDR="172.71.0.1",
        )
        self.assertEqual(client_ip(request), "198.51.100.4")

    def test_ignores_malformed_header_values(self):
        request = self._request(
            HTTP_CF_CONNECTING_IP="not-an-ip",
            HTTP_X_FORWARDED_FOR="also-not-an-ip",
            REMOTE_ADDR="172.71.0.1",
        )
        self.assertEqual(client_ip(request), "172.71.0.1")

    def test_never_returns_an_empty_value(self):
        # django-ratelimit parses this value, so it must always be usable.
        # RequestFactory defaults REMOTE_ADDR to 127.0.0.1, so clear it.
        request = self._request(REMOTE_ADDR="")
        self.assertEqual(client_ip(request), "0.0.0.0")

    @override_settings(TRUST_PROXY_HEADERS=False)
    def test_proxy_headers_can_be_disabled(self):
        request = self._request(
            HTTP_CF_CONNECTING_IP="203.0.113.7",
            REMOTE_ADDR="172.71.0.1",
        )
        self.assertEqual(client_ip(request), "172.71.0.1")


@override_settings(
    CACHES=LOCMEM_CACHE,
    RECAPTCHA_PUBLIC_KEY="test-public-key",
    RECAPTCHA_PRIVATE_KEY="test-private-key",
)
class RateLimitPerVisitorTests(TestCase):
    """Behind a proxy every visitor must get an independent rate limit."""

    def setUp(self):
        cache.clear()

    def _post(self, from_ip):
        payload = {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "message": "Hello there",
            "url": "",
            "captcha": "test-token",
        }
        return self.client.post(
            reverse("contact"),
            payload,
            REMOTE_ADDR="172.71.0.1",  # same Cloudflare edge for everyone
            HTTP_CF_CONNECTING_IP=from_ip,
        )

    @patch("django_recaptcha.fields.client.submit")
    def test_visitors_do_not_share_one_rate_limit_bucket(self, mocked_submit):
        mocked_submit.return_value = RecaptchaResponse(is_valid=True)

        # First visitor exhausts their own allowance.
        for _ in range(3):
            self._post("203.0.113.7")
        self.assertEqual(len(mail.outbox), 3)

        blocked = self._post("203.0.113.7")
        self.assertTrue(blocked.context["rate_limited"])

        # A different visitor is unaffected by the first visitor's usage.
        allowed = self._post("198.51.100.9")
        self.assertEqual(allowed.status_code, 302)
        self.assertEqual(len(mail.outbox), 4)


# ---------------------------------------------------------------------------
# Contact form
# ---------------------------------------------------------------------------
@override_settings(
    CACHES=LOCMEM_CACHE,
    RECAPTCHA_PUBLIC_KEY="test-public-key",
    RECAPTCHA_PRIVATE_KEY="test-private-key",
)
class ContactViewTests(TestCase):
    url_name = "contact"

    def setUp(self):
        cache.clear()

    def _valid_payload(self, **overrides):
        payload = {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "message": "Hello there",
            "url": "",
            # ReCaptchaV3 posts its token under the field name ("captcha").
            "captcha": "test-token",
        }
        payload.update(overrides)
        return payload

    def test_get_renders_form(self):
        response = self.client.get(reverse(self.url_name))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<form")
        self.assertContains(response, "Send Message")

    @patch("django_recaptcha.fields.client.submit")
    def test_valid_submission_sends_email_and_redirects(self, mocked_submit):
        mocked_submit.return_value = RecaptchaResponse(is_valid=True)

        response = self.client.post(reverse(self.url_name), self._valid_payload())

        self.assertRedirects(response, reverse("contact_success"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Jane Doe", mail.outbox[0].subject)

    @patch("django_recaptcha.fields.client.submit")
    def test_valid_submission_is_stored(self, mocked_submit):
        mocked_submit.return_value = RecaptchaResponse(is_valid=True)

        self.client.post(reverse(self.url_name), self._valid_payload())

        stored = ContactMessage.objects.get()
        self.assertEqual(stored.name, "Jane Doe")
        self.assertEqual(stored.email, "jane@example.com")
        self.assertEqual(stored.message, "Hello there")

    @patch("portfolio_app.views.send_mail", side_effect=Exception("smtp down"))
    @patch("django_recaptcha.fields.client.submit")
    def test_message_is_kept_when_email_fails(self, mocked_submit, mocked_send):
        mocked_submit.return_value = RecaptchaResponse(is_valid=True)

        # The failure is logged, but the visitor still sees the success page.
        with self.assertLogs("portfolio_app.views", level="ERROR"):
            response = self.client.post(reverse(self.url_name), self._valid_payload())

        self.assertRedirects(response, reverse("contact_success"))
        self.assertEqual(ContactMessage.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    @patch("django_recaptcha.fields.client.submit")
    def test_refreshing_success_page_does_not_resend(self, mocked_submit):
        mocked_submit.return_value = RecaptchaResponse(is_valid=True)
        self.client.post(reverse(self.url_name), self._valid_payload())

        # The success page is reached via GET (PRG), so reloading it never re-POSTs.
        self.assertEqual(self.client.get(reverse("contact_success")).status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

    def test_honeypot_redirects_without_sending(self):
        response = self.client.post(
            reverse(self.url_name), self._valid_payload(url="http://spam.example")
        )
        self.assertRedirects(response, reverse("contact_success"))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_invalid_submission_shows_errors_and_sends_nothing(self):
        response = self.client.post(reverse(self.url_name), self._valid_payload(name=""))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["form"].is_valid())
        self.assertEqual(len(mail.outbox), 0)

    @patch("django_recaptcha.fields.client.submit")
    def test_rate_limit_blocks_fourth_message(self, mocked_submit):
        mocked_submit.return_value = RecaptchaResponse(is_valid=True)

        for _ in range(3):
            self.client.post(reverse(self.url_name), self._valid_payload())
        self.assertEqual(len(mail.outbox), 3)

        response = self.client.post(reverse(self.url_name), self._valid_payload())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["rate_limited"])
        self.assertEqual(len(mail.outbox), 3)

