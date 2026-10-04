"""Convert uploaded images to WebP, and shrink them to a sensible size.

Why this exists
---------------
The skill banners were uploaded as 2172x724 PNGs of about 1.6 MB each. Twelve of
those is roughly 19 MB of images on a single page, which is why the skills page
crawled. They are displayed at most 475 CSS pixels wide, so they were also about
4.6x larger than a 3x screen can even show.

Two separate wins, and the second is the bigger one:

* **Format.** WebP is typically 25-35% smaller than JPEG at equal quality, and
  far smaller than PNG for anything photographic.
* **Size.** Shrinking to at most ``MAX_DIMENSION`` on the longest edge. Resizing
  a 2172px image to 1600px alone removes about half the pixels.

Together these take the banners from ~1.6 MB to roughly 60 KB, a ~96% cut.

This module holds no database access, so it can be tested without one. The
management command does the model walking.
"""

import logging
from io import BytesIO
from pathlib import Path

from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

WEBP_EXTENSION = '.webp'

# Quality 82 is the usual "visually indistinguishable from the original" point
# for WebP; the last few points of quality cost a lot of bytes.
WEBP_QUALITY = 82

# The longest edge, in pixels. The widest this site ever displays an image is
# the 475px skill banner, so 1600 still covers a 3x screen with room to spare.
# Anything larger is bytes nobody can see.
MAX_DIMENSION = 1600

# WebP's slowest compression is only about 1-2% smaller than this, for several
# times the CPU. That matters on the Raspberry Pi that runs the site.
WEBP_METHOD = 4

# Formats Pillow can open that we expect to receive. Used only for reporting, so
# an unexpected format is reported rather than silently ignored.
EXPECTED_SUFFIXES = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tif', '.tiff', '.webp'}


def is_image_name(name):
    return Path(name or '').suffix.lower() in EXPECTED_SUFFIXES


def webp_name(name):
    """'skills/banner.png' -> 'skills/banner.webp', keeping the folder."""
    path = Path(name or '')
    if not path.name:
        # PurePath('') has no name and with_suffix() would raise; a nameless
        # file still needs something to be stored under.
        return f'image{WEBP_EXTENSION}'
    return str(path.with_suffix(WEBP_EXTENSION))


def _prepare(source):
    """A correctly oriented, WebP-friendly copy of the image.

    ``exif_transpose`` applies the orientation tag, so a photo taken on a phone
    is not stored sideways — WebP output has no orientation tag to read.

    Alpha is preserved when present, because WebP supports it; a flat RGB
    conversion would otherwise turn transparency black. Photos are converted to
    RGB because storing them as RGBA wastes roughly a quarter of the bytes on an
    alpha channel they do not use.
    """
    image = ImageOps.exif_transpose(source) or source
    has_alpha = image.mode in ('RGBA', 'LA') or (
        image.mode == 'P' and 'transparency' in image.info
    )
    return image.convert('RGBA' if has_alpha else 'RGB')


def convert_to_webp(uploaded_file, *, quality=WEBP_QUALITY,
                    max_dimension=MAX_DIMENSION, method=WEBP_METHOD):
    """WebP version of ``uploaded_file``, or ``None`` if it is not an image.

    Returns a :class:`~django.core.files.base.ContentFile` ready to store. It
    never raises on unusable input: an upload that Pillow cannot read is a user
    mistake, not a crash, and the caller can keep the original.

    Note that EXIF data is deliberately not carried over. That drops the
    camera metadata — including any GPS coordinates — from images uploaded from
    a phone.
    """
    try:
        uploaded_file.seek(0)
    except (AttributeError, OSError):
        pass

    try:
        with Image.open(uploaded_file) as source:
            source.load()
            image = _prepare(source)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.info(
            'Not converting %r to WebP: %s', getattr(uploaded_file, 'name', '?'), exc,
        )
        return None

    # thumbnail() only ever shrinks, and keeps the aspect ratio.
    image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

    buffer = BytesIO()
    image.save(buffer, format='WEBP', quality=quality, method=method)
    return ContentFile(buffer.getvalue(), name=webp_name(getattr(uploaded_file, 'name', 'image')))


def convert_uploaded_image_to_webp(instance, field_name, **options):
    """Convert a *just-uploaded* image on ``instance`` to WebP, in place.

    Returns ``True`` when a conversion happened. Deliberately does nothing unless
    the field holds an unsaved upload: re-saving an instance must not re-encode
    an image that is already stored, which would both waste CPU and create a new
    file on every save.

    An unreadable file is left exactly as uploaded, so a quirky image is stored
    rather than rejected.
    """
    field_file = getattr(instance, field_name, None)
    if not field_file or getattr(field_file, '_committed', True):
        return False

    name = getattr(field_file, 'name', '') or ''
    if Path(name).suffix.lower() == WEBP_EXTENSION:
        return False

    converted = convert_to_webp(field_file, **options)
    if converted is None:
        return False

    # Pass only the file name: FieldFile.save() re-applies the field's
    # upload_to, so including the folder here would nest it twice.
    field_file.save(f'{Path(name).stem}{WEBP_EXTENSION}', converted, save=False)
    return True


def image_fields():
    """Every ``(model, field_name)`` the site stores an image in.

    Imported inside the function to avoid a circular import, because the models
    import this module for their save hooks.
    """
    from .models import Profile, Project, Skill

    return [
        (Profile, 'profile_picture'),
        (Skill, 'image'),
        (Project, 'image'),
    ]
