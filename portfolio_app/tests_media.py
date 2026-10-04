"""Tests for media discovery and the prune_media command.

The most important tests here are the ones about what must *not* be deleted. A
false positive in orphan detection removes content that cannot be recovered from
the database, so the guards are the feature.
"""

from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import models
from django.test import TestCase, override_settings
from PIL import Image

from .media import (
    all_file_fields,
    files_on_disk,
    find_orphans,
    human_size,
    image_fields,
    referenced_files,
)
from .models import Profile, Project, Skill


def png_bytes():
    buffer = BytesIO()
    Image.new('RGB', (20, 20), 'blue').save(buffer, format='PNG')
    return buffer.getvalue()


def upload(name='file.png'):
    return SimpleUploadedFile(name, png_bytes(), content_type='image/png')


class MediaTestCase(TestCase):
    """Every test gets its own media root and a clean set of records."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.media_root = Path(self.tmp.name)
        override = override_settings(MEDIA_ROOT=self.tmp.name)
        override.enable()
        self.addCleanup(override.disable)
        Skill.objects.all().delete()
        Profile.objects.all().delete()

    def write(self, relative, content=b'x'):
        path = self.media_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def run_command(self, *args):
        out = StringIO()
        call_command('prune_media', *args, stdout=out)
        return out.getvalue()


# ---------------------------------------------------------------------------
# Field discovery
# ---------------------------------------------------------------------------
class FieldDiscoveryTests(TestCase):
    def test_every_file_field_is_found_including_resumes(self):
        # The resume fields are the ones a hand-written list missed, which made a
        # cleanup check report two live PDFs as unreferenced.
        found = {(model.__name__, name) for model, name in all_file_fields()}

        self.assertIn(('Profile', 'profile_picture'), found)
        self.assertIn(('Profile', 'resume_en'), found)
        self.assertIn(('Profile', 'resume_de'), found)
        self.assertIn(('Skill', 'image'), found)
        self.assertIn(('Project', 'image'), found)

    def test_discovery_is_not_a_hand_written_list(self):
        # Every declared FileField must appear, discovered from the models.
        declared = set()
        for model in (Profile, Skill, Project):
            for field in model._meta.get_fields():
                if isinstance(field, models.FileField):
                    declared.add((model.__name__, field.name))

        self.assertTrue(declared)
        discovered = {model.__name__ for model, _ in all_file_fields()}
        discovered_pairs = {(model.__name__, name) for model, name in all_file_fields()}
        self.assertTrue(discovered)
        self.assertTrue(declared.issubset(discovered_pairs))

    def test_image_fields_are_a_subset_of_file_fields(self):
        file_fields = set(all_file_fields())
        images = set(image_fields())

        self.assertTrue(images)
        self.assertTrue(images.issubset(file_fields))
        # A PDF is a file but not an image, so it must not be in image_fields.
        self.assertNotIn(('Profile', 'resume_en'), images)


# ---------------------------------------------------------------------------
# Referenced files
# ---------------------------------------------------------------------------
class ReferencedFilesTests(MediaTestCase):
    def test_a_skill_image_is_referenced(self):
        Skill.objects.create(name='Python', start_date='2020-01-01', image=upload('banner.png'))

        names = referenced_files()

        self.assertEqual(len(names), 1)
        self.assertTrue(names.pop().startswith('skills/'))

    def test_a_resume_is_referenced(self):
        # The exact case that a hardcoded image-only list got wrong.
        profile = Profile.objects.create()
        profile.resume_en.save('Resume.pdf', SimpleUploadedFile('Resume.pdf', b'%PDF'), save=True)

        self.assertIn('resumes/Resume.pdf', referenced_files())

    def test_a_missing_field_contributes_nothing(self):
        Skill.objects.create(name='Python', start_date='2020-01-01')

        self.assertEqual(referenced_files(), set())


# ---------------------------------------------------------------------------
# Files on disk
# ---------------------------------------------------------------------------
class FilesOnDiskTests(MediaTestCase):
    def test_every_file_is_found(self):
        self.write('skills/a.webp')
        self.write('nested/deep/b.pdf')

        self.assertEqual(files_on_disk(), {'skills/a.webp', 'nested/deep/b.pdf'})

    def test_a_missing_root_is_empty_not_an_error(self):
        self.assertEqual(files_on_disk(self.media_root / 'nope'), set())

    def test_symlinks_are_skipped(self):
        # A link could point outside the media root, and deleting through it
        # would reach a file this command has no business touching.
        target = self.media_root / 'real.txt'
        target.write_text('x')
        (self.media_root / 'link.txt').symlink_to(target)

        self.assertEqual(files_on_disk(), {'real.txt'})


# ---------------------------------------------------------------------------
# Orphan detection
# ---------------------------------------------------------------------------
class FindOrphansTests(MediaTestCase):
    def test_an_unreferenced_file_is_an_orphan(self):
        self.write('projects/old.jpg')

        orphans, missing = find_orphans()

        self.assertEqual(orphans, ['projects/old.jpg'])
        self.assertEqual(missing, [])

    def test_a_referenced_file_is_not_an_orphan(self):
        skill = Skill.objects.create(name='Python', start_date='2020-01-01', image=upload('banner.png'))
        skill.refresh_from_db()

        orphans, _missing = find_orphans()

        self.assertEqual(orphans, [])

    def test_a_referenced_file_missing_from_disk_is_reported_separately(self):
        skill = Skill.objects.create(name='Python', start_date='2020-01-01', image=upload('banner.png'))
        skill.refresh_from_db()
        (self.media_root / skill.image.name).unlink()

        orphans, missing = find_orphans()

        self.assertEqual(orphans, [])
        self.assertEqual(missing, [skill.image.name])

    def test_a_resume_is_never_an_orphan(self):
        # Regression guard for the mistake that motivated this module.
        profile = Profile.objects.create()
        profile.resume_en.save('Resume.pdf', SimpleUploadedFile('Resume.pdf', b'%PDF'), save=True)
        profile.resume_de.save('Lebenslauf.pdf', SimpleUploadedFile('Lebenslauf.pdf', b'%PDF'), save=True)

        orphans, _missing = find_orphans()

        self.assertEqual(orphans, [])
        self.assertIn('resumes/Resume.pdf', files_on_disk())


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------
class PruneMediaCommandTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        # A real media directory always contains referenced files. One here keeps
        # the "wrong database" guard out of the way of the tests below, which are
        # about orphan handling; the guard has its own tests.
        self.keep = self._referenced_skill_image()

    def _referenced_skill_image(self, name='keep.png'):
        skill = Skill.objects.create(
            name=f'S{name}', start_date='2020-01-01', image=upload(name),
        )
        skill.refresh_from_db()
        return self.media_root / skill.image.name

    def test_it_reports_without_deleting_by_default(self):
        path = self.write('projects/old.jpg')

        out = self.run_command()

        self.assertTrue(path.exists())
        self.assertIn('Nothing was deleted', out)
        self.assertIn('projects/old.jpg', out)

    def test_delete_removes_the_file(self):
        path = self.write('projects/old.jpg')

        out = self.run_command('--delete')

        self.assertFalse(path.exists())
        self.assertIn('Deleted 1 file(s)', out)

    def test_delete_leaves_referenced_files_alone(self):
        orphan = self.write('projects/old.jpg')

        self.run_command('--delete')

        self.assertTrue(self.keep.exists())
        self.assertFalse(orphan.exists())

    def test_a_resume_survives_a_delete_run(self):
        # The end-to-end version of the regression guard.
        profile = Profile.objects.create()
        profile.resume_en.save('Resume.pdf', SimpleUploadedFile('Resume.pdf', b'%PDF'), save=True)
        resume = self.media_root / 'resumes' / 'Resume.pdf'
        self.write('projects/old.jpg')

        self.run_command('--delete')

        self.assertTrue(resume.exists())

    def test_it_says_so_when_there_is_nothing_to_do(self):
        out = self.run_command()

        self.assertIn('No unreferenced files', out)

    def test_it_refuses_to_delete_when_nothing_is_referenced(self):
        # A database with no file references at all means the wrong database or
        # MEDIA_ROOT, not a media directory full of rubbish.
        Skill.objects.all().delete()
        Profile.objects.all().delete()
        self.write('projects/old.jpg')
        self.write('skills/another.webp')

        with self.assertRaises(CommandError) as caught:
            self.run_command('--delete')

        self.assertIn('Refusing to delete', str(caught.exception))
        self.assertTrue((self.media_root / 'projects' / 'old.jpg').exists())

    def test_it_warns_but_still_reports_when_nothing_is_referenced(self):
        Skill.objects.all().delete()
        Profile.objects.all().delete()
        self.write('projects/old.jpg')

        out = self.run_command()

        self.assertIn('wrong database or MEDIA_ROOT', out)
        self.assertIn('projects/old.jpg', out)

    def test_missing_references_are_reported(self):
        self.keep.unlink()

        out = self.run_command()

        self.assertIn('referenced but missing on disk', out)

    def test_a_nonexistent_media_root_is_an_error(self):
        self.tmp.cleanup()
        # The directory is gone; the command should say so rather than crash.
        with self.assertRaises(CommandError):
            self.run_command()

    def test_empty_directories_are_only_removed_when_asked(self):
        path = self.write('projects/old.jpg')
        directory = path.parent

        self.run_command('--delete')

        self.assertTrue(directory.exists())

    def test_prune_empty_dirs_removes_the_leftover_directory(self):
        path = self.write('projects/old.jpg')
        directory = path.parent

        self.run_command('--delete', '--prune-empty-dirs')

        self.assertFalse(directory.exists())

    def test_prune_empty_dirs_keeps_a_directory_still_in_use(self):
        self.write('skills/old-orphan.webp')

        self.run_command('--delete', '--prune-empty-dirs')

        self.assertTrue((self.media_root / 'skills').exists())

    def test_it_reports_the_saving(self):
        self.write('projects/old.jpg', b'x' * 2048)

        out = self.run_command('--delete')

        self.assertIn('2.0 KB', out)


class HumanSizeTests(TestCase):
    def test_bytes_kilobytes_and_megabytes(self):
        self.assertEqual(human_size(512), '512 B')
        self.assertEqual(human_size(2048), '2.0 KB')
        self.assertEqual(human_size(5 * 1024 * 1024), '5.0 MB')
