"""Delete page views older than the retention window (GDPR storage limitation)."""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from portfolio_app.models import PageView


class Command(BaseCommand):
    help = (
        "Delete page views older than the retention window. "
        "Defaults to settings.ANALYTICS_RETENTION_DAYS, with crawler rows "
        "limited to ANALYTICS_BOT_RETENTION_DAYS."
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
        # Crawler rows are cheap to produce and worth little, so they get a
        # shorter window. An explicit --days still shortens them further.
        bot_days = min(settings.ANALYTICS_BOT_RETENTION_DAYS, days)
        now = timezone.now()
        cutoff = now - timedelta(days=days)
        bot_cutoff = now - timedelta(days=bot_days)

        stale = PageView.objects.filter(
            Q(viewed_at__lt=cutoff) | Q(is_bot=True, viewed_at__lt=bot_cutoff)
        )
        count = stale.count()

        if options['dry_run']:
            self.stdout.write(
                f"Would delete {count} page view(s): human older than {days} "
                f"days (before {cutoff:%Y-%m-%d %H:%M}); bots older than "
                f"{bot_days} days (before {bot_cutoff:%Y-%m-%d %H:%M})."
            )
            return

        if count == 0:
            self.stdout.write(
                f"No page views older than {days} days (bots: {bot_days} days)."
            )
            return

        stale.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {count} page view(s) older than {days} days "
                f"(bots: {bot_days} days)."
            )
        )
