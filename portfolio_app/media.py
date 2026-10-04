"""Media files: which fields store them, and which files nothing references.

The discovery functions here read the models rather than listing field names by
hand. That is a direct response to a real mistake: a hand-written list of "image"
fields omitted ``Profile.resume_en`` and ``resume_de``, so a cleanup check
reported two live resume PDFs as unreferenced. Any hand-written list can fall out
of date the moment a field is added, and the cost of getting it wrong is deleting
content that cannot be recovered from the database.

``all_file_fields`` therefore walks every model in every installed app, so a
field is never missed, including one added by a third-party app that stores into
``MEDIA_ROOT``.
"""

from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.db import models


def human_size(num_bytes):
    """'1.6 MB' — sizes here span four orders of magnitude, so bytes are unhelpful."""
    size = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:,.0f} {unit}' if unit == 'B' else f'{size:,.1f} {unit}'
        size /= 1024


def all_file_fields():
    """Every ``(model, field_name)`` that stores a file, discovered from the models.

    Sorted so reports read consistently. ``FileField`` covers ``ImageField``,
    since the latter is a subclass.
    """
    found = []
    for model in apps.get_models():
        for field in model._meta.get_fields():
            if isinstance(field, models.FileField):
                found.append((model, field.name))
    return sorted(found, key=lambda pair: (pair[0].__name__, pair[1]))


def image_fields():
    """The file fields that store images, e.g. for WebP conversion.

    Derived from :func:`all_file_fields` rather than listed, so adding an image
    field cannot leave it silently unconverted.
    """
    return [
        (model, field_name) for model, field_name in all_file_fields()
        if isinstance(model._meta.get_field(field_name), models.ImageField)
    ]


def referenced_files():
    """Every file name the database points at, across all file fields.

    Returns a set of storage-relative paths, for example ``{'skills/a.webp'}``.
    """
    referenced = set()
    for model, field_name in all_file_fields():
        values = (
            model.objects
            .exclude(**{field_name: ''})
            .exclude(**{f'{field_name}__isnull': True})
            .values_list(field_name, flat=True)
        )
        for value in values:
            # FileField values are strings; a blank one means no file.
            name = str(value or '').strip()
            if name:
                referenced.add(name)
    return referenced


def files_on_disk(root=None):
    """Every regular file under the media root, as storage-relative paths.

    Symlinks are skipped rather than followed: a link could point outside the
    media root, and deleting through it would reach a file this command has no
    business touching.
    """
    root = Path(root or settings.MEDIA_ROOT)
    if not root.exists():
        return set()

    found = set()
    for path in root.rglob('*'):
        if path.is_symlink() or not path.is_file():
            continue
        found.add(str(path.relative_to(root)))
    return found


def find_orphans(root=None):
    """``(orphans, missing)`` for the media root.

    ``orphans`` are files nothing references — usually the original left behind
    when an image was replaced. ``missing`` are references with no file, which is
    a different problem and must not be "fixed" by deleting anything.
    """
    referenced = referenced_files()
    on_disk = files_on_disk(root)
    return sorted(on_disk - referenced), sorted(referenced - on_disk)


def age_in_hours(path, now=None):
    """How long ago ``path`` was last modified, in hours.

    Used to leave very recently written files alone. Django saves the file
    *before* the database row that references it, so for a moment a brand-new
    upload genuinely looks unreferenced. An automated cleanup running in that
    moment would delete a file the site is about to point at.
    """
    import time

    try:
        modified = path.stat().st_mtime
    except OSError:
        # Cannot tell how old it is, so treat it as new and leave it alone.
        return 0.0
    return ((now or time.time()) - modified) / 3600


def upload_grace_hours():
    """The default grace period, from settings.MEDIA_PRUNE_MIN_AGE_HOURS."""
    return int(getattr(settings, 'MEDIA_PRUNE_MIN_AGE_HOURS', 24))
