"""Convert every stored image to WebP, shrinking anything oversized.

Run this once after the site has images that were uploaded before conversion was
automatic, and again any time you want to re-check:

    python manage.py convert_images_to_webp --dry-run
    python manage.py convert_images_to_webp

It walks the image fields, converts each file, points the database at the new
WebP and deletes the original. Files already in WebP are skipped, so running it
again is harmless.

This rewrites data, so it reports what it did rather than working silently, and
``--dry-run`` shows the same report without touching anything. Take a backup of
``media/`` first — there is no undo beyond restoring it.
"""

from pathlib import Path

from django.core.management.base import BaseCommand

from portfolio_app.images import (
    WEBP_EXTENSION,
    convert_to_webp,
    image_fields,
    webp_name,
)


def human_size(num_bytes):
    """'1.6 MB' — sizes here span four orders of magnitude, so bytes are unhelpful."""
    size = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:,.0f} {unit}' if unit == 'B' else f'{size:,.1f} {unit}'
        size /= 1024


class Command(BaseCommand):
    help = 'Convert stored images to WebP and shrink oversized ones.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would change without writing or deleting anything.',
        )
        parser.add_argument(
            '--keep-originals',
            action='store_true',
            help=(
                'Leave the original files on disk. They are unreferenced after '
                'the switch, so this only wastes space — useful if you want to '
                'compare before deleting.'
            ),
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        keep_originals = options['keep_originals']

        if dry_run:
            self.stdout.write('Dry run: nothing will be written or deleted.')
        self.stdout.write('')

        converted = 0
        skipped = 0
        failed = 0
        bytes_before = 0
        bytes_after = 0

        for model, field_name in image_fields():
            label = model._meta.label.split('.')[-1]
            queryset = model.objects.exclude(**{field_name: ''}).exclude(
                **{f'{field_name}__isnull': True}
            )

            for instance in queryset:
                field_file = getattr(instance, field_name)
                name = field_file.name or ''
                if not name:
                    continue

                if Path(name).suffix.lower() == WEBP_EXTENSION:
                    skipped += 1
                    continue

                storage = field_file.storage
                try:
                    with storage.open(name, 'rb') as handle:
                        original_size = handle.size
                        content = convert_to_webp(handle)
                except (OSError, ValueError) as exc:
                    failed += 1
                    self.stdout.write(self.style.ERROR(
                        f'  ! {label}.{field_name}: {name}: {exc}'
                    ))
                    continue

                if content is None:
                    # Not an image Pillow can read; leave the record alone.
                    failed += 1
                    self.stdout.write(self.style.WARNING(
                        f'  ~ {label}.{field_name}: {name}: not a readable image, skipped'
                    ))
                    continue

                new_size = len(content)
                bytes_before += original_size
                bytes_after += new_size
                saved = 1 - (new_size / original_size if original_size else 0)
                self.stdout.write(
                    f'  {label}.{field_name}: {name} -> {Path(webp_name(name)).name}'
                    f'  ({human_size(original_size)} -> {human_size(new_size)},'
                    f' -{saved:.0%})'
                )

                if dry_run:
                    converted += 1
                    continue

                # save=False so FieldFile.save() does not trigger a full model
                # save; the field is written to the database just below.
                field_file.save(f'{Path(name).stem}{WEBP_EXTENSION}', content, save=False)
                instance.save(update_fields=[field_name])

                if not keep_originals:
                    try:
                        storage.delete(name)
                    except OSError as exc:
                        # The conversion already succeeded, so this is a tidiness
                        # problem rather than a failure worth stopping for.
                        self.stdout.write(self.style.WARNING(
                            f'    could not delete the original {name}: {exc}'
                        ))
                converted += 1

        self.stdout.write('')
        verb = 'would convert' if dry_run else 'converted'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {converted} image(s), skipped {skipped} already WebP'
            + (f', {failed} left alone' if failed else '')
        ))
        if bytes_before:
            saved = 1 - (bytes_after / bytes_before)
            self.stdout.write(
                f'{human_size(bytes_before)} -> {human_size(bytes_after)}'
                f'  (-{saved:.0%}, {human_size(bytes_before - bytes_after)} saved)'
            )

        if dry_run:
            self.stdout.write('\nNothing was changed. Re-run without --dry-run to apply.')

        if not keep_originals and not dry_run:
            self.stdout.write(
                '\nNote: originals are deleted. Any file no longer referenced by '
                'the database (left behind by an earlier re-upload) was not '
                'touched by this command.'
            )
