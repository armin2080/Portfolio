"""Tests for the GitHub project sync.

The HTTP layer is stubbed rather than mocked per-call, so these exercise the real
parsing, filtering and matching logic without touching the network.
"""

import json
from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase, override_settings

from .github_sync import (
    SyncReport,
    fetch_public_repos,
    humanize_repo_name,
    is_syncable,
    project_defaults_from_repo,
    refresh_project_from_github,
    sync_projects,
)
from .models import Project


def make_repo(**overrides):
    """A GitHub API repository payload with sensible defaults."""
    repo = {
        'id': 1001,
        'name': 'vocab-buddy',
        'full_name': 'armin2080/vocab-buddy',
        'description': 'An AI-assisted German learning workspace.',
        'html_url': 'https://github.com/armin2080/vocab-buddy',
        'homepage': '',
        'fork': False,
        'archived': False,
        'disabled': False,
        'private': False,
        'owner': {'login': 'armin2080'},
        'created_at': '2025-01-01T10:00:00Z',
        'pushed_at': '2025-10-01T12:30:00Z',
    }
    repo.update(overrides)
    return repo


class FakeResponse:
    def __init__(self, payload, headers=None):
        self._body = json.dumps(payload).encode()
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def urlopen_returning(payload, headers=None):
    return patch(
        'portfolio_app.github_sync.urllib.request.urlopen',
        return_value=FakeResponse(payload, headers),
    )


# ---------------------------------------------------------------------------
# Repo name prettifying
# ---------------------------------------------------------------------------
class HumanizeRepoNameTests(TestCase):
    def test_separators_become_spaces(self):
        self.assertEqual(
            humanize_repo_name('Bayesian-MCMC-Insurance-Regression'),
            'Bayesian MCMC Insurance Regression',
        )
        self.assertEqual(humanize_repo_name('smart_cover_letter_agent'), 'Smart Cover Letter Agent')

    def test_acronyms_survive(self):
        # Title-casing unconditionally would turn these into Mcmc / Ai / Api.
        self.assertEqual(humanize_repo_name('MCMC-sampler'), 'MCMC Sampler')
        self.assertEqual(humanize_repo_name('AI-tools'), 'AI Tools')

    def test_lowercase_names_are_title_cased(self):
        self.assertEqual(humanize_repo_name('vocab-buddy'), 'Vocab Buddy')

    def test_camel_case_is_left_alone(self):
        self.assertEqual(humanize_repo_name('VocabBuddy'), 'VocabBuddy')

    def test_dots_are_treated_as_separators(self):
        self.assertEqual(humanize_repo_name('armin2080.github.io'), 'Armin2080 Github Io')

    def test_empty_name_is_returned_unchanged(self):
        self.assertEqual(humanize_repo_name(''), '')


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------
class SyncableRepoTests(TestCase):
    def test_plain_repo_is_syncable(self):
        self.assertTrue(is_syncable(make_repo(), 'armin2080'))

    def test_forks_archived_and_disabled_are_skipped(self):
        for flag in ('fork', 'archived', 'disabled'):
            with self.subTest(flag=flag):
                self.assertFalse(is_syncable(make_repo(**{flag: True}), 'armin2080'))

    def test_private_repos_are_skipped(self):
        self.assertFalse(is_syncable(make_repo(private=True), 'armin2080'))

    def test_repos_owned_by_someone_else_are_skipped(self):
        self.assertFalse(is_syncable(make_repo(owner={'login': 'someone-else'}), 'armin2080'))

    def test_owner_match_is_case_insensitive(self):
        self.assertTrue(is_syncable(make_repo(owner={'login': 'Armin2080'}), 'armin2080'))


# ---------------------------------------------------------------------------
# Field mapping
# ---------------------------------------------------------------------------
class ProjectDefaultsTests(TestCase):
    def test_maps_repo_fields_to_project_fields(self):
        fields = project_defaults_from_repo(make_repo())
        self.assertEqual(fields['name'], 'Vocab Buddy')
        self.assertEqual(fields['description'], 'An AI-assisted German learning workspace.')
        self.assertEqual(fields['link'], 'https://github.com/armin2080/vocab-buddy')
        self.assertEqual(fields['github_repo_id'], 1001)
        self.assertEqual(fields['github_full_name'], 'armin2080/vocab-buddy')

    def test_prefers_homepage_as_the_project_link(self):
        fields = project_defaults_from_repo(make_repo(homepage='https://vocabbuddy.example'))
        self.assertEqual(fields['link'], 'https://vocabbuddy.example')

    def test_date_uses_last_push(self):
        fields = project_defaults_from_repo(make_repo())
        self.assertEqual(fields['date'], date(2025, 10, 1))

    def test_date_falls_back_to_creation_when_never_pushed(self):
        fields = project_defaults_from_repo(make_repo(pushed_at=None))
        self.assertEqual(fields['date'], date(2025, 1, 1))

    def test_missing_description_becomes_blank_not_invented(self):
        fields = project_defaults_from_repo(make_repo(description=None))
        self.assertEqual(fields['description'], '')

    def test_tolerates_an_unparseable_timestamp(self):
        fields = project_defaults_from_repo(make_repo(pushed_at='not-a-date', created_at='also-bad'))
        self.assertIsNotNone(fields['date'])


# ---------------------------------------------------------------------------
# API layer
# ---------------------------------------------------------------------------
@override_settings(GITHUB_USERNAME='armin2080')
class FetchReposTests(TestCase):
    def test_returns_the_repository_list(self):
        with urlopen_returning([make_repo()]):
            self.assertEqual(len(fetch_public_repos('armin2080')), 1)

    def test_follows_the_next_page_link(self):
        link = '<https://api.github.com/users/armin2080/repos?page=2>; rel="next"'
        first = FakeResponse([make_repo(id=1)], {'Link': link})
        second = FakeResponse([make_repo(id=2, name='second')])
        with patch('portfolio_app.github_sync.urllib.request.urlopen', side_effect=[first, second]):
            repos = fetch_public_repos('armin2080')
        self.assertEqual([r['id'] for r in repos], [1, 2])

    def test_stops_when_there_is_no_next_page(self):
        with urlopen_returning([make_repo()]):
            self.assertEqual(len(fetch_public_repos('armin2080')), 1)

    def test_unexpected_payload_is_an_error_not_a_silent_empty_result(self):
        with urlopen_returning({'message': 'Not Found'}):
            with self.assertRaises(ValueError):
                fetch_public_repos('nobody')

    def test_sends_a_user_agent_and_token(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured['headers'] = request.headers
            return FakeResponse([make_repo()])

        with patch('portfolio_app.github_sync.urllib.request.urlopen', side_effect=fake_urlopen):
            fetch_public_repos('armin2080', token='secret-token')

        # urllib capitalises header names.
        self.assertIn('User-agent', captured['headers'])
        self.assertEqual(captured['headers'].get('Authorization'), 'Bearer secret-token')


# ---------------------------------------------------------------------------
# Sync behaviour
# ---------------------------------------------------------------------------
@override_settings(GITHUB_USERNAME='armin2080')
class SyncProjectsTests(TestCase):
    def test_creates_a_project_for_a_new_repo(self):
        with urlopen_returning([make_repo()]):
            report = sync_projects()

        self.assertEqual(len(report.created), 1)
        project = Project.objects.get()
        self.assertEqual(project.name, 'Vocab Buddy')
        self.assertEqual(project.github_repo_id, 1001)
        self.assertTrue(project.is_published)
        self.assertFalse(project.image)

    def test_skips_forks_and_archived(self):
        with urlopen_returning([make_repo(fork=True), make_repo(id=2, archived=True)]):
            report = sync_projects()

        self.assertEqual(Project.objects.count(), 0)
        self.assertEqual(len(report.skipped), 2)

    def test_second_run_updates_instead_of_duplicating(self):
        with urlopen_returning([make_repo()]):
            sync_projects()
        with urlopen_returning([make_repo()]):
            report = sync_projects()

        self.assertEqual(Project.objects.count(), 1)
        self.assertEqual(len(report.created), 0)
        self.assertEqual(len(report.updated), 1)

    def test_admin_edits_are_never_overwritten(self):
        # The whole point: importing is one-way. Customise a project and it must
        # survive every later sync.
        with urlopen_returning([make_repo()]):
            sync_projects()

        project = Project.objects.get()
        project.name = 'My Custom Name'
        project.description = 'My custom description.'
        project.link = 'https://example.com/custom'
        project.is_published = False
        project.save()

        with urlopen_returning([make_repo(description='Changed on GitHub')]):
            sync_projects()

        project.refresh_from_db()
        self.assertEqual(project.name, 'My Custom Name')
        self.assertEqual(project.description, 'My custom description.')
        self.assertEqual(project.link, 'https://example.com/custom')

    def test_hiding_a_project_survives_a_sync(self):
        # Unpublishing is how a project is removed from the site; a sync must not
        # quietly bring it back.
        with urlopen_returning([make_repo()]):
            sync_projects()
        Project.objects.update(is_published=False)

        with urlopen_returning([make_repo()]):
            sync_projects()

        self.assertFalse(Project.objects.get().is_published)

    def test_a_repo_renamed_on_github_is_not_duplicated(self):
        # Matching is on the numeric id, which survives renames.
        with urlopen_returning([make_repo()]):
            sync_projects()
        with urlopen_returning([make_repo(name='vocab-buddy-2', full_name='armin2080/vocab-buddy-2')]):
            sync_projects()

        self.assertEqual(Project.objects.count(), 1)
        self.assertEqual(Project.objects.get().github_full_name, 'armin2080/vocab-buddy-2')

    def test_a_repo_that_disappears_is_left_alone(self):
        # Never delete: an empty API result must not wipe the portfolio.
        with urlopen_returning([make_repo()]):
            sync_projects()
        with urlopen_returning([]):
            report = sync_projects()

        self.assertEqual(Project.objects.count(), 1)
        self.assertEqual(report.fetched, 0)

    def test_manual_projects_are_untouched(self):
        manual = Project.objects.create(name='Handmade', link='https://example.com')
        with urlopen_returning([make_repo()]):
            sync_projects()

        manual.refresh_from_db()
        self.assertIsNone(manual.github_repo_id)
        self.assertEqual(Project.objects.count(), 2)

    def test_one_broken_repo_does_not_stop_the_rest(self):
        broken = make_repo(id=None, name='broken')
        with urlopen_returning([broken, make_repo(id=2002, name='good-one')]):
            report = sync_projects()

        self.assertEqual(Project.objects.count(), 1)
        self.assertEqual(len(report.errors), 1)
        self.assertFalse(report.ok)

    def test_dry_run_writes_nothing(self):
        with urlopen_returning([make_repo()]):
            report = sync_projects(dry_run=True)

        self.assertEqual(Project.objects.count(), 0)
        self.assertEqual(len(report.created), 1)
        self.assertIn('would create', report.created[0])

    def test_missing_username_is_a_clear_error(self):
        with override_settings(GITHUB_USERNAME=''):
            with self.assertRaises(ValueError):
                sync_projects()

    def test_summary_mentions_the_counts(self):
        with urlopen_returning([make_repo()]):
            report = sync_projects()
        self.assertIn('1 created', report.summary())

    def test_report_ok_reflects_errors(self):
        self.assertTrue(SyncReport().ok)
        self.assertFalse(SyncReport(errors=['x']).ok)


# ---------------------------------------------------------------------------
# Manual refresh
# ---------------------------------------------------------------------------
@override_settings(GITHUB_USERNAME='armin2080')
class RefreshProjectTests(TestCase):
    def test_overwrites_the_github_fields(self):
        project = Project.objects.create(
            name='Old', link='https://old.example', github_repo_id=1001,
        )
        with urlopen_returning(make_repo(homepage='https://new.example')):
            refresh_project_from_github(project)

        project.refresh_from_db()
        self.assertEqual(project.name, 'Vocab Buddy')
        self.assertEqual(project.link, 'https://new.example')
        self.assertIsNotNone(project.github_synced_at)

    def test_leaves_photo_category_and_skills_alone(self):
        project = Project.objects.create(name='Old', link='https://old.example', github_repo_id=1001)
        project.image = 'projects/screenshot.png'
        project.save()

        with urlopen_returning(make_repo()):
            refresh_project_from_github(project)

        project.refresh_from_db()
        self.assertEqual(project.image, 'projects/screenshot.png')

    def test_refuses_a_project_without_a_github_link(self):
        project = Project.objects.create(name='Manual', link='https://example.com')
        with self.assertRaises(ValueError):
            refresh_project_from_github(project)


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------
@override_settings(GITHUB_USERNAME='armin2080')
class SyncCommandTests(TestCase):
    def test_creates_projects_and_reports(self):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        with urlopen_returning([make_repo()]):
            call_command('sync_github_projects', stdout=out)

        self.assertEqual(Project.objects.count(), 1)
        self.assertIn('1 created', out.getvalue())

    def test_dry_run_flag_is_honoured(self):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        with urlopen_returning([make_repo()]):
            call_command('sync_github_projects', '--dry-run', stdout=out)

        self.assertEqual(Project.objects.count(), 0)
        self.assertIn('Dry run', out.getvalue())

    def test_api_failure_raises_a_command_error(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        import urllib.error

        error = urllib.error.URLError('no network')
        with patch('portfolio_app.github_sync.urllib.request.urlopen', side_effect=error):
            with self.assertRaises(CommandError):
                call_command('sync_github_projects')

    def test_missing_username_raises_a_command_error(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with override_settings(GITHUB_USERNAME=''):
            with self.assertRaises(CommandError):
                call_command('sync_github_projects')


# ---------------------------------------------------------------------------
# Public visibility
# ---------------------------------------------------------------------------
class UnpublishedProjectTests(TestCase):
    def setUp(self):
        self.visible = Project.objects.create(
            name='Visible Project', link='https://example.com', is_published=True,
        )
        self.hidden = Project.objects.create(
            name='Hidden Project', link='https://example.com', is_published=False,
        )

    def test_hidden_projects_are_absent_from_the_projects_page(self):
        response = self.client.get('/projects/')
        self.assertContains(response, 'Visible Project')
        self.assertNotContains(response, 'Hidden Project')

    def test_hidden_projects_are_absent_from_the_homepage(self):
        response = self.client.get('/')
        self.assertContains(response, 'Visible Project')
        self.assertNotContains(response, 'Hidden Project')

    def test_a_project_without_a_description_renders_without_a_gap(self):
        Project.objects.create(name='No Description', link='https://example.com')
        response = self.client.get('/projects/')
        self.assertContains(response, 'No Description')
