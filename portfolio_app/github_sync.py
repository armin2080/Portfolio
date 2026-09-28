"""Import projects from public GitHub repositories.

Design notes
------------
* **Nothing is ever deleted.** A repository that disappears from GitHub simply
  stops being updated; the project stays until you decide what to do with it. An
  empty or failed API response therefore cannot wipe the portfolio.
* **Your edits always win.** Names, descriptions, links and dates are written
  only when a project is first imported. Photos, categories and skills are never
  touched. After that the sync only refreshes its own bookkeeping fields, so
  customising a project is safe.
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

from .models import Project

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

    @property
    def ok(self):
        return not self.errors

    def summary(self):
        return (
            f"{self.fetched} repositories fetched · "
            f"{len(self.created)} created · {len(self.updated)} updated · "
            f"{len(self.unchanged)} unchanged · {len(self.skipped)} skipped"
            + (f" · {len(self.errors)} errors" if self.errors else "")
        )


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


def sync_projects(username=None, token=None, dry_run=False):
    """Import and refresh projects from GitHub. Returns a :class:`SyncReport`."""
    username = username or getattr(settings, 'GITHUB_USERNAME', '')
    token = token or getattr(settings, 'GITHUB_TOKEN', '') or None

    if not username:
        raise ValueError('GITHUB_USERNAME is not configured.')

    report = SyncReport()
    repos = fetch_public_repos(username, token=token)
    report.fetched = len(repos)

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
                else:
                    Project.objects.create(**defaults)
                    report.created.append(label)
                continue

            # Only the sync's own bookkeeping is refreshed. name/description/link/
            # date/image/category/skills/is_published belong to the site owner, so
            # editing a project here is never undone by a later sync.
            if dry_run:
                report.unchanged.append(f'{label} (would refresh)')
            else:
                existing.github_full_name = defaults['github_full_name']
                existing.github_synced_at = timezone.now()
                existing.save(
                    update_fields=['github_full_name', 'github_synced_at', 'updated_at']
                )
                report.updated.append(label)

        except Exception as exc:  # one bad repository must not stop the run
            logger.exception('GitHub sync failed for %s', label)
            report.errors.append(f'{label}: {exc}')

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
