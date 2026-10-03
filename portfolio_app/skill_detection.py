"""Work out which skills a GitHub repository actually demonstrates.

The evidence is the same thing a person would look at: which files are in the
repository, and what its dependency files ask for. ``manage.py`` means Django;
``scikit-learn`` in requirements.txt means scikit-learn; a Dockerfile means
containers. Nothing here guesses — a skill is only reported when a rule the
site owner wrote actually matched, so every tag can be traced back to a fact
about the repository.

Two things this module deliberately does NOT do:

* It never returns an assumed value when the repository cannot be read. A
  failure raises, and the caller leaves the existing skill tags alone. Treating
  "could not read" as "contains nothing" would silently strip a project's tags
  on a flaky connection.
* It never touches the database. Matching is a pure function of the rules and
  the evidence, which keeps it testable without any network access.
"""

import base64
import fnmatch
import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import quote

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.11+ is the supported floor
    tomllib = None

logger = logging.getLogger(__name__)

# Matched against the last path segment. Order matters only for readability.
DEPENDENCY_FILES = (
    'requirements.txt',
    'pyproject.toml',
    'Pipfile',
    'package.json',
    'environment.yml',
)

# A repository can list hundreds of files but only a few manifests matter, and
# each one costs an API request. Unauthenticated GitHub allows 60 requests per
# hour, so this ceiling keeps a single repository from eating the whole budget.
MAX_MANIFESTS = 3


@dataclass
class RepoEvidence:
    """What a repository is made of."""

    paths: list = field(default_factory=list)
    dependencies: set = field(default_factory=set)
    language: str = ''


def _get_json(url, token=None, timeout=15):
    """The JSON body of a GitHub API response.

    Indirection that keeps this module importable without a cycle:
    ``github_sync`` imports this module, so importing its helper at module level
    would be circular. The helper also returns response headers, which detection
    has no use for, so they are dropped here.
    """
    from .github_sync import _request_json

    payload, _headers = _request_json(url, token=token, timeout=timeout)
    return payload


# ---------------------------------------------------------------------------
# Dependency files
# ---------------------------------------------------------------------------

def normalise_package_name(name):
    """'Scikit_Learn[alldeps]>=1.0' -> 'scikit-learn'.

    A name can arrive with extras, a version specifier or an environment marker
    attached; only the distribution name identifies the package.
    """
    name = str(name).strip().lower()
    name = re.split(r'[<>=!~;\[\s(]', name, maxsplit=1)[0]
    return name.replace('_', '-').strip()


def parse_requirements(text):
    """Names from a requirements.txt, ignoring options and comments."""
    packages = set()
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        # Skip -r other.txt, -e ., --index-url ... and blank lines.
        if not line or line.startswith('-'):
            continue
        packages.add(normalise_package_name(line))
    return {package for package in packages if package}


def parse_pyproject(text):
    """Names from [project] dependencies and Poetry's dependencies."""
    if tomllib is None:
        return set()
    try:
        data = tomllib.loads(text)
    except (ValueError, TypeError):
        return set()

    packages = set()
    project = data.get('project') or {}
    for dependency in project.get('dependencies') or []:
        packages.add(normalise_package_name(dependency))
    for group in (project.get('optional-dependencies') or {}).values():
        for dependency in group or []:
            packages.add(normalise_package_name(dependency))

    poetry = (data.get('tool') or {}).get('poetry') or {}
    for name in poetry.get('dependencies') or {}:
        packages.add(normalise_package_name(name))
    for group in (poetry.get('group') or {}).values():
        for name in (group or {}).get('dependencies') or {}:
            packages.add(normalise_package_name(name))

    return {package for package in packages if package}


def parse_pipfile(text):
    """Names from Pipfile's [packages] and [dev-packages]."""
    if tomllib is None:
        return set()
    try:
        data = tomllib.loads(text)
    except (ValueError, TypeError):
        return set()

    packages = set()
    for section in ('packages', 'dev-packages'):
        for name in data.get(section) or {}:
            packages.add(normalise_package_name(name))
    return {package for package in packages if package}


def parse_package_json(text):
    """Names from a package.json, including devDependencies."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return set()
    if not isinstance(data, dict):
        return set()

    packages = set()
    for section in ('dependencies', 'devDependencies', 'peerDependencies'):
        for name in data.get(section) or {}:
            packages.add(normalise_package_name(name))
    return {package for package in packages if package}


def parse_environment_yml(text):
    """Names from a conda environment.yml dependencies list."""
    packages = set()
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line.startswith('- '):
            continue
        entry = line[2:].strip()
        if entry.startswith('pip') or entry.startswith('{'):
            continue
        packages.add(normalise_package_name(entry.split('=', 1)[0]))
    return {package for package in packages if package}


PARSERS = {
    'requirements.txt': parse_requirements,
    'pyproject.toml': parse_pyproject,
    'Pipfile': parse_pipfile,
    'package.json': parse_package_json,
    'environment.yml': parse_environment_yml,
}


def parse_dependency_file(filename, text):
    parser = PARSERS.get(filename)
    return parser(text) if parser else set()


# ---------------------------------------------------------------------------
# Reading a repository
# ---------------------------------------------------------------------------

def fetch_evidence(repo, token=None, timeout=15):
    """The file paths and dependency names of one repository.

    ``repo`` is a repository payload as returned by the GitHub API, so the
    language and default branch come from the caller's response and cost nothing
    extra. Costs one API request for the file tree, plus one per dependency file
    that exists.

    Raises on network or API failure — see the module docstring.
    """
    from .github_sync import API_ROOT

    full_name = repo.get('full_name')
    if not full_name:
        raise ValueError('Repository payload has no full_name.')

    branch = repo.get('default_branch') or 'main'
    # Both are interpolated into a URL, so they must be percent-encoded: a path
    # such as 'old version/requirements.txt' would otherwise be an invalid URL.
    tree = _get_json(
        f'{API_ROOT}/repos/{full_name}/git/trees/{quote(branch)}?recursive=1',
        token=token, timeout=timeout,
    )
    if tree.get('truncated'):
        logger.warning('File list for %s was truncated; some signals may be missed.', full_name)

    paths = [
        entry['path']
        for entry in tree.get('tree', [])
        if entry.get('type') == 'blob' and entry.get('path')
    ]

    dependencies = set()
    # Prefer the shallowest match: in a repository that keeps an 'old version/'
    # copy of everything, the top-level requirements.txt is the authoritative one.
    manifest_candidates = sorted(
        (path for path in paths if path.rsplit('/', 1)[-1] in DEPENDENCY_FILES),
        key=lambda path: (path.count('/'), path),
    )[:MAX_MANIFESTS]

    for path in manifest_candidates:
        try:
            payload = _get_json(
                f'{API_ROOT}/repos/{full_name}/contents/{quote(path)}',
                token=token, timeout=timeout,
            )
            content = payload.get('content') or ''
            if not content:
                continue
            text = base64.b64decode(content).decode('utf-8', 'replace')
        except Exception:
            # One unreadable manifest must not lose the whole repository: the
            # file list is still valid evidence on its own.
            logger.warning('Could not read %s from %s.', path, full_name, exc_info=True)
            continue

        dependencies |= parse_dependency_file(path.rsplit('/', 1)[-1], text)

    return RepoEvidence(
        paths=paths,
        dependencies=dependencies,
        language=(repo.get('language') or '').strip(),
    )


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def path_matches(pattern, paths):
    """Glob a pattern against every path and against each path segment.

    Matching segments means ``manage.py`` finds ``backend/manage.py`` and
    ``Dockerfile`` finds ``docker/Dockerfile``, without the rule having to know
    the layout. ``.github/workflows/*`` still works as a full-path glob.
    """
    if not pattern:
        return False
    for path in paths:
        if fnmatch.fnmatch(path, pattern):
            return True
        if any(fnmatch.fnmatch(segment, pattern) for segment in path.split('/')):
            return True
    return False


def dependency_matches(pattern, dependencies):
    """Does a required package start with this name?

    Matching the start of the name catches every spelling a package is
    published as: ``psycopg2`` finds ``psycopg2-binary``, ``torch`` finds
    ``torchvision``, ``django`` finds ``djangorestframework``. That is usually
    right for detection, because these packages cannot be installed without the
    parent technology — you cannot use Django REST Framework without Django.

    The trade-off is that a short pattern over-matches: ``r`` would match
    ``requests``. Write rules at the granularity you mean, and check the result
    on a dry run after adding one.
    """
    pattern = normalise_package_name(pattern)
    if not pattern:
        return False
    return any(dependency.startswith(pattern) for dependency in dependencies)


def signal_matches(signal, evidence):
    """Does one admin-defined rule match this repository?"""
    if signal.kind == 'path':
        return path_matches(signal.pattern, evidence.paths)
    if signal.kind == 'dependency':
        return dependency_matches(signal.pattern, evidence.dependencies)
    if signal.kind == 'language':
        return signal.pattern.strip().lower() == evidence.language.strip().lower()
    logger.warning('Ignoring skill signal with unknown kind %r.', signal.kind)
    return False


def detect_skill_names(evidence, signals):
    """Every skill name implied by the rules, for this evidence."""
    return set(detect_skill_matches(evidence, signals))


def detect_skill_matches(evidence, signals):
    """Skill name -> the evidence that matched, so a suggestion can explain itself.

    Showing the reason matters: the site owner confirms or dismisses each
    suggestion, and "manage.py" is far easier to judge than a bare tag.
    """
    matches = {}
    for signal in signals:
        if not signal_matches(signal, evidence):
            continue
        reason = signal.pattern
        if signal.kind == 'dependency':
            reason = f'{signal.pattern} in a dependency file'
        elif signal.kind == 'language':
            reason = f'main language {evidence.language}'
        matches.setdefault(signal.skill_name, [])
        if reason not in matches[signal.skill_name]:
            matches[signal.skill_name].append(reason)
    return matches
