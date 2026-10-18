"""Build the CV as a PDF from the content shown on the site.

The resume page and the CV share one source of truth. Everything the CV shows —
the header, the summary, experience, education, the projects, the skill tree and
the certificates — is read from the database when a visitor asks for the file, so
editing the site is what changes the CV. There is no uploaded PDF to keep in
step with the content any more.

How it works, in three steps:

1. ``build_resume_context()`` reads the published content and **escapes every
   value for LaTeX** before it goes near the template. Escaping here rather than
   in the template means a new field cannot be added without it: a skill called
   ``C++`` or a description containing ``50%`` would otherwise produce a document
   that does not compile, or one that runs arbitrary TeX.
2. ``render_latex()`` renders ``resume/resume.tex`` with that context. LaTeX and
   Django's template syntax collide — LaTeX writes ``{`` and ``%`` everywhere,
   so ``\newcommand{\foo}[2]{%`` or ``\textbf{{{ x }}}`` would be read as a
   Django tag — and Django cannot be told to use other delimiters. The file is
   therefore rendered with Jinja2, which can: it uses ``<<value>>``,
   ``<% tag %>`` and ``<# comment #>`` instead. Only the parts filled in from
   the database use those delimiters, so every other line of the design is
   ordinary LaTeX and can be edited as such.
3. ``compile_latex()`` runs a LaTeX engine over the source in a temporary
   directory and returns the PDF bytes.

Nothing is written to ``MEDIA_ROOT``. The PDF lives only in the cache, keyed by
the hash of the source it was built from, which is why a brand new rendering can
never serve a stale file and why ``manage.py prune_media`` has nothing to
misidentify as an orphan.

On the production Raspberry Pi the compile is the expensive part, so repeated
downloads of unchanged content are served from the cache, and only one compile
runs at a time (a second request during a compile waits for it rather than
starting a competing LaTeX process on a machine with 416 MB of RAM).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.core.cache import cache
from django.utils.text import slugify
from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from .models import Certificate, Education, Profile, Project, Skill, WorkExperience

logger = logging.getLogger(__name__)

# Where resume.tex lives, and what it is called.
RESUME_DIR = Path(settings.BASE_DIR) / 'resume'
RESUME_TEMPLATE_NAME = 'resume.tex'

# The document is a style, not data, so it mirrors the Languages panel on
# /resume/. That page hardcodes the same three entries; if they ever become
# editable they should move to a model and this constant should go away.
LANGUAGES = (
    ('Persian', 'Native'),
    ('English', 'Advanced (C1)'),
    ('German', 'A2/B1'),
)

# Leading bullet characters are stripped from description lines: the CV supplies
# its own bullet, and a GitHub description pasted in with a leading "- " would
# otherwise be double-bulleted.
_BULLET_PREFIXES = ('-', '*', '\u2022', '\u00b7')


class ResumeError(Exception):
    """Base class for anything that stops a CV being produced."""


class LatexEngineMissing(ResumeError):
    """No LaTeX engine could be found on this machine."""


class LatexCompileError(ResumeError):
    """The engine ran but did not produce a PDF."""

    def __init__(self, message, log=''):
        super().__init__(message)
        self.log = log

# LaTeX escapes, and the URL escapes, are deliberately different sets. Inside
# ``\\href{...}`` a raw underscore is handled by hyperref and a raw tilde is
# fine, but ``%`` starts a comment, ``#`` is a macro parameter and ``&`` is an
# alignment tab, and all three produce a document that will not compile.
_TEXT_ESCAPES = {
    '\\': r'\textbackslash{}',
    '&': r'\&',
    '%': r'\%',
    '$': r'\$',
    '#': r'\#',
    '_': r'\_',
    '{': r'\{',
    '}': r'\}',
    '~': r'\textasciitilde{}',
    '^': r'\textasciicircum{}',
}
_URL_ESCAPES = {
    '\\': r'\textbackslash{}',
    '{': r'\{',
    '}': r'\}',
    '%': r'\%',
    '#': r'\#',
    '&': r'\&',
    '$': r'\$',
}


def _escaper(table):
    """Return a function that escapes one of the tables above in a single pass.

    A single ``re.sub`` pass matters: replacing the backslashes first and the
    braces afterwards would escape the backslashes it had just inserted.
    """
    pattern = re.compile('|'.join(re.escape(char) for char in table))

    def escape(value):
        if value is None:
            return ''
        return pattern.sub(lambda match: table[match.group()], str(value))

    return escape


latex_escape = _escaper(_TEXT_ESCAPES)
latex_escape_url = _escaper(_URL_ESCAPES)


def format_month_year(value, fallback='Present'):
    """Format a date as ``MM/YYYY``, or `fallback` when there is no date."""
    if not value:
        return fallback
    return f'{value.month:02d}/{value.year}'


def format_date_range(start, end, fallback='Present'):
    return f'{format_month_year(start, fallback)} -- {format_month_year(end, fallback)}'


def description_lines(text):
    """Split a stored description into bullet-sized lines.

    Returns already-escaped lines. Empty lines and the bullet markers the author
    typed are dropped, so both a blank line and a ``- item`` render as one CV
    bullet.
    """
    lines = []
    for raw in (text or '').splitlines():
        line = raw.strip()
        while line[:1] in _BULLET_PREFIXES:
            line = line[1:].strip()
        if line:
            lines.append(latex_escape(line))
    return lines


# ---------------------------------------------------------------------------
# Reading the site's content
# ---------------------------------------------------------------------------


def _contact_entries(profile):
    """The header contact line, as (label, hyperlink) pairs.

    An entry with no URL is rendered as plain text — the location and the phone
    number — which is why this returns pairs rather than a single string. A
    *link* with no URL is left out entirely: a bare "LinkedIn" with nothing
    behind it is worse than not mentioning it.
    """
    entries = []

    def add_text(value):
        if value:
            entries.append({'label': latex_escape(value), 'url': ''})

    def add_link(label, url):
        if url:
            entries.append({
                'label': latex_escape(label),
                'url': latex_escape_url(url),
            })

    if profile:
        add_text(profile.location)
        add_link(profile.email, f'mailto:{profile.email}')
        add_text(profile.phone)
        add_link('LinkedIn', profile.linkedin_url)
        add_link('GitHub', profile.github_url)

    add_link('Portfolio', settings.SITE_URL)
    return entries


def _experience_entries():
    entries = []
    for experience in WorkExperience.objects.all():
        entries.append({
            'company': latex_escape(experience.company),
            'role': latex_escape(experience.role),
            'location': latex_escape(experience.location),
            'dates': format_date_range(experience.start_date, experience.end_date),
            'bullets': description_lines(experience.description),
        })
    return entries


def _education_entries():
    entries = []
    for education in Education.objects.all():
        entries.append({
            'institution': latex_escape(education.institution),
            'degree': latex_escape(education.degree),
            'dates': format_date_range(education.start_date, education.end_date),
            'bullets': description_lines(education.description),
        })
    return entries


def _project_entries():
    """The most recent published projects, with the technologies they used."""
    projects = (
        Project.objects.filter(is_published=True)
        .prefetch_related('skills_used__parent')[: settings.RESUME_MAX_PROJECTS]
    )
    entries = []
    for project in projects:
        technologies = ', '.join(
            latex_escape(skill.name) for skill in project.display_skills()
        )
        entries.append({
            'name': latex_escape(project.name),
            'link': latex_escape_url(project.link),
            'technologies': technologies,
            'bullets': description_lines(project.description),
        })
    return entries


def _skill_rows():
    """Main skills paired with their sub-skills, both published.

    The two-level tree already groups technologies the way a CV wants them — the
    main skill is the heading and the sub-skills are the technologies under it —
    so no separate categorisation is needed.
    """
    main_skills = list(
        Skill.objects.filter(is_published=True, parent__isnull=True)
        .order_by('display_order', 'start_date', 'name')
    )
    subskills_by_parent = {}
    for subskill in (
        Skill.objects.filter(is_published=True, parent__isnull=False)
        .order_by('display_order', 'start_date', 'name')
    ):
        subskills_by_parent.setdefault(subskill.parent_id, []).append(subskill)

    return [(skill, subskills_by_parent.get(skill.pk, [])) for skill in main_skills]


def _skill_entries(rows):
    return [
        {
            'name': latex_escape(skill.name),
            'technologies': ', '.join(latex_escape(subskill.name) for subskill in subskills),
        }
        for skill, subskills in rows
    ]


def _skill_keywords(rows):
    """Every skill name, for the PDF's keyword metadata."""
    names = [skill.name for skill, _subskills in rows]
    names += [subskill.name for _skill, subskills in rows for subskill in subskills]
    return latex_escape(', '.join(names))


def _certificate_entries():
    return [
        {
            'name': latex_escape(certificate.name),
            'institution': latex_escape(certificate.institution),
            'year': certificate.issue_date.year,
        }
        for certificate in Certificate.objects.all()
    ]


def build_resume_context():
    """Everything the template needs, with every value already LaTeX-escaped."""
    profile = Profile.objects.first()
    skill_rows = _skill_rows()
    return {
        'name': latex_escape(profile.name if profile else ''),
        'title': latex_escape(profile.title.upper() if profile and profile.title else ''),
        'contact': _contact_entries(profile),
        'summary': latex_escape(profile.professional_summary if profile else ''),
        'keywords': _skill_keywords(skill_rows),
        'experiences': _experience_entries(),
        'educations': _education_entries(),
        'projects': _project_entries(),
        'skills': _skill_entries(skill_rows),
        'languages': [
            {'name': latex_escape(name), 'level': latex_escape(level)}
            for name, level in LANGUAGES
        ],
        'certificates': _certificate_entries(),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# Jinja2 rather than Django's template engine, because LaTeX and Django's
# delimiters collide and Django offers no way to change them: LaTeX writes
# ``\textbf{{{ x }}}`` and ``\newcommand{\foo}[2]{%``, both of which Django would
# try to parse as a variable or a tag. Jinja2 takes custom delimiters, so
# ordinary LaTeX passes through untouched. See the header of resume.tex.
#
# trim_blocks/lstrip_blocks drop the newline and indent that a control tag would
# otherwise leave behind, so the generated document does not accumulate blank
# lines and stray spaces between every list entry.
#
# StrictUndefined turns a context key that the module forgot to provide into an
# error instead of silently emitting an empty CV with a section missing.
LATEX_TEMPLATE_ENVIRONMENT = Environment(
    loader=FileSystemLoader(str(RESUME_DIR)),
    autoescape=False,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    block_start_string='<%',
    block_end_string='%>',
    variable_start_string='<<',
    variable_end_string='>>',
    comment_start_string='<#',
    comment_end_string='#>',
)


def render_latex(context=None):
    """Render resume.tex into a complete LaTeX document."""
    if context is None:
        context = build_resume_context()
    try:
        template = LATEX_TEMPLATE_ENVIRONMENT.get_template(RESUME_TEMPLATE_NAME)
        return template.render(**context)
    except TemplateError as exc:
        # A template that no longer matches the context is a bug in this module
        # or in resume.tex, not something the visitor can do anything about.
        raise ResumeError(f'{RESUME_TEMPLATE_NAME} could not be rendered: {exc}') from exc


def build_latex_source():
    """Convenience wrapper: context -> LaTeX source."""
    return render_latex(build_resume_context())


def pdf_filename():
    """The name the downloaded file is offered under, e.g. armin-maddah-asl-CV.pdf."""
    profile = Profile.objects.first()
    base = slugify(profile.name) if profile and profile.name else ''
    return f'{base}-CV.pdf' if base else 'CV.pdf'


def content_disposition():
    """An attachment header that survives a non-ASCII name.

    ``filename`` is the plain fallback older clients use; ``filename*`` carries
    the real (percent-encoded) name. slugify() already strips accents, so the
    pair matters little in practice — but a name typed entirely in another script
    would slugify to nothing, and the fallback keeps the download working.
    """
    filename = pdf_filename()
    return (
        f'attachment; filename="{filename}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )


# ---------------------------------------------------------------------------
# Compiling
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LatexEngine:
    """A LaTeX binary, and the arguments it needs for a non-interactive run."""

    name: str
    path: str

    def command(self, source_path, output_dir):
        if self.name == 'tectonic':
            # `--untrusted` disables \write18 and reading files outside the
            # input directory. The document needs neither, and this is the only
            # place the site runs an external program on content that came from
            # the database, so the restriction is worth having.
            return [
                self.path, '--chatter', 'minimal', '--untrusted',
                '--outdir', str(output_dir), str(source_path),
            ]
        return [
            self.path, '-interaction=nonstopmode', '-halt-on-error',
            '-file-line-error', f'-output-directory={output_dir}', str(source_path),
        ]


def _engine_name_for(binary_name):
    name = Path(binary_name).name.lower()
    for known in ('tectonic', 'pdflatex', 'xelatex', 'lualatex'):
        if name.startswith(known):
            return known
    return name


def _resolve_engine(spec):
    """Turn a name or a path into an engine, or None when it is not there."""
    if not spec:
        return None
    if os.sep in spec:
        path = Path(spec)
        if path.is_file() and os.access(path, os.X_OK):
            return LatexEngine(name=_engine_name_for(path.name), path=str(path))
        return None
    found = shutil.which(spec)
    if found:
        return LatexEngine(name=_engine_name_for(spec), path=found)
    return None


def find_engine():
    """Locate the LaTeX engine to use, or return None.

    ``RESUME_LATEX_ENGINE`` wins when set — either a binary name or a full path.
    Otherwise the project's own downloaded Tectonic binary is preferred, so the
    same command works on a developer machine, in CI and on the Pi without a
    system LaTeX install; a system pdflatex is equally acceptable.
    """
    explicit = _resolve_engine(settings.RESUME_LATEX_ENGINE)
    if explicit:
        return explicit

    local_tectonic = Path(settings.BASE_DIR) / '.latex' / 'tectonic'
    candidates = [str(local_tectonic)] + list(_SYSTEM_ENGINES)
    for candidate in candidates:
        engine = _resolve_engine(candidate)
        if engine:
            return engine
    return None


_SYSTEM_ENGINES = ('tectonic', 'pdflatex', 'xelatex', 'lualatex')


def engine_available():
    """Whether a CV can be generated here. Used to decide whether to offer it."""
    return find_engine() is not None


def _compile_environment():
    """Environment for the engine process.

    Tectonic keeps the support files it downloads in a cache under the user's
    home directory. The systemd unit that runs the site makes /home read-only
    apart from the project directory, so the location has to be overridable:
    point ``RESUME_LATEX_CACHE_DIR`` at a writable directory (the prune service
    already documents /var/lib/portfolio as the writable one).
    """
    environment = os.environ.copy()
    if settings.RESUME_LATEX_CACHE_DIR:
        environment['TECTONIC_CACHE_DIR'] = settings.RESUME_LATEX_CACHE_DIR
    return environment


def compile_latex(source, engine=None):
    """Compile LaTeX source and return the PDF as bytes.

    Runs in a scratch directory of its own, so two compiles cannot tread on each
    other's aux files and nothing is left behind in the project.
    """
    engine = engine or find_engine()
    if engine is None:
        raise LatexEngineMissing(
            'No LaTeX engine found. Run scripts/install-latex.sh, or set '
            'RESUME_LATEX_ENGINE to a pdflatex/tectonic binary.'
        )

    with tempfile.TemporaryDirectory(prefix='resume-') as workdir:
        workdir = Path(workdir)
        source_path = workdir / 'resume.tex'
        source_path.write_text(source, encoding='utf-8')

        try:
            result = subprocess.run(
                engine.command(source_path, workdir),
                cwd=workdir,
                env=_compile_environment(),
                capture_output=True,
                text=True,
                timeout=settings.RESUME_LATEX_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise LatexCompileError(
                f'{engine.name} did not finish within '
                f'{settings.RESUME_LATEX_TIMEOUT}s.'
            ) from exc
        except OSError as exc:
            raise LatexCompileError(f'Could not run {engine.path}: {exc}') from exc

        output = workdir / 'resume.pdf'
        if result.returncode != 0 or not output.is_file():
            log = (result.stderr or '') + (result.stdout or '')
            raise LatexCompileError(
                f'{engine.name} failed with exit code {result.returncode}.',
                log=log[-4000:],
            )

        return output.read_bytes()


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def _cache_key(source, engine):
    digest = hashlib.sha256(source.encode('utf-8')).hexdigest()
    return f'resume-pdf:{engine.name}:{digest}'


def _lock_key():
    return 'resume-pdf:lock'


def generate_pdf(*, use_cache=True, force=False):
    """Produce the CV PDF, compiling only when the cache cannot answer.

    The cache key is a hash of the exact LaTeX source, plus the engine name, so
    editing any content or the template produces a different key and the stale
    entry is simply never read again. No invalidation logic is needed, and a
    compile can never serve a PDF built from older content.

    A cache miss takes the compile lock. A second request arriving during a
    compile waits for the first one's result — on the production Pi three
    simultaneous pdflatex processes would be a memory problem, and the waiting
    request gets the PDF rather than an error.

    ``force`` recompiles even when a cached PDF exists, which ``build_resume``
    uses to refresh the cache without waiting for its contents to change.
    """
    engine = find_engine()
    if engine is None:
        raise LatexEngineMissing(
            'No LaTeX engine found. Run scripts/install-latex.sh, or set '
            'RESUME_LATEX_ENGINE to a pdflatex/tectonic binary.'
        )

    source = build_latex_source()
    key = _cache_key(source, engine)

    if not use_cache:
        return compile_latex(source, engine=engine)

    if not force:
        cached = cache.get(key)
        if cached is not None:
            return cached

    # cache.add() is atomic on the file-based cache, so only one request gets
    # True and runs the engine. The TTL is a backstop: if a worker is killed
    # mid-compile the lock would otherwise never be released.
    holds_lock = cache.add(
        _lock_key(), 'compiling', timeout=settings.RESUME_LATEX_TIMEOUT + 30
    )
    if not holds_lock:
        pdf = _wait_for_compile(key, deadline=settings.RESUME_LATEX_WAIT_SECONDS)
        if pdf is not None:
            return pdf
        # Deliberately no second compile here. On production the site runs a
        # single synchronous worker with a 30s Gunicorn timeout, so waiting and
        # then compiling could run two long operations inside one request and
        # have the worker killed — taking the whole site down with it.
        raise LatexCompileError(
            'The CV is already being rebuilt. Please try again in a moment.'
        )

    try:
        pdf = compile_latex(source, engine=engine)
        cache.set(key, pdf, timeout=settings.RESUME_LATEX_CACHE_SECONDS)
        return pdf
    finally:
        cache.delete(_lock_key())


def _wait_for_compile(key, deadline, interval=0.5):
    """Wait up to `deadline` seconds for the in-flight compile's result.

    Polls the cache rather than blocking on a real lock, so a worker that dies
    mid-compile cannot hold a request open indefinitely. Returns None when the
    deadline passes or the lock disappears without a result.
    """
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        time.sleep(interval)
        cached = cache.get(key)
        if cached is not None:
            return cached
        if not cache.get(_lock_key()):
            # The holder is gone without leaving a result.
            return None
    return None
