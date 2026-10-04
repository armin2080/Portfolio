"""Tests for WebP conversion, on upload and for images already stored.

The conversion is deliberately pure — no database — so most of this exercises it
directly, and the rest checks that the models and the command wire it up.
"""

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from .images import (
    MAX_DIMENSION,
    convert_to_webp,
    convert_uploaded_image_to_webp,
    is_image_name,
    webp_name,
)
from .media import image_fields
from .models import Profile, Project, Skill


def image_bytes(width=100, height=100, mode='RGB', fmt='PNG', color=None):
    """An in-memory image, for uploading without touching the filesystem."""
    buffer = BytesIO()
    fill = color or ('red' if mode == 'RGB' else (255, 0, 0, 128))
    Image.new(mode, (width, height), fill).save(buffer, format=fmt)
    return buffer.getvalue()


def upload(name='test.png', **kwargs):
    return SimpleUploadedFile(name, image_bytes(**kwargs), content_type='image/png')


def open_result(content):
    return Image.open(BytesIO(content.read()))


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------
class WebPNameTests(TestCase):
    def test_extension_is_replaced_and_the_folder_kept(self):
        self.assertEqual(webp_name('skills/banner.png'), 'skills/banner.webp')
        self.assertEqual(webp_name('a/b/c/photo.JPEG'), 'a/b/c/photo.webp')

    def test_a_name_without_an_extension_gains_one(self):
        self.assertEqual(webp_name('banner'), 'banner.webp')

    def test_a_nameless_path_still_gets_a_name(self):
        self.assertEqual(webp_name(''), 'image.webp')

    def test_image_name_detection(self):
        self.assertTrue(is_image_name('a/b.png'))
        self.assertTrue(is_image_name('photo.JPEG'))
        self.assertFalse(is_image_name('cv.pdf'))
        self.assertFalse(is_image_name(''))


# ---------------------------------------------------------------------------
# The conversion itself
# ---------------------------------------------------------------------------
class ConvertToWebPTests(TestCase):
    def test_the_result_is_webp(self):
        content = convert_to_webp(upload())

        self.assertIsNotNone(content)
        with open_result(content) as image:
            self.assertEqual(image.format, 'WEBP')

    def test_the_result_keeps_the_dimensions_of_a_small_image(self):
        # thumbnail() must only ever shrink, never enlarge.
        content = convert_to_webp(upload(width=200, height=120))

        with open_result(content) as image:
            self.assertEqual((image.width, image.height), (200, 120))

    def test_an_oversized_image_is_shrunk_and_keeps_its_ratio(self):
        content = convert_to_webp(upload(width=4000, height=1000))

        with open_result(content) as image:
            self.assertEqual(image.width, MAX_DIMENSION)
            # 4:1 stays 4:1 rather than being squashed into a square.
            self.assertEqual(image.height, MAX_DIMENSION // 4)

    def test_the_longest_edge_is_what_is_limited(self):
        # A portrait image must be limited by its height, not its width.
        content = convert_to_webp(upload(width=500, height=3000))

        with open_result(content) as image:
            self.assertEqual(image.height, MAX_DIMENSION)
            self.assertLess(image.width, MAX_DIMENSION)

    def test_transparency_survives(self):
        content = convert_to_webp(upload(mode='RGBA', fmt='PNG'))

        with open_result(content) as image:
            self.assertIn(image.mode, ('RGBA', 'LA'))
            self.assertEqual(image.getpixel((0, 0))[3], 128)

    def test_an_opaque_image_is_converted_to_rgb(self):
        # Storing a photo as RGBA would waste a quarter of the bytes on an alpha
        # channel that is not used.
        content = convert_to_webp(upload(mode='RGB'))

        with open_result(content) as image:
            self.assertEqual(image.mode, 'RGB')

    def test_a_jpeg_is_accepted(self):
        content = convert_to_webp(upload(name='photo.jpg', fmt='JPEG'))

        self.assertIsNotNone(content)
        with open_result(content) as image:
            self.assertEqual(image.format, 'WEBP')

    def test_webp_output_is_smaller_than_the_png_input(self):
        raw = image_bytes(width=800, height=600, fmt='PNG')
        content = convert_to_webp(SimpleUploadedFile('big.png', raw))

        self.assertLess(len(content), len(raw))

    def test_a_file_that_is_not_an_image_is_left_alone(self):
        # A user mistake, not a crash: the caller keeps the original.
        content = convert_to_webp(
            SimpleUploadedFile('notes.txt', b'this is not an image')
        )

        self.assertIsNone(content)

    def test_an_empty_file_is_left_alone(self):
        self.assertIsNone(convert_to_webp(SimpleUploadedFile('empty.png', b'')))

    def test_the_name_becomes_webp(self):
        content = convert_to_webp(upload(name='banner.png'))

        self.assertEqual(content.name, 'banner.webp')

    def test_the_source_is_rewound_first(self):
        # A file read once already must still convert, since callers hand over
        # handles they may have touched.
        uploaded = upload()
        uploaded.read()

        self.assertIsNotNone(convert_to_webp(uploaded))


# ---------------------------------------------------------------------------
# Converting one field on an instance
# ---------------------------------------------------------------------------
class ConvertUploadedImageTests(TestCase):
    def setUp(self):
        Skill.objects.all().delete()
        self.skill = Skill(name='Python', start_date='2020-01-01')

    def test_an_uploaded_file_is_converted_in_place(self):
        self.skill.image = upload(name='banner.png')

        self.assertTrue(convert_uploaded_image_to_webp(self.skill, 'image'))
        self.assertEqual(Path(self.skill.image.name).suffix, '.webp')

    def test_an_already_webp_upload_is_left_alone(self):
        self.skill.image = upload(name='banner.webp')

        self.assertFalse(convert_uploaded_image_to_webp(self.skill, 'image'))

    def test_an_empty_field_is_left_alone(self):
        self.assertFalse(convert_uploaded_image_to_webp(self.skill, 'image'))

    def test_a_stored_file_is_not_converted_again(self):
        # _committed is True once a file has been written, so re-saving an
        # instance must not re-encode it or create a second file.
        self.skill.image = upload()
        convert_uploaded_image_to_webp(self.skill, 'image')

        self.assertFalse(convert_uploaded_image_to_webp(self.skill, 'image'))

    def test_an_unreadable_file_is_kept_as_uploaded(self):
        self.skill.image = SimpleUploadedFile('broken.png', b'nope')

        self.assertFalse(convert_uploaded_image_to_webp(self.skill, 'image'))
        self.assertEqual(Path(self.skill.image.name).suffix, '.png')


# ---------------------------------------------------------------------------
# The save hooks
# ---------------------------------------------------------------------------
class ModelUploadConversionTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        override = override_settings(MEDIA_ROOT=self.tmp.name)
        override.enable()
        self.addCleanup(override.disable)
        Skill.objects.all().delete()

    def test_a_skill_image_is_converted_on_save(self):
        skill = Skill.objects.create(
            name='Python', start_date='2020-01-01', image=upload(name='banner.png'),
        )

        skill.refresh_from_db()
        self.assertEqual(Path(skill.image.name).suffix, '.webp')
        self.assertTrue(Path(skill.image.path).exists())

    def test_a_project_image_is_converted_on_save(self):
        project = Project.objects.create(
            name='P', link='https://example.invalid', image=upload(name='shot.png'),
        )

        project.refresh_from_db()
        self.assertEqual(Path(project.image.name).suffix, '.webp')

    def test_a_profile_picture_is_converted_on_save(self):
        profile = Profile.objects.create(profile_picture=upload(name='me.png'))

        profile.refresh_from_db()
        self.assertEqual(Path(profile.profile_picture.name).suffix, '.webp')

    def test_the_singleton_rule_still_applies(self):
        Profile.objects.create()
        with self.assertRaises(ValueError):
            Profile.objects.create()

    def test_saving_with_update_fields_does_not_orphan_the_converted_file(self):
        # A caller passing update_fields must still get the new name written,
        # or the file would sit on disk unreferenced.
        skill = Skill.objects.create(name='Python', start_date='2020-01-01')
        skill.image = upload(name='later.png')
        skill.save(update_fields=['image'])

        skill.refresh_from_db()
        self.assertEqual(Path(skill.image.name).suffix, '.webp')
        self.assertTrue(Path(skill.image.path).exists())

    def test_an_ordinary_save_does_not_re_encode(self):
        skill = Skill.objects.create(
            name='Python', start_date='2020-01-01', image=upload(name='banner.png'),
        )
        skill.refresh_from_db()
        first_name = skill.image.name

        skill.name = 'Python 3'
        skill.save()
        skill.refresh_from_db()

        self.assertEqual(skill.image.name, first_name)

    def test_saving_without_an_image_is_fine(self):
        skill = Skill.objects.create(name='Noimage', start_date='2020-01-01')

        self.assertFalse(skill.image)


# ---------------------------------------------------------------------------
# The management command
# ---------------------------------------------------------------------------
class ConvertImagesCommandTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        override = override_settings(MEDIA_ROOT=self.tmp.name)
        override.enable()
        self.addCleanup(override.disable)
        Skill.objects.all().delete()

    def _existing_file(self, field_name='image'):
        """A skill whose image is already stored as a PNG.

        Written through the storage layer directly: creating it via the model
        would convert it, which is the behaviour this command exists for.
        """
        skill = Skill.objects.create(name='Python', start_date='2020-01-01')
        skill.image.save(
            'legacy.png',
            SimpleUploadedFile('legacy.png', image_bytes(width=3000, height=1000)),
            save=False,
        )
        skill.save(update_fields=['image'])
        return skill

    def test_an_existing_png_is_converted_and_the_record_updated(self):
        skill = self._existing_file()

        self._run()

        skill.refresh_from_db()
        self.assertEqual(Path(skill.image.name).suffix, '.webp')
        self.assertTrue(Path(skill.image.path).exists())

    def test_the_original_is_deleted(self):
        skill = self._existing_file()
        original = Path(self.tmp.name) / 'skills' / 'legacy.png'
        self.assertTrue(original.exists())

        self._run()

        self.assertFalse(original.exists())

    def test_keep_originals_leaves_the_file_behind(self):
        self._existing_file()

        self._run('--keep-originals')

        self.assertTrue((Path(self.tmp.name) / 'skills' / 'legacy.png').exists())

    def test_the_converted_image_is_shrunk(self):
        skill = self._existing_file()

        self._run()

        skill.refresh_from_db()
        with Image.open(skill.image.path) as image:
            self.assertEqual(image.width, MAX_DIMENSION)

    def test_a_dry_run_changes_nothing(self):
        skill = self._existing_file()

        self._run('--dry-run')

        skill.refresh_from_db()
        self.assertEqual(Path(skill.image.name).suffix, '.png')
        self.assertTrue((Path(self.tmp.name) / 'skills' / 'legacy.png').exists())

    def test_a_dry_run_still_reports_the_saving(self):
        self._existing_file()

        out = self._run('--dry-run')

        self.assertIn('would convert 1 image(s)', out)
        self.assertIn('Nothing was changed', out)

    def test_a_file_already_in_webp_is_skipped(self):
        skill = Skill.objects.create(
            name='Python', start_date='2020-01-01', image=upload(name='already.png'),
        )
        skill.refresh_from_db()
        self.assertEqual(Path(skill.image.name).suffix, '.webp')

        out = self._run()

        self.assertIn('skipped 1 already WebP', out)
        self.assertIn('converted 0 image(s)', out)

    def test_running_twice_is_harmless(self):
        skill = self._existing_file()

        self._run()
        skill.refresh_from_db()
        after_first = skill.image.name

        self._run()
        skill.refresh_from_db()

        self.assertEqual(skill.image.name, after_first)

    def test_an_unreadable_file_is_reported_and_left_alone(self):
        skill = Skill.objects.create(name='Python', start_date='2020-01-01')
        skill.image.save(
            'broken.png', SimpleUploadedFile('broken.png', b'not an image'), save=False,
        )
        skill.save(update_fields=['image'])

        out = self._run()

        skill.refresh_from_db()
        self.assertEqual(Path(skill.image.name).suffix, '.png')
        self.assertIn('left alone', out)

    def test_every_image_field_is_covered(self):
        # The command walks the registry, so a new image field must appear there.
        fields = {(model.__name__, field) for model, field in image_fields()}
        self.assertEqual(
            fields,
            {('Profile', 'profile_picture'), ('Skill', 'image'), ('Project', 'image')},
        )

    def _run(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command('convert_images_to_webp', *args, stdout=out)
        return out.getvalue()
