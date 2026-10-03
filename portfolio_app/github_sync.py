"""Import projects from public GitHub repositories.

Design notes
------------
* **Nothing is ever deleted.** A repository that disappears from GitHub simply
  stops being updated; the project stays until you decide what to do with it. An
  empty or failed API response therefore cannot wipe the portfolio.
* **Your edits always win, except where you asked them not to.** Names,
  descriptions, links and dates are written only when a project is first
  imported. Photos and categories are never touched, and `is_published` is
  never changed by the sync.
* **Skill tags are only ever suggested.** The sync reads what a repository
  contains and records `SkillSuggestion` rows, using rules the site owner wrote
  (`SkillSignal`). Nothing is tagged until a suggestion is accepted in the admin,
  so a rebuild can never drop a tag no rule can prove. Untick
  `suggest_skills` on a project to stop suggestions for it entirely.
* **Reading contents costs API requests.** A repository whose `pushed_at` has
  not changed is skipped — unless `force_skills` is set, which re-reads
  everything (needed after editing the rules). Failed reads leave the existing
  tags alone rather than clearing them.
* **`is_published` is never changed** by the sync. Imported projects arrive
  published (per the site owner's preference), and hiding one keeps it hidden.
* Repositories are matched on GitHub's numeric id, which survives renames.

Uses only the standard library (``urllib``) so no extra dependency is needed.
"""

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime

from django.conf import settings
from django.utils import timezone

from .models import Project, Skill, SkillSignal, SkillSuggestion
from .skill_detection import detect_skill_matches, fetch_evidence

logger = logging.getLogger(__name__)

API_ROOT = 'https://api.github.com'
USER_AGENT = 'armin2080-portfolio-sync'
# Safety valve against a pathological number of pages.
MAX_PAGES = 10
PAGE_SIZE = 100


@dataclass
class SyncReport:
    """What a sync run did. Returned rather than printed, so it is testable."""

    created: list = field(default_factory=list)
    updated: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    unchanged: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    fetched: int = 0
    # Skill suggestions (nothing is applied without confirmation).
    warnings: list = field(default_factory=list)
    skills_created: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)
    dates_proposed: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors

    def summary(self):
        summary = (
            f"{self.fetched} repositories fetched · "
            f"{len(self.created)} created · {len(self.updated)} updated · "
            f"{len(self.unchanged)} unchanged · {len(self.skipped)} skipped"
            + (f" · {len(self.errors)} errors" if self.errors else "")
            + (f" · {len(self.warnings)} warnings" if self.warnings else "")
        )
        skill_bits = []
        if self.suggestions:
            skill_bits.append(f"{len(self.suggestions)} projects with suggestions")
        if self.skills_created:
            skill_bits.append(f"{len(self.skills_created)} skills created hidden")
        if self.dates_proposed:
            skill_bits.append(f"{len(self.dates_proposed)} experience dates suggested")
        if skill_bits:
            summary += ' · ' + ' · '.join(skill_bits)
        return summary


def humanize_repo_name(repo_name):
    """'Bayesian-MCMC-Insurance-Regression' -> 'Bayesian MCMC Insurance Regression'.

    Separators become spaces, and each word that is entirely lower case is
    title-cased. Words that already contain a capital are left alone, so acronyms
    and product casing survive — title-casing unconditionally would produce
    'Mcmc', 'Ai' and 'Iphone'.
    """
    cleaned = re.sub(r'[-_.]+', ' ', repo_name).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    if not cleaned:
        return repo_name

    words = [
        word if any(character.isupper() for character in word) else word.title()
        for word in cleaned.split(' ')
    ]
    return ' '.join(words)


def _request_json(url, token=None, timeout=15):
    """GET a JSON document from the GitHub API."""
    headers = {
        'Accept': 'application/vnd.github+json',
        # GitHub rejects requests without a User-Agent.
        'User-Agent': USER_AGENT,
        'X-GitHub-Api-Version': '2022-11-28',
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8')), response.headers


def fetch_public_repos(username, token=None, timeout=15):
    """Every public repository owned by ``username`` (not forks of others' repos).

    Paginates via the ``Link`` header. Raises on network or API failure so the
    caller can report it rather than silently importing nothing.
    """
    repos = []
    url = (
        f'{API_ROOT}/users/{username}/repos'
        f'?per_page={PAGE_SIZE}&type=owner&sort=pushed&direction=desc'
    )

    for _page in range(MAX_PAGES):
        payload, headers = _request_json(url, token=token, timeout=timeout)
        if not isinstance(payload, list):
            raise ValueError(f'Unexpected GitHub response for {username}: {type(payload)}')
        repos.extend(payload)

        url = _next_page_url(headers.get('Link', ''))
        if not url:
            break
    else:
        logger.warning('Stopped after %s pages of repositories; some may be missing.', MAX_PAGES)

    return repos


def _next_page_url(link_header):
    """Extract the rel="next" URL from a GitHub Link header, or ''."""
    if not link_header:
        return ''
    for part in link_header.split(','):
        segments = part.split(';')
        if len(segments) < 2:
            continue
        url = segments[0].strip().strip('<>')
        if any(segment.strip() == 'rel="next"' for segment in segments[1:]):
            return url
    return ''


def is_syncable(repo, username):
    """Should this repository become a portfolio project?

    Forks, archived and disabled repositories are skipped: they are rarely
    portfolio material. Repositories owned by someone else are skipped too, in
    case the endpoint ever returns a contribution.
    """
    if repo.get('fork') or repo.get('archived') or repo.get('disabled'):
        return False
    if repo.get('private'):
        return False
    owner = (repo.get('owner') or {}).get('login', '')
    return owner.lower() == username.lower()


def _parse_github_datetime(value):
    """GitHub returns ISO-8601 with a trailing Z; Django wants an aware datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None


def project_defaults_from_repo(repo):
    """Field values for a project imported from this repository."""
    repo_name = repo.get('name') or ''
    pushed = _parse_github_datetime(repo.get('pushed_at'))
    created = _parse_github_datetime(repo.get('created_at'))

    return {
        'name': humanize_repo_name(repo_name),
        'description': (repo.get('description') or '').strip(),
        # Prefer a live demo when the repository declares one.
        'link': (repo.get('homepage') or '').strip() or repo.get('html_url', ''),
        'date': (pushed or created or timezone.now()).date(),
        'github_repo_id': repo.get('id'),
        'github_full_name': repo.get('full_name', ''),
        'github_synced_at': timezone.now(),
    }


def _skills_for_names(names, report, dry_run, signals=()):
    """Skill rows for these names, creating hidden ones for anything new.

    A name with no matching skill is a technology the site does not list yet.
    Creating it unpublished means it shows up in the admin for review instead of
    appearing on the site unannounced.

    When a rule says which main skill a new sub-skill belongs under
    (``parent_skill_name``), the created skill is attached to it. Without that a
    newly detected technology would land at the top level and need re-parenting
    by hand, which is exactly the clutter the tree exists to avoid.

    Returns ``(skill_rows, names)``. In a dry run no row is created, but the name
    is still returned so the preview reports the suggestion accurately.
    """
    # name -> main skill to attach a newly created skill to.
    parents = {}
    for signal in signals:
        if signal.parent_skill_name:
            parents.setdefault(
                signal.skill_name.lower(), signal.parent_skill_name
            )

    skills = []
    resolved_names = set()
    for name in sorted(names):
        skill = Skill.objects.filter(name__iexact=name).first()
        if skill is None:
            if dry_run:
                parent = parents.get(name.lower())
                report.skills_created.append(
                    f'{name} (would create, hidden'
                    + (f', under {parent})' if parent else ')')
                )
                resolved_names.add(name)
                continue
            parent_skill = None
            parent_name = parents.get(name.lower())
            if parent_name:
                parent_skill = Skill.objects.filter(
                    name__iexact=parent_name
                ).first()
                if parent_skill is None:
                    logger.warning(
                        'Rule wanted %s under %s, but no such main skill exists; '
                        'creating %s at the top level instead.',
                        name, parent_name, name,
                    )
            skill = Skill.objects.create(
                name=name,
                start_date=parent_skill.start_date if parent_skill
                else timezone.now().date(),
                parent=parent_skill,
                is_published=False,
            )
            report.skills_created.append(
                f'{name} (created, hidden'
                + (f', under {parent_skill.name})' if parent_skill else ')')
            )
        skills.append(skill)
        # The stored spelling, so a rule in a different case does not read as a
        # rename of an existing skill.
        resolved_names.add(skill.name)
    return skills, resolved_names


def _suggest_project_skills(project, repo, signals, report, dry_run, force_skills,
                            earliest, token):
    """Record which skills this repository demonstrates, for the owner to confirm.

    Writes :class:`~portfolio_app.models.SkillSuggestion` rows and nothing else.
    Tags are never changed here — accepting a suggestion is the owner's decision,
    made in the admin.

    Never raises: a repository that cannot be read leaves the existing
    suggestions untouched, because withdrawing them on a failed read would look
    like "this repository proves nothing". The failure is recorded as a warning
    rather than an error — the import itself succeeded — and the repository is
    retried next run because its ``pushed_at`` is only recorded after a
    successful read.
    """
    label = repo.get('full_name') or repo.get('name') or '?'
    pushed_at = repo.get('pushed_at') or ''

    # Nothing has changed since last time, so the detection result cannot have
    # changed either. Skip the API requests.
    if not force_skills and pushed_at and pushed_at == project.github_pushed_at:
        return

    try:
        evidence = fetch_evidence(repo, token=token)
    except Exception as exc:
        logger.warning('Could not read contents of %s: %s', label, exc)
        report.warnings.append(f'{label}: could not read contents ({exc})')
        return

    matches = detect_skill_matches(evidence, signals)
    skills, resolved_names = _skills_for_names(
        matches.keys(), report, dry_run, signals
    )

    if dry_run:
        # `skills` is empty in a dry run because no row is created, so report the
        # resolved names instead.
        for name in sorted(resolved_names):
            reason = ', '.join(matches.get(name, []))
            report.suggestions.append(f'{label}: {name} (would suggest \u00b7 {reason})')
        return

    # Rebuild this project's suggestions: rules may have changed, or the code may
    # have been refactored, so a suggestion that no longer applies goes away.
    wanted = {}
    for skill in skills:
        wanted[skill.pk] = ', '.join(matches.get(skill.name, []))[:300]

    existing = {s.skill_id: s for s in project.skill_suggestions.all()}
    removed = [pk for pk in existing if pk not in wanted]
    if removed:
        project.skill_suggestions.filter(skill_id__in=removed).delete()

    for pk, reason in wanted.items():
        current = existing.get(pk)
        if current is None:
            SkillSuggestion.objects.create(
                project=project, skill_id=pk, evidence=reason,
            )
            continue
        if current.evidence != reason:
            current.evidence = reason
            current.save(update_fields=['evidence', 'updated_at'])

    # Also drop suggestions for skills the project already carries: they have
    # been dealt with, so re-suggesting them is noise.
    already = set(project.skills_used.values_list('pk', flat=True))
    if already:
        project.skill_suggestions.filter(skill_id__in=already).delete()

    if wanted:
        report.suggestions.append(f'{label}: {len(wanted)} suggested')

    # Earliest sighting of each skill, used to propose honest experience dates.
    created = _parse_github_datetime(repo.get('created_at'))
    if created:
        for skill in skills:
            key = skill.name.lower()
            if key not in earliest or created.date() < earliest[key]:
                earliest[key] = created.date()

    # Only recorded after a successful read, so a failed run is retried rather
    # than being treated as "already up to date".
    project.github_pushed_at = pushed_at
    project.save(update_fields=['github_pushed_at', 'updated_at'])


def propose_skill_dates(earliest, report, dry_run=False):
    """Record an earlier start date as a *suggestion*, never applying it.

    Only ever proposes a date earlier than the current one: the sync must not be
    able to shorten an experience claim the site owner set by hand, only offer
    evidence that it could start sooner.
    """
    for key, when in sorted(earliest.items()):
        skill = Skill.objects.filter(name__iexact=key).first()
        if skill is None:
            continue
        if when >= min(filter(None, [skill.start_date, skill.suggested_start_date])):
            continue
        report.dates_proposed.append(f'{skill.name}: {skill.start_date} \u2192 {when}')
        if not dry_run:
            skill.suggested_start_date = when
            skill.save(update_fields=['suggested_start_date', 'updated_at'])


def sync_projects(username=None, token=None, dry_run=False, force_skills=False):
    """Import and refresh projects from GitHub. Returns a :class:`SyncReport`."""
    username = username or getattr(settings, 'GITHUB_USERNAME', '')
    token = token or getattr(settings, 'GITHUB_TOKEN', '') or None

    if not username:
        raise ValueError('GITHUB_USERNAME is not configured.')

    report = SyncReport()
    repos = fetch_public_repos(username, token=token)
    report.fetched = len(repos)

    signals = list(SkillSignal.objects.filter(is_active=True))
    if not signals:
        logger.info('No active skill signals are configured; nothing will be suggested.')
    earliest = {}

    for repo in repos:
        repo_id = repo.get('id')
        label = repo.get('full_name') or repo.get('name') or '?'

        try:
            if not is_syncable(repo, username):
                report.skipped.append(label)
                continue
            if not repo_id:
                report.errors.append(f'{label}: no repository id in the response')
                continue

            defaults = project_defaults_from_repo(repo)
            existing = Project.objects.filter(github_repo_id=repo_id).first()

            if existing is None:
                if dry_run:
                    report.created.append(f'{label} (would create)')
                    continue
                project = Project.objects.create(**defaults)
                report.created.append(label)
                if project.suggest_skills:
                    _suggest_project_skills(
                        project, repo, signals, report, dry_run, force_skills,
                        earliest, token,
                    )
                continue

            # name/description/link/date/image/category/skills/is_published all
            # belong to the site owner, so editing a project is never undone by a
            # later sync. Skill suggestions are the one thing it adds, and even
            # those do not become tags without confirmation.
            if dry_run:
                report.unchanged.append(f'{label} (would refresh)')
            else:
                existing.github_full_name = defaults['github_full_name']
                existing.github_synced_at = timezone.now()
                existing.save(
                    update_fields=['github_full_name', 'github_synced_at', 'updated_at']
                )
                report.updated.append(label)

            if existing.suggest_skills:
                _suggest_project_skills(
                    existing, repo, signals, report, dry_run, force_skills,
                    earliest, token,
                )

        except Exception as exc:  # one bad repository must not stop the run
            logger.exception('GitHub sync failed for %s', label)
            report.errors.append(f'{label}: {exc}')

    propose_skill_dates(earliest, report, dry_run=dry_run)

    return report


def refresh_project_from_github(project, token=None):
    """Re-import a single project's GitHub fields, overwriting the local ones.

    Used by the admin action, where overwriting is the explicit intent. Returns
    the updated project, or raises if the repository cannot be found.
    """
    token = token or getattr(settings, 'GITHUB_TOKEN', '') or None
    if not project.github_repo_id:
        raise ValueError('This project is not linked to a GitHub repository.')

    payload, _headers = _request_json(
        f'{API_ROOT}/repositories/{project.github_repo_id}', token=token
    )
    defaults = project_defaults_from_repo(payload)

    project.name = defaults['name']
    project.description = defaults['description']
    project.link = defaults['link']
    project.github_full_name = defaults['github_full_name']
    project.github_synced_at = timezone.now()
    project.save(update_fields=[
        'name', 'description', 'link', 'github_full_name', 'github_synced_at', 'updated_at',
    ])
    return project
