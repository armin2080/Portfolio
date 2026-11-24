"""Write a consistent snapshot of the site's state.

The archive contains the SQLite database, uploaded media and ``.env``. The
database is copied with SQLite's backup API rather than by reading the file, so
a page being written at the same moment cannot produce a torn copy.

Because ``.env`` is included, the archive holds the site's secrets. It is
created with mode 0600 inside a 0700 directory; treat a copy moved elsewhere
with the same care.
"""

import os
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Back up the database, uploaded media and .env to a timestamped "
        "archive, then delete archives older than the retention window. The "
        "daily portfolio-backup timer runs this."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--output-dir',
            default=None,
            help='Directory for the archive. Defaults to settings.BACKUP_DIR.',
        )
        parser.add_argument(
            '--keep-days',
            type=int,
            default=None,
            help='Delete archives older than this. Defaults to BACKUP_KEEP_DAYS.',
        )
        parser.add_argument(
            '--no-media',
            action='store_true',
            help='Leave uploaded media out of the archive (database and .env only).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would be written without writing anything.',
        )

    def handle(self, *args, **options):
        output_dir = Path(options['output_dir'] or settings.BACKUP_DIR)
        keep_days = (
            options['keep_days']
            if options['keep_days'] is not None
            else settings.BACKUP_KEEP_DAYS
        )
        db_path = Path(settings.DATABASES['default']['NAME'])
        media_root = Path(settings.MEDIA_ROOT)
        env_file = Path(settings.BASE_DIR) / '.env'

        if not db_path.exists():
            raise CommandError(f"Database not found: {db_path}")
        if keep_days < 1:
            raise CommandError('--keep-days must be at least 1')

        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        archive = output_dir / f'portfolio-{stamp}.tar.gz'

        if options['dry_run']:
            self.stdout.write(
                f"Would write {archive} from {db_path}"
                + (f", {media_root}" if not options['no_media'] else '')
                + (f", {env_file}" if env_file.exists() else '')
                + f", keeping {keep_days} day(s)."
            )
            return

        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        # Work inside the destination directory so the finished archive can be
        # renamed into place: a monitor must never see a half-written file.
        with tempfile.TemporaryDirectory(dir=output_dir, prefix='.backup-') as tmp:
            tmp = Path(tmp)
            snapshot = tmp / 'db.sqlite3'
            self._copy_database(db_path, snapshot)

            partial = tmp / archive.name
            with tarfile.open(partial, 'w:gz') as tar:
                tar.add(snapshot, arcname='db.sqlite3')
                if not options['no_media'] and media_root.is_dir():
                    tar.add(media_root, arcname='media')
                if env_file.is_file():
                    tar.add(env_file, arcname='.env')

            os.chmod(partial, 0o600)
            os.replace(partial, archive)

        self._prune(output_dir, keep_days)

        # The watchdog checks this file's age; a failed run leaves it untouched,
        # so a missed backup is visible rather than silently replacing the mark.
        state = output_dir / 'last-success'
        state.write_text(datetime.now().isoformat(timespec='seconds') + '\n')
        os.chmod(state, 0o600)

        size_mb = archive.stat().st_size / (1024 * 1024)
        self.stdout.write(
            self.style.SUCCESS(f"Wrote {archive} ({size_mb:.1f} MiB).")
        )

    def _copy_database(self, db_path, destination):
        """Copy the database through SQLite's online backup API."""
        source = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        os.chmod(destination, 0o600)

    def _prune(self, output_dir, keep_days):
        cutoff = datetime.now() - timedelta(days=keep_days)
        for old in output_dir.glob('portfolio-*.tar.gz'):
            try:
                modified = datetime.fromtimestamp(old.stat().st_mtime)
            except OSError:
                continue
            if modified < cutoff:
                old.unlink(missing_ok=True)
                self.stdout.write(f"Removed expired backup {old.name}.")
