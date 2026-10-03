"""Tests for automatic skill detection and tagging.

The GitHub HTTP layer is stubbed, so the real matching, parsing and tagging logic
runs without touching the network. Detection is deliberately deterministic, which
makes it fully testable — that is the main reason there is no guessing involved.
"""

import base64
import json
from datetime import date
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .github_sync import propose_skill_dates, sync_projects
from .models import Project, Skill, SkillSignal, SkillSuggestion
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
# Suggesting skills during a sync
# ---------------------------------------------------------------------------
class SkillSuggestionSyncTests(TestCase):
    def setUp(self):
        # The seeded rules and skill tree are data from migrations and are
        # present in the test database; each test states its own explicitly.
        SkillSignal.objects.all().delete()
        Skill.objects.all().delete()

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

    def test_a_detected_skill_becomes_a_suggestion_not_a_tag(self):
        Skill.objects.create(name='Django', start_date=date(2021, 1, 1))
        signal('path', 'manage.py', 'Django')

        report = self._run(tree_paths=['manage.py'])

        project = Project.objects.get()
        # The whole point: nothing is tagged without confirmation.
        self.assertEqual(project.skills_used.count(), 0)
        self.assertEqual(
            [s.skill.name for s in project.skill_suggestions.all()], ['Django']
        )
        self.assertTrue(report.suggestions)

    def test_the_suggestion_records_its_evidence(self):
        Skill.objects.create(name='Django', start_date=date(2021, 1, 1))
        signal('path', 'manage.py', 'Django')

        self._run(tree_paths=['manage.py'])

        self.assertEqual(Project.objects.get().skill_suggestions.get().evidence, 'manage.py')

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
        self.assertEqual(Project.objects.get().skill_suggestions.get().skill, existing)

    def test_a_skill_the_project_already_has_is_not_suggested(self):
        project = Project.objects.create(
            name='P', link='https://example.invalid', github_repo_id=1001,
        )
        tagged = Skill.objects.create(name='Django', start_date=date(2021, 1, 1))
        project.skills_used.set([tagged])
        signal('path', 'manage.py', 'Django')

        self._run(tree_paths=['manage.py'])

        self.assertEqual(project.skill_suggestions.count(), 0)

    def test_a_suggestion_that_no_longer_matches_is_withdrawn(self):
        # Re-running after a refactor should not leave a stale suggestion behind.
        signal('path', 'manage.py', 'Django')
        self._run(tree_paths=['manage.py'])
        self.assertEqual(Project.objects.get().skill_suggestions.count(), 1)

        self._run(tree_paths=['README.md'], force_skills=True)

        self.assertEqual(Project.objects.get().skill_suggestions.count(), 0)

    def test_running_twice_does_not_duplicate_a_suggestion(self):
        signal('path', 'manage.py', 'Django')
        self._run(tree_paths=['manage.py'])
        self._run(tree_paths=['manage.py'], force_skills=True)

        self.assertEqual(Project.objects.get().skill_suggestions.count(), 1)

    def test_existing_tags_are_never_touched(self):
        # A curated tag no rule can prove must survive a sync untouched.
        project = Project.objects.create(
            name='Curated', link='https://example.invalid', github_repo_id=1001,
        )
        kept = Skill.objects.create(name='IT Service Management', start_date=date(2021, 1, 1))
        project.skills_used.set([kept])
        signal('path', 'manage.py', 'Django')

        self._run(tree_paths=['manage.py'])

        self.assertEqual(
            [s.name for s in Project.objects.get().skills_used.all()],
            ['IT Service Management'],
        )

    def test_suggest_skills_off_reads_nothing(self):
        project = Project.objects.create(
            name='Curated', link='https://example.invalid', github_repo_id=1001,
            suggest_skills=False,
        )
        signal('path', 'manage.py', 'Django')

        calls = []
        self._run(tree_paths=['manage.py'], calls=calls)

        self.assertEqual(project.skill_suggestions.count(), 0)
        self.assertFalse(
            [url for url in calls if '/contents/' in url or '/git/trees/' in url]
        )

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

    def test_a_failed_read_leaves_suggestions_and_tags_untouched(self):
        project = Project.objects.create(
            name='P', link='https://example.invalid', github_repo_id=1001,
        )
        kept = Skill.objects.create(name='Python', start_date=date(2020, 1, 1))
        project.skills_used.set([kept])
        signal('path', 'manage.py', 'Django')

        report = self._run(fail_tree=True)

        self.assertEqual([s.name for s in Project.objects.get().skills_used.all()], ['Python'])
        self.assertEqual(project.skill_suggestions.count(), 0)
        # A detection hiccup is a warning, not an error: the import succeeded and
        # the timer unit should not be marked as failed for it.
        self.assertTrue(report.warnings)
        self.assertFalse(report.errors)
        self.assertTrue(report.ok)

    def test_an_empty_repository_withdraws_suggestions(self):
        # Distinct from a failure: reading successfully and finding nothing does
        # mean the repository supports no suggestions.
        signal('path', 'manage.py', 'Django')
        self._run(tree_paths=['manage.py'])

        report = self._run(tree_paths=[], force_skills=True)

        self.assertEqual(Project.objects.get().skill_suggestions.count(), 0)
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
        self.assertEqual(project.skill_suggestions.count(), 0)
        self.assertFalse(project.github_pushed_at)
        # ...but the suggestions were reported.
        self.assertTrue(report.suggestions)
        self.assertTrue(report.skills_created)

    def test_manual_projects_are_never_suggested_for(self):
        Project.objects.create(name='Manual', link='https://example.invalid')
        signal('path', 'manage.py', 'Django')

        self._run(tree_paths=['manage.py'])

        manual = Project.objects.get(name='Manual')
        self.assertEqual(manual.skills_used.count(), 0)
        self.assertEqual(manual.skill_suggestions.count(), 0)

    def test_without_any_signals_nothing_is_suggested(self):
        report = self._run(tree_paths=['manage.py'])

        self.assertEqual(Project.objects.get().skills_used.count(), 0)
        self.assertEqual(Project.objects.get().skill_suggestions.count(), 0)
        self.assertFalse(report.suggestions)


class _Report:
    """Minimal stand-in for SyncReport, to keep these tests focused."""

    def __init__(self):
        self.dates_proposed = []


# ---------------------------------------------------------------------------
# Experience dates
# ---------------------------------------------------------------------------
class SkillDateTests(TestCase):
    def setUp(self):
        Skill.objects.all().delete()

    def test_an_earlier_date_is_proposed_not_applied(self):
        Skill.objects.create(name='Python', start_date=date(2022, 1, 1))
        report = _Report()

        propose_skill_dates({'python': date(2019, 5, 1)}, report=report)

        skill = Skill.objects.get(name='Python')
        # The real date is untouched; the suggestion waits for confirmation.
        self.assertEqual(skill.start_date, date(2022, 1, 1))
        self.assertEqual(skill.suggested_start_date, date(2019, 5, 1))
        self.assertTrue(report.dates_proposed)

    def test_a_later_date_is_not_proposed(self):
        # Proposing a later date would silently shorten a claim made by hand.
        Skill.objects.create(name='Python', start_date=date(2019, 5, 1))
        report = _Report()

        propose_skill_dates({'python': date(2024, 1, 1)}, report=report)

        self.assertIsNone(Skill.objects.get(name='Python').suggested_start_date)
        self.assertFalse(report.dates_proposed)

    def test_an_already_proposed_date_is_not_re_proposed(self):
        skill = Skill.objects.create(name='Python', start_date=date(2022, 1, 1))
        skill.suggested_start_date = date(2019, 5, 1)
        skill.save()
        report = _Report()

        propose_skill_dates({'python': date(2020, 1, 1)}, report=report)

        self.assertEqual(Skill.objects.get(name='Python').suggested_start_date, date(2019, 5, 1))
        self.assertFalse(report.dates_proposed)

    def test_an_unknown_skill_is_ignored(self):
        report = _Report()

        propose_skill_dates({'nonexistent': date(2020, 1, 1)}, report=report)

        self.assertFalse(report.dates_proposed)

    def test_dry_run_does_not_write(self):
        Skill.objects.create(name='Python', start_date=date(2022, 1, 1))
        report = _Report()

        propose_skill_dates({'python': date(2019, 5, 1)}, report=report, dry_run=True)

        self.assertIsNone(Skill.objects.get(name='Python').suggested_start_date)
        self.assertTrue(report.dates_proposed)


# ---------------------------------------------------------------------------
# Hidden skills stay hidden
# ---------------------------------------------------------------------------
class HiddenSkillTests(TestCase):
    def setUp(self):
        Skill.objects.all().delete()
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


# ---------------------------------------------------------------------------
# Main skills and sub-skills
# ---------------------------------------------------------------------------
class SkillTreeTests(TestCase):
    """The skills page shows main skills as cards, with sub-skills underneath."""

    def setUp(self):
        # Start from nothing: the real tree is seeded by a migration.
        Skill.objects.all().delete()
        self.parent = Skill.objects.create(
            name='Python', start_date=date(2020, 1, 1), description='Main language.',
            display_order=1,
        )
        self.child = Skill.objects.create(
            name='Pandas', start_date=date(2020, 1, 1), parent=self.parent,
        )

    def test_a_subskill_appears_under_its_main_skill(self):
        response = self.client.get(reverse('skills'))

        self.assertContains(response, 'Python')
        self.assertContains(response, 'Pandas')

    def test_the_card_lists_the_subskills(self):
        response = self.client.get(reverse('skills'))
        card = response.context['cards'][0]

        self.assertEqual(card['skill'], self.parent)
        self.assertEqual(card['subskills'], [self.child])

    def test_only_main_skills_get_a_card(self):
        response = self.client.get(reverse('skills'))

        self.assertEqual([c['skill'].name for c in response.context['cards']], ['Python'])

    def test_a_hidden_subskill_is_not_listed(self):
        self.child.is_published = False
        self.child.save()

        response = self.client.get(reverse('skills'))

        self.assertNotContains(response, 'Pandas')

    def test_a_hidden_main_skill_is_not_shown(self):
        self.parent.is_published = False
        self.parent.save()

        cards = self.client.get(reverse('skills')).context['cards']

        self.assertEqual(cards, [])

    def test_project_count_includes_subskill_work(self):
        # A project tagged only with pandas should count towards Python, or
        # adding detail to the tree would make a main skill look less used.
        project = Project.objects.create(name='P', link='https://example.invalid')
        project.skills_used.set([self.child])

        card = self.client.get(reverse('skills')).context['cards'][0]

        self.assertEqual(card['project_count'], 1)

    def test_a_project_is_counted_once_however_many_subskills_it_uses(self):
        project = Project.objects.create(name='P', link='https://example.invalid')
        other = Skill.objects.create(
            name='NumPy', start_date=date(2020, 1, 1), parent=self.parent,
        )
        project.skills_used.set([self.child, other, self.parent])

        card = self.client.get(reverse('skills')).context['cards'][0]

        self.assertEqual(card['project_count'], 1)

    def test_display_order_controls_the_page_order(self):
        Skill.objects.create(
            name='Machine Learning', start_date=date(2015, 1, 1), display_order=0,
        )

        cards = self.client.get(reverse('skills')).context['cards']

        self.assertEqual([c['skill'].name for c in cards], ['Machine Learning', 'Python'])

    def test_a_card_shows_the_image_when_one_is_set(self):
        self.parent.image = 'skills/python.png'
        self.parent.save()

        response = self.client.get(reverse('skills'))

        self.assertContains(response, 'skills/python.png')

    def test_a_card_shows_a_placeholder_without_an_image(self):
        response = self.client.get(reverse('skills'))

        # The banner has a fixed height so the grid rows stay aligned whether or
        # not an image is present.
        self.assertContains(response, 'h-24 md:h-28')
        self.assertNotContains(response, '/media/skills/')

    def test_the_grid_uses_three_columns_on_wide_screens(self):
        # Guards the card size: two columns with a 16/9 image made each card
        # ~470px tall and the page ~3000px.
        response = self.client.get(reverse('skills'))

        self.assertContains(response, 'lg:grid-cols-3')


class SkillTreeValidationTests(TestCase):
    def setUp(self):
        Skill.objects.all().delete()

    def test_a_skill_cannot_be_its_own_parent(self):
        skill = Skill.objects.create(name='Python', start_date=date(2020, 1, 1))
        skill.parent = skill

        with self.assertRaises(ValidationError):
            skill.clean()

    def test_the_tree_is_only_two_levels_deep(self):
        # A third level would need a recursive template for no content benefit.
        main = Skill.objects.create(name='Python', start_date=date(2020, 1, 1))
        sub = Skill.objects.create(name='Pandas', start_date=date(2020, 1, 1), parent=main)
        deeper = Skill(name='groupby', start_date=date(2020, 1, 1), parent=sub)

        with self.assertRaises(ValidationError):
            deeper.clean()

    def test_a_main_skill_is_always_valid(self):
        Skill(name='Python', start_date=date(2020, 1, 1)).clean()


class SkillRollupTests(TestCase):
    """A project card shows the main skill behind each tag."""

    def setUp(self):
        Skill.objects.all().delete()
        self.main = Skill.objects.create(name='Data Analysis', start_date=date(2020, 1, 1))
        self.sub = Skill.objects.create(
            name='Pandas', start_date=date(2020, 1, 1), parent=self.main,
        )

    def test_a_subskill_tag_rolls_up_to_its_main_skill(self):
        project = Project.objects.create(name='P', link='https://example.invalid')
        project.skills_used.set([self.sub])

        self.assertEqual([s.name for s in project.display_skills()], ['Data Analysis'])

    def test_a_main_skill_tag_is_shown_as_it_is(self):
        project = Project.objects.create(name='P', link='https://example.invalid')
        project.skills_used.set([self.main])

        self.assertEqual([s.name for s in project.display_skills()], ['Data Analysis'])

    def test_a_main_skill_appears_once_when_tagged_with_its_own_subskill(self):
        project = Project.objects.create(name='P', link='https://example.invalid')
        project.skills_used.set([self.main, self.sub])

        self.assertEqual([s.name for s in project.display_skills()], ['Data Analysis'])

    def test_the_card_shows_the_main_skill_not_the_subskill(self):
        project = Project.objects.create(name='P', link='https://example.invalid')
        project.skills_used.set([self.sub])

        response = self.client.get(reverse('projects'))

        self.assertContains(response, 'Data Analysis')
        self.assertNotContains(response, 'Pandas')

    def test_a_skill_created_by_a_rule_lands_under_its_parent(self):
        # parent_skill_name is what stops a newly detected technology from
        # appearing at the top level and needing to be re-parented by hand.
        Skill.objects.create(name='Machine Learning', start_date=date(2020, 1, 1))
        SkillSignal.objects.all().delete()
        SkillSignal.objects.create(
            kind='dependency', pattern='pytorch-lightning',
            skill_name='PyTorch Lightning', parent_skill_name='Machine Learning',
        )
        router = github_router(
            repos=[make_repo()],
            tree_paths=['requirements.txt'],
            manifests={'requirements.txt': 'pytorch-lightning\n'},
        )
        with patch('portfolio_app.github_sync.urllib.request.urlopen', side_effect=router):
            sync_projects(username='armin2080')

        created = Skill.objects.get(name='PyTorch Lightning')
        self.assertEqual(created.parent.name, 'Machine Learning')
        self.assertFalse(created.is_published)

    def test_a_missing_parent_falls_back_to_the_top_level(self):
        # A rule naming a parent that does not exist must not lose the skill.
        SkillSignal.objects.all().delete()
        SkillSignal.objects.create(
            kind='dependency', pattern='fastapi',
            skill_name='FastAPI', parent_skill_name='Nonexistent',
        )
        router = github_router(
            repos=[make_repo()],
            tree_paths=['requirements.txt'],
            manifests={'requirements.txt': 'fastapi\n'},
        )
        with patch('portfolio_app.github_sync.urllib.request.urlopen', side_effect=router):
            sync_projects(username='armin2080')

        self.assertIsNone(Skill.objects.get(name='FastAPI').parent)
