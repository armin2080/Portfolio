import os
import sqlite3
import tarfile
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .models import PageView

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "portfolio-monitoring-tests",
    }
}
DUMMY_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.dummy.DummyCache",
    }
}


class HealthEndpointTests(TestCase):
    @override_settings(CACHES=LOCMEM_CACHE)
    def test_health_endpoint_reports_ok(self):
        response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["checks"]["database"], "ok")
        self.assertEqual(payload["checks"]["cache"], "ok")
        self.assertIn("latex", payload["checks"])

    @override_settings(CACHES=LOCMEM_CACHE)
    def test_health_endpoint_is_not_cached_or_indexed(self):
        response = self.client.get(reverse("healthz"))

        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["X-Robots-Tag"], "noindex")

    @override_settings(CACHES=LOCMEM_CACHE)
    def test_health_endpoint_is_not_counted_as_a_page_view(self):
        self.client.get(reverse("healthz"))

        self.assertEqual(PageView.objects.count(), 0)

    @override_settings(CACHES=DUMMY_CACHE)
    def test_health_endpoint_fails_when_the_cache_is_unusable(self):
        response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["checks"]["cache"], "error")
        self.assertEqual(payload["checks"]["database"], "ok")

    @override_settings(CACHES=LOCMEM_CACHE)
    def test_missing_latex_engine_is_reported_without_failing_the_probe(self):
        with patch(
            "portfolio_app.views.resume_latex.engine_available",
            return_value=False,
        ):
            response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checks"]["latex"], "unavailable")

    def test_robots_txt_disallows_the_health_endpoint(self):
        response = self.client.get("/robots.txt")

        self.assertContains(response, "Disallow: /healthz/")


class BackupCommandTests(SimpleTestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db = self.root / "db.sqlite3"
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("INSERT INTO sample (name) VALUES ('kept')")
        self.media = self.root / "media"
        self.media.mkdir()
        (self.media / "photo.webp").write_bytes(b"not-really-an-image")
        self.output = self.root / "backups"
        self.database_settings = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(self.db),
            }
        }

    def run_backup(self, **options):
        with override_settings(
            DATABASES=self.database_settings,
            MEDIA_ROOT=self.media,
        ):
            out = StringIO()
            call_command("backup_portfolio", stdout=out, **options)
            return out.getvalue()

    def archives(self):
        return sorted(self.output.glob("portfolio-*.tar.gz"))

    def test_writes_a_consistent_archive_with_media_and_env(self):
        env_file = self.root / ".env"
        env_file.write_text("SECRET_KEY=test\n")
        output = self.run_backup(output_dir=str(self.output), keep_days=7)

        archives = self.archives()
        self.assertEqual(len(archives), 1)
        self.assertIn("Wrote", output)
        self.assertTrue((self.output / "last-success").is_file())

        # The archive is private because it contains .env.
        self.assertEqual(archives[0].stat().st_mode & 0o777, 0o600)

        with tarfile.open(archives[0]) as tar:
            names = tar.getnames()
        self.assertIn("db.sqlite3", names)
        self.assertIn("media/photo.webp", names)

    def test_archived_database_is_readable_and_complete(self):
        self.run_backup(output_dir=str(self.output), keep_days=7)

        with tarfile.open(self.archives()[0]) as tar:
            extracted = tar.extractfile("db.sqlite3")
            self.assertIsNotNone(extracted)
            snapshot = self.root / "snapshot.sqlite3"
            snapshot.write_bytes(extracted.read())

        with sqlite3.connect(snapshot) as conn:
            self.assertEqual(conn.execute("SELECT name FROM sample").fetchone(), ("kept",))

    def test_prunes_archives_older_than_the_retention_window(self):
        self.output.mkdir()
        old = self.output / "portfolio-20200101-000000.tar.gz"
        old.write_bytes(b"old")
        old_time = (datetime.now() - timedelta(days=10)).timestamp()
        os.utime(old, (old_time, old_time))

        self.run_backup(output_dir=str(self.output), keep_days=7)

        self.assertFalse(old.exists())
        self.assertEqual(len(self.archives()), 1)

    def test_dry_run_writes_nothing(self):
        output = self.run_backup(
            output_dir=str(self.output), keep_days=7, dry_run=True
        )

        self.assertIn("Would write", output)
        self.assertFalse(self.output.exists())

    def test_refuses_a_missing_database(self):
        with override_settings(
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": str(self.root / "missing.sqlite3"),
                }
            },
            MEDIA_ROOT=self.media,
        ):
            with self.assertRaises(CommandError):
                call_command("backup_portfolio", output_dir=str(self.output))
