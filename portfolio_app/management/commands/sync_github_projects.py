"""Import and refresh portfolio projects from public GitHub repositories.

Run daily by a systemd timer (see SYSTEMD_DEPLOY.md). Safe to run by hand:

    python manage.py sync_github_projects --dry-run
    python manage.py sync_github_projects

Nothing is ever deleted, and edits made in the admin are never overwritten, so a
run cannot damage existing content.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from portfolio_app.github_sync import sync_projects


class Command(BaseCommand):
    help = "Import new projects from public GitHub repositories and refresh existing ones."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would change without writing anything.',
        )
        parser.add_argument(
            '--username',
            default=None,
            help='GitHub user to read (defaults to settings.GITHUB_USERNAME).',
        )

    def handle(self, *args, **options):
        username = options['username'] or getattr(settings, 'GITHUB_USERNAME', '')
        if not username:
            env_file = settings.BASE_DIR / '.env'
            raise CommandError(
                'No GitHub username configured, so there is nothing to sync.\n'
                f'Set GITHUB_USERNAME in {env_file} or pass --username.\n'
                f'That file is {"present" if env_file.exists() else "MISSING"}'
                + (
                    ' and has no GITHUB_USERNAME line set to a value — note that a'
                    ' line like "GITHUB_USERNAME=" is empty, and an exported shell'
                    ' variable takes precedence over the file.'
                    if env_file.exists()
                    else ' — create it, or pass --username.'
                )
            )

        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write('Dry run: nothing will be written.')

        try:
            report = sync_projects(username=username, dry_run=dry_run)
        except Exception as exc:
            # The site is unaffected by a failed sync; surface it clearly so the
            # timer's logs are useful.
            raise CommandError(f'GitHub sync failed: {exc}') from exc

        self.stdout.write(f'@{username}: {report.summary()}')

        if report.created:
            self.stdout.write(self.style.SUCCESS('\nCreated:'))
            for name in report.created:
                self.stdout.write(f'  + {name}')
        if report.updated:
            self.stdout.write('\nRefreshed:')
            for name in report.updated:
                self.stdout.write(f'  ~ {name}')
        if report.errors:
            self.stdout.write(self.style.ERROR('\nErrors:'))
            for error in report.errors:
                self.stdout.write(f'  ! {error}')
            raise CommandError(f'{len(report.errors)} repositories could not be synced.')
