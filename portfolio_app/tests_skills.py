"""Tests for automatic skill detection and tagging.

The GitHub HTTP layer is stubbed, so the real matching, parsing and tagging logic
runs without touching the network. Detection is deliberately deterministic, which
makes it fully testable — that is the main reason there is no guessing involved.
"""

import base64
import json
from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from .github_sync import apply_skill_dates, sync_projects
from .models import Project, Skill, SkillSignal
from .skill_detection import (
    RepoEvidence,
    dependency_matches,
    detect_skill_names,
    fetch_evidence,
    normalise_package_name,
    parse_dependency_file,
    parse_environment_yml,
    parse_package_json,
    parse_pipfile,
    parse_pyproject,
    parse_requirements,
    path_matches,
    signal_matches,
)


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


def github_router(repos=None, tree_paths=(), manifests=None, calls=None,
                  fail_tree=False, fail_contents=False):
    """Answer the GitHub endpoints detection and the sync use.

    ``calls`` collects the URLs requested, which lets a test assert that an
    unchanged repository costs no API requests at all.
    """
    manifests = manifests or {}

    def responder(request, *args, **kwargs):
        url = request.full_url
        if calls is not None:
            calls.append(url)
        if '/users/' in url:
            return FakeResponse(repos or [])
        if '/git/trees/' in url:
            # Failing the tree is how an unreadable repository is simulated: an
            # empty tree is a *successful* read that genuinely means "nothing".
            if fail_tree:
                raise OSError('network died')
            return FakeResponse({
                'tree': [{'path': path, 'type': 'blob'} for path in tree_paths],
                'truncated': False,
            })
        if '/contents/' in url:
            if fail_contents:
                raise OSError('network died')
            path = url.split('/contents/', 1)[1].split('?')[0]
            body = base64.b64encode(manifests.get(path, '').encode()).decode()
            return FakeResponse({'content': body})
        raise AssertionError(f'unexpected URL {url}')

    return responder


def make_repo(**overrides):
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
        'language': 'Python',
        'default_branch': 'main',
        'created_at': '2025-01-01T10:00:00Z',
        'pushed_at': '2025-10-01T12:30:00Z',
    }
    repo.update(overrides)
    return repo


def signal(kind, pattern, skill_name):
    return SkillSignal.objects.create(
        kind=kind, pattern=pattern, skill_name=skill_name,
    )


# ---------------------------------------------------------------------------
# Package names
# ---------------------------------------------------------------------------
class NormalisePackageNameTests(TestCase):
    def test_strips_version_extras_and_markers(self):
        self.assertEqual(normalise_package_name('Scikit_Learn[alldeps]>=1.0'), 'scikit-learn')
        self.assertEqual(normalise_package_name('Django==5.2'), 'django')
        self.assertEqual(normalise_package_name('psycopg2-binary ; extra == "dev"'), 'psycopg2-binary')

    def test_underscores_become_hyphens(self):
        self.assertEqual(normalise_package_name('opencv_python'), 'opencv-python')

    def test_blank_input_is_blank(self):
        self.assertEqual(normalise_package_name('   '), '')


# ---------------------------------------------------------------------------
# Dependency file parsing
# ---------------------------------------------------------------------------
class DependencyParsingTests(TestCase):
    def test_requirements_skips_comments_options_and_editable_installs(self):
        parsed = parse_requirements(
            '# a comment\n'
            'Django==5.2\n'
            '-r other.txt\n'
            '--index-url https://example.invalid\n'
            '-e .\n'
            'pandas\n'
        )
        self.assertEqual(parsed, {'django', 'pandas'})

    def test_pyproject_reads_project_and_optional_dependencies(self):
        parsed = parse_pyproject(
            '[project]\n'
            'dependencies = ["django>=5", "pandas"]\n'
            '[project.optional-dependencies]\n'
            'dev = ["pytest"]\n'
        )
        self.assertIn('django', parsed)
        self.assertIn('pandas', parsed)
        self.assertIn('pytest', parsed)

    def test_pyproject_reads_poetry_dependencies(self):
        parsed = parse_pyproject(
            '[tool.poetry.dependencies]\n'
            'python = "^3.11"\n'
            'scikit-learn = "^1.4"\n'
        )
        self.assertIn('scikit-learn', parsed)

    def test_pipfile_reads_packages_and_dev_packages(self):
        parsed = parse_pipfile('[packages]\ndjango = "*"\n[dev-packages]\npytest = "*"\n')
        self.assertEqual(parsed, {'django', 'pytest'})

    def test_package_json_reads_all_dependency_sections(self):
        parsed = parse_package_json(
            '{"dependencies": {"react": "^18"}, "devDependencies": {"vite": "^5"}}'
        )
        self.assertEqual(parsed, {'react', 'vite'})

    def test_environment_yml_reads_conda_list(self):
        parsed = parse_environment_yml('dependencies:\n  - numpy=1.26\n  - pip\n  - pip:\n')
        self.assertEqual(parsed, {'numpy'})

    def test_broken_files_do_not_raise(self):
        self.assertEqual(parse_pyproject('not: [valid'), set())
        self.assertEqual(parse_package_json('{not json'), set())
        self.assertEqual(parse_pipfile('also not toml ['), set())

    def test_unknown_filename_parses_to_nothing(self):
        self.assertEqual(parse_dependency_file('Gemfile.lock', 'nokogiri'), set())


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
class PathMatchingTests(TestCase):
    def test_exact_path_matches(self):
        self.assertTrue(path_matches('manage.py', ['manage.py']))

    def test_matches_a_nested_path_by_segment(self):
        # The rule should not need to know the layout of the repository.
        self.assertTrue(path_matches('manage.py', ['backend/manage.py']))
        self.assertTrue(path_matches('Dockerfile', ['docker/Dockerfile']))

    def test_glob_against_the_whole_path(self):
        self.assertTrue(path_matches('.github/workflows/*', ['.github/workflows/ci.yml']))
        self.assertTrue(path_matches('*.ipynb', ['notebooks/analysis.ipynb']))

    def test_does_not_match_unrelated_files(self):
        self.assertFalse(path_matches('manage.py', ['main.py', 'setup.py']))

    def test_empty_pattern_matches_nothing(self):
        self.assertFalse(path_matches('', ['manage.py']))

    def test_empty_path_list_matches_nothing(self):
        self.assertFalse(path_matches('manage.py', []))


class DependencyMatchingTests(TestCase):
    def test_exact_package_matches(self):
        self.assertTrue(dependency_matches('pandas', {'pandas'}))

    def test_the_spelling_it_is_published_as_matches(self):
        # These packages cannot be installed without the parent technology, so a
        # prefix match is the behaviour we want for detection.
        self.assertTrue(dependency_matches('psycopg2', {'psycopg2-binary'}))
        self.assertTrue(dependency_matches('torch', {'torchvision'}))
        self.assertTrue(dependency_matches('django', {'djangorestframework'}))

    def test_an_unrelated_package_does_not_match(self):
        self.assertFalse(dependency_matches('flask', {'django', 'pandas'}))

    def test_a_short_pattern_over_matches(self):
        # Documented trade-off: this is why rules should be specific. The admin
        # help text says so, and a dry run shows the effect.
        self.assertTrue(dependency_matches('r', {'requests'}))

    def test_rule_pattern_is_normalised_too(self):
        self.assertTrue(dependency_matches('opencv_python', {'opencv-python'}))

    def test_empty_pattern_matches_nothing(self):
        self.assertFalse(dependency_matches('', {'pandas'}))


class SignalMatchingTests(TestCase):
    def setUp(self):
        # The seeded rules are data from a migration and are already present in
        # the test database; each test states its own rules explicitly.
        SkillSignal.objects.all().delete()

    def test_path_signal(self):
        self.assertTrue(signal_matches(
            signal('path', 'manage.py', 'Django'),
            RepoEvidence(paths=['manage.py']),
        ))

    def test_dependency_signal(self):
        self.assertTrue(signal_matches(
            signal('dependency', 'scikit-learn', 'Scikit-learn'),
            RepoEvidence(dependencies={'scikit-learn'}),
        ))

    def test_language_signal_is_case_insensitive(self):
        self.assertTrue(signal_matches(
            signal('language', 'python', 'Python'),
            RepoEvidence(language='Python'),
        ))

    def test_language_signal_does_not_match_a_different_language(self):
        self.assertFalse(signal_matches(
            signal('language', 'Python', 'Python'),
            RepoEvidence(language='R'),
        ))

    def test_unknown_kind_matches_nothing(self):
        self.assertFalse(signal_matches(
            signal('nonsense', 'x', 'Y'),
            RepoEvidence(paths=['x']),
        ))


class DetectSkillNamesTests(TestCase):
    def setUp(self):
        SkillSignal.objects.all().delete()

    def test_collects_every_matching_skill(self):
        signals = [
            signal('path', 'manage.py', 'Django'),
            signal('dependency', 'pandas', 'Pandas'),
            signal('dependency', 'torch', 'Machine Learning'),
            signal('language', 'Python', 'Python'),
            signal('dependency', 'pyspark', 'Big Data'),
        ]
        evidence = RepoEvidence(
            paths=['manage.py', 'requirements.txt'],
            dependencies={'pandas', 'torch'},
            language='Python',
        )
        self.assertEqual(
            detect_skill_names(evidence, signals),
            {'Django', 'Pandas', 'Machine Learning', 'Python'},
        )

    def test_no_signals_no_skills(self):
        self.assertEqual(detect_skill_names(RepoEvidence(paths=['manage.py']), []), set())


# ---------------------------------------------------------------------------
# Reading a repository
# ---------------------------------------------------------------------------
class FetchEvidenceTests(TestCase):
    def test_reads_the_tree_and_a_manifest(self):
        router = github_router(
            tree_paths=['manage.py', 'requirements.txt', 'app/views.py'],
            manifests={'requirements.txt': 'Django==5.2\npandas\n'},
        )
        with patch('portfolio_app.github_sync.urllib.request.urlopen', side_effect=router):
            evidence = fetch_evidence(make_repo())

        self.assertIn('manage.py', evidence.paths)
        self.assertEqual(evidence.dependencies, {'django', 'pandas'})
        self.assertEqual(evidence.language, 'Python')

    def test_a_failed_tree_read_raises(self):
        # Callers rely on this: "could not read" must not be mistaken for
        # "contains nothing", which would clear a project's tags.
        def boom(request, *args, **kwargs):
            raise OSError('offline')

        with patch('portfolio_app.github_sync.urllib.request.urlopen', side_effect=boom):
            with self.assertRaises(OSError):
                fetch_evidence(make_repo())

    def test_an_unreadable_manifest_still_returns_the_tree(self):
        def responder(request, *args, **kwargs):
            if '/git/trees/' in request.full_url:
                return FakeResponse({
                    'tree': [{'path': 'manage.py', 'type': 'blob'}],
                    'truncated': False,
                })
            raise OSError('contents unavailable')

        with patch('portfolio_app.github_sync.urllib.request.urlopen', side_effect=responder):
            evidence = fetch_evidence(make_repo())

        self.assertEqual(evidence.paths, ['manage.py'])
        self.assertEqual(evidence.dependencies, set())

    def test_missing_full_name_raises(self):
        with self.assertRaises(ValueError):
            fetch_evidence({'name': 'no-full-name'})


# ---------------------------------------------------------------------------
# Tagging during a sync
# ---------------------------------------------------------------------------
class SkillTaggingSyncTests(TestCase):
    def setUp(self):
        # The seeded rules are data from a migration; tests define their own so
        # the expectation is visible right here.
        SkillSignal.objects.all().delete()

    def _run(self, repos=None, tree_paths=(), manifests=None, calls=None,
             fail_tree=False, fail_contents=False, **kwargs):
        router = github_router(
            repos=repos or [make_repo()],
            tree_paths=tree_paths,
            manifests=manifests,
            calls=calls,
            fail_tree=fail_tree,
            fail_contents=fail_contents,
        )
        with patch('portfolio_app.github_sync.urllib.request.urlopen', side_effect=router):
            return sync_projects(username='armin2080', **kwargs)

    def test_detected_skills_are_linked_to_a_new_project(self):
        Skill.objects.create(name='Django', start_date=date(2021, 1, 1))
        signal('path', 'manage.py', 'Django')

        report = self._run(tree_paths=['manage.py'])

        project = Project.objects.get()
        self.assertEqual([s.name for s in project.skills_used.all()], ['Django'])
        self.assertTrue(report.tag_changes)

    def test_an_unknown_skill_is_created_hidden(self):
        signal('dependency', 'fastapi', 'FastAPI')

        self._run(
            tree_paths=['requirements.txt'],
            manifests={'requirements.txt': 'fastapi\n'},
        )

        created = Skill.objects.get(name='FastAPI')
        self.assertFalse(created.is_published)

    def test_an_existing_skill_is_reused_not_duplicated(self):
        existing = Skill.objects.create(name='Django', start_date=date(2021, 1, 1))
        signal('path', 'manage.py', 'Django')

        self._run(tree_paths=['manage.py'])

        self.assertEqual(Skill.objects.filter(name='Django').count(), 1)
        self.assertEqual(Project.objects.get().skills_used.get(), existing)

    def test_a_tag_that_no_longer_matches_is_removed(self):
        # The chosen policy: tags track the repository, so a stale tag goes.
        project = Project.objects.create(name='Old', link='https://example.invalid', github_repo_id=1001)
        stale = Skill.objects.create(name='Django', start_date=date(2021, 1, 1))
        project.skills_used.set([stale])

        self._run(tree_paths=['README.md'])

        self.assertEqual(Project.objects.get().skills_used.count(), 0)

    def test_auto_skills_off_leaves_the_tags_alone(self):
        project = Project.objects.create(
            name='Curated', link='https://example.invalid', github_repo_id=1001,
            auto_skills=False,
        )
        kept = Skill.objects.create(name='IT Service Management', start_date=date(2021, 1, 1))
        project.skills_used.set([kept])
        signal('path', 'manage.py', 'Django')

        calls = []
        self._run(tree_paths=['manage.py'], calls=calls)

        self.assertEqual([s.name for s in Project.objects.get().skills_used.all()],
                         ['IT Service Management'])
        # Contents were not even read.
        self.assertFalse([url for url in calls if '/contents/' in url or '/git/trees/' in url])

    def test_an_unchanged_repository_is_not_read_again(self):
        signal('path', 'manage.py', 'Django')
        self._run(tree_paths=['manage.py'])

        calls = []
        self._run(tree_paths=['manage.py'], calls=calls)

        self.assertFalse(
            [url for url in calls if '/git/trees/' in url or '/contents/' in url],
            'an unchanged repository should cost no content requests',
        )

    def test_force_skills_reads_an_unchanged_repository_again(self):
        signal('path', 'manage.py', 'Django')
        self._run(tree_paths=['manage.py'])

        calls = []
        self._run(tree_paths=['manage.py'], calls=calls, force_skills=True)

        self.assertTrue([url for url in calls if '/git/trees/' in url])

    def test_a_failed_read_leaves_existing_tags_untouched(self):
        project = Project.objects.create(name='P', link='https://example.invalid', github_repo_id=1001)
        kept = Skill.objects.create(name='Python', start_date=date(2020, 1, 1))
        project.skills_used.set([kept])
        signal('path', 'manage.py', 'Django')

        report = self._run(fail_tree=True)

        self.assertEqual([s.name for s in Project.objects.get().skills_used.all()], ['Python'])
        # A tagging hiccup is a warning, not an error: the import succeeded and
        # the timer unit should not be marked as failed for it.
        self.assertTrue(report.warnings)
        self.assertFalse(report.errors)
        self.assertTrue(report.ok)

    def test_an_empty_repository_clears_the_tags(self):
        # Distinct from the failure above: reading successfully and finding
        # nothing really does mean the tags no longer apply.
        project = Project.objects.create(name='P', link='https://example.invalid', github_repo_id=1001)
        stale = Skill.objects.create(name='Django', start_date=date(2020, 1, 1))
        project.skills_used.set([stale])

        report = self._run(tree_paths=[])

        self.assertEqual(Project.objects.get().skills_used.count(), 0)
        self.assertFalse(report.errors)

    def test_a_dry_run_writes_nothing(self):
        project = Project.objects.create(
            name='P', link='https://example.invalid', github_repo_id=1001,
        )
        Skill.objects.create(name='Django', start_date=date(2021, 1, 1))
        signal('dependency', 'fastapi', 'FastAPI')

        report = self._run(
            tree_paths=['manage.py', 'requirements.txt'],
            manifests={'requirements.txt': 'fastapi\n'},
            dry_run=True,
        )

        # Nothing was written...
        self.assertFalse(Skill.objects.filter(name='FastAPI').exists())
        self.assertEqual(project.skills_used.count(), 0)
        self.assertFalse(project.github_pushed_at)
        # ...but the changes were reported.
        self.assertTrue(report.tag_changes)
        self.assertTrue(report.skills_created)

    def test_manual_projects_are_never_tagged(self):
        Project.objects.create(name='Manual', link='https://example.invalid')
        signal('path', 'manage.py', 'Django')

        self._run(tree_paths=['manage.py'])

        manual = Project.objects.get(name='Manual')
        self.assertEqual(manual.skills_used.count(), 0)

    def test_without_any_signals_nothing_is_tagged(self):
        report = self._run(tree_paths=['manage.py'])

        self.assertEqual(Project.objects.get().skills_used.count(), 0)
        self.assertFalse(report.tag_changes)


class _Report:
    """Minimal stand-in for SyncReport, to keep these tests focused."""

    def __init__(self):
        self.dates_moved = []


# ---------------------------------------------------------------------------
# Experience dates
# ---------------------------------------------------------------------------
class SkillDateTests(TestCase):
    def test_a_date_is_moved_earlier_when_evidence_proves_it(self):
        Skill.objects.create(name='Python', start_date=date(2022, 1, 1))
        report = _Report()

        apply_skill_dates({'python': date(2019, 5, 1)}, report=report)

        self.assertEqual(Skill.objects.get(name='Python').start_date, date(2019, 5, 1))
        self.assertTrue(report.dates_moved)

    def test_a_date_is_never_moved_later(self):
        # Moving it later would silently shorten a claim the owner made by hand.
        Skill.objects.create(name='Python', start_date=date(2019, 5, 1))
        report = _Report()

        apply_skill_dates({'python': date(2024, 1, 1)}, report=report)

        self.assertEqual(Skill.objects.get(name='Python').start_date, date(2019, 5, 1))
        self.assertFalse(report.dates_moved)

    def test_an_unknown_skill_is_ignored(self):
        report = _Report()

        apply_skill_dates({'nonexistent': date(2020, 1, 1)}, report=report)

        self.assertFalse(report.dates_moved)

    def test_dry_run_does_not_write(self):
        Skill.objects.create(name='Python', start_date=date(2022, 1, 1))
        report = _Report()

        apply_skill_dates({'python': date(2019, 5, 1)}, report=report, dry_run=True)

        self.assertEqual(Skill.objects.get(name='Python').start_date, date(2022, 1, 1))
        self.assertTrue(report.dates_moved)


# ---------------------------------------------------------------------------
# Hidden skills stay hidden
# ---------------------------------------------------------------------------
class HiddenSkillTests(TestCase):
    def setUp(self):
        self.hidden = Skill.objects.create(
            name='FastAPI', start_date=date(2024, 1, 1), is_published=False,
        )
        self.shown = Skill.objects.create(
            name='Python', start_date=date(2020, 1, 1), is_published=True,
        )

    def test_skills_page_hides_an_unpublished_skill(self):
        response = self.client.get(reverse('skills'))
        self.assertContains(response, 'Python')
        self.assertNotContains(response, 'FastAPI')

    def test_resume_page_hides_an_unpublished_skill(self):
        response = self.client.get(reverse('resume'))
        self.assertNotContains(response, 'FastAPI')

    def test_homepage_hides_an_unpublished_skill(self):
        response = self.client.get(reverse('index'))
        self.assertNotContains(response, 'FastAPI')

    def test_project_cards_hide_an_unpublished_skill(self):
        project = Project.objects.create(name='P', link='https://example.invalid')
        project.skills_used.set([self.hidden, self.shown])

        response = self.client.get(reverse('projects'))

        self.assertContains(response, 'Python')
        self.assertNotContains(response, 'FastAPI')
