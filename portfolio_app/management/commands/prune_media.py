"""Delete media files that nothing in the database references.

Replacing an image in the admin leaves the previous file on disk. Django never
removes it, so `media/` accumulates files that are still reachable by URL but
belong to nothing. This command finds and optionally removes them.

    python manage.py prune_media                # report only
    python manage.py prune_media --delete       # actually remove

**Reporting is the default and deletion must be asked for explicitly.** The cost
of a false positive is deleting content that cannot be recovered from the
database, so the safe path is the one you get by accident.

Two guards exist for the same reason:

* References are discovered from every ``FileField`` in every installed app (see
  ``portfolio_app.media``), never a hand-written list. A hand-written list of
  image fields once reported the live resume PDFs as unreferenced.
* The command refuses to delete when the database references *no* files at all
  while files exist on disk. That combination means the wrong database or the
  wrong ``MEDIA_ROOT``, and treating it as "everything is an orphan" would wipe
  the media directory.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from portfolio_app.media import (
    age_in_hours,
    files_on_disk,
    find_orphans,
    human_size,
    referenced_files,
    upload_grace_hours,
)


class Command(BaseCommand):
    help = 'Find (and optionally delete) media files that nothing references.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--delete',
            action='store_true',
            help=(
                'Actually remove the unreferenced files. Without this the command '
                'only reports what it found.'
            ),
        )
        parser.add_argument(
            '--prune-empty-dirs',
            action='store_true',
            help='Also remove directories left empty after deleting files.',
        )
        parser.add_argument(
            '--min-age-hours',
            type=int,
            default=None,
            help=(
                'Leave files modified more recently than this alone (default '
                f'{upload_grace_hours()}h, settings.MEDIA_PRUNE_MIN_AGE_HOURS). '
                'Django writes the file before the row that references it, so a '
                'brand-new upload briefly looks unreferenced; this stops an '
                'automated run from deleting one. Use 0 to disable.'
            ),
        )

    def handle(self, *args, **options):
        do_delete = options['delete']
        root = Path(settings.MEDIA_ROOT)
        grace_hours = (
            upload_grace_hours() if options['min_age_hours'] is None
            else options['min_age_hours']
        )

        if not root.exists():
            raise CommandError(f'MEDIA_ROOT does not exist: {root}')

        on_disk = files_on_disk(root)
        referenced = referenced_files()
        candidates, missing = find_orphans(root)

        # Hold back anything written too recently. Django saves the file before
        # the row that references it, so an upload in flight looks unreferenced.
        orphans, too_new = [], []
        for name in candidates:
            (too_new if age_in_hours(root / name) < grace_hours else orphans).append(name)
        orphans.sort()
        too_new.sort()

        total_bytes = sum((root / name).stat().st_size for name in orphans)

        self.stdout.write(f'MEDIA_ROOT: {root}')
        self.stdout.write(
            f'  {len(on_disk)} file(s) on disk, '
            f'{len(referenced & on_disk)} of them referenced'
        )
        self.stdout.write(f'  grace period: {grace_hours}h'
                          + ('' if grace_hours else '  (disabled)'))

        if too_new:
            self.stdout.write(
                f'\n{len(too_new)} unreferenced file(s) are newer than the grace '
                'period and were left alone:'
            )
            for name in too_new:
                self.stdout.write(f'  . {name}  ({age_in_hours(root / name):.1f}h old)')

        if missing:
            # Reported, never "fixed": a missing file is a database problem, and
            # deleting something would not help.
            self.stdout.write(self.style.WARNING(
                f'\n{len(missing)} file(s) are referenced but missing on disk:'
            ))
            for name in missing:
                self.stdout.write(f'  ? {name}')

        if not orphans:
            if too_new:
                self.stdout.write(self.style.SUCCESS(
                    '\nNothing to delete: the only unreferenced file(s) are newer '
                    'than the grace period.'
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    '\nNo unreferenced files. Nothing to do.'
                ))
            return

        # A database that references no files at all, while files sit on disk, is
        # far more likely to be the wrong database or MEDIA_ROOT than a media
        # directory where nothing is used.
        if not referenced:
            message = (
                'The database references no files at all, which usually means the '
                'wrong database or MEDIA_ROOT is in use rather than that all the '
                'media is rubbish.'
            )
            if do_delete:
                raise CommandError(
                    f'Refusing to delete: {message} Check the configuration first.'
                )
            self.stdout.write(self.style.WARNING(f'\n{message}'))

        self.stdout.write(f'\n{len(orphans)} unreferenced file(s), {human_size(total_bytes)}:')
        for name in orphans:
            size = (root / name).stat().st_size
            self.stdout.write(f'  - {name}  ({human_size(size)})')

        if not do_delete:
            self.stdout.write(self.style.WARNING(
                f'\nNothing was deleted. Re-run with --delete to remove these '
                f'{len(orphans)} file(s) ({human_size(total_bytes)}).'
            ))
            return

        removed = 0
        failed = 0
        for name in orphans:
            try:
                (root / name).unlink()
                removed += 1
            except OSError as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(f'  ! could not delete {name}: {exc}'))

        if options['prune_empty_dirs']:
            self._remove_empty_dirs(root)

        self.stdout.write(self.style.SUCCESS(
            f'\nDeleted {removed} file(s), freeing {human_size(total_bytes)}'
            + (f', {failed} could not be removed' if failed else '')
        ))

    def _remove_empty_dirs(self, root):
        """Remove directories that are empty, deepest first.

        Only empty ones: a directory still holding a referenced file is left
        alone.
        """
        removed = 0
        for path in sorted(root.rglob('*'), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir() and not path.is_symlink() and not any(path.iterdir()):
                try:
                    path.rmdir()
                    removed += 1
                except OSError:
                    pass
        if removed:
            self.stdout.write(f'Removed {removed} empty director(ies).')
