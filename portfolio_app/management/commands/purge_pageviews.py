"""Delete page views older than the retention window (GDPR storage limitation)."""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from portfolio_app.models import PageView


class Command(BaseCommand):
    help = (
        "Delete page views older than the retention window. "
        "Defaults to settings.ANALYTICS_RETENTION_DAYS."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=None,
            help='Override the configured retention window.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would be deleted without deleting anything.',
        )

    def handle(self, *args, **options):
        days = options['days'] or settings.ANALYTICS_RETENTION_DAYS
        cutoff = timezone.now() - timedelta(days=days)

        stale = PageView.objects.filter(viewed_at__lt=cutoff)
        count = stale.count()

        if options['dry_run']:
            self.stdout.write(
                f"Would delete {count} page view(s) older than {days} days "
                f"(before {cutoff:%Y-%m-%d %H:%M})."
            )
            return

        if count == 0:
            self.stdout.write(f"No page views older than {days} days.")
            return

        stale.delete()
        self.stdout.write(
            self.style.SUCCESS(f"Deleted {count} page view(s) older than {days} days.")
        )
