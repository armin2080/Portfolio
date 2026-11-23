"""Build the CV as a PDF from the content shown on the site.

The resume page and the CV share one source of truth. Everything the CV shows —
the header, the summary, experience, education, the projects, the skill tree and
the certificates — is read from the database when a visitor asks for the file, so
editing the site is what changes the CV. There is no uploaded PDF to keep in
step with the content any more.

Two ways to ask for one:

* ``/resume/download/`` — the site's own selection: its most relevant content,
  sized to one page. This is the "Download CV" button.
* ``/resume/generator/`` — the same document, built from a :class:`ResumeOptions`
  the visitor chooses (which roles, projects, skill categories and certificates).
  The page opens on the button's selection, so it starts from the recommendation
  rather than an empty form.

Either way the document is meant to be **one A4 page**. There is more content on
the site than fits on one, so ``_select`` and the ``RESUME_MAX_*`` settings decide
what goes in, and long descriptions are shortened — preferably at a sentence
boundary — rather than spilling over. A compile costs ~20s on the production
server and holds the site's only worker, so this is a fixed budget rather than a
"compile, measure, retry" loop; ``generate_pdf`` logs a warning if the result is
still longer than a page.

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

On a low-powered production server the compile is the expensive part, so
repeated downloads of unchanged content are served from the cache, and only one
compile runs at a time (a second request during a compile waits for it rather
than starting a competing LaTeX process on a small machine).
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

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX only; Windows falls back below
    fcntl = None

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


def shorten(text, limit):
    """Shorten one line to about `limit` characters, preferring a clean ending.

    The CV has to fit one page, and a description imported from GitHub can run to
    several hundred characters. Cutting mid-clause reads as a broken sentence on
    a document whose whole job is to look considered, so this prefers to stop
    after a complete sentence and only falls back to a word boundary (with an
    ellipsis, so it is visible that something was left out). A sentence that ends
    less than halfway through the allowance is ignored — a three-word fragment
    would be worse than the ellipsis.

    The limit is applied before escaping, so it counts characters a reader sees
    rather than LaTeX markup.
    """
    if len(text) <= limit:
        return text

    head = text[:limit]
    for stop in ('. ', '! ', '? '):
        cut = head.rfind(stop)
        if cut > limit / 2:
            return head[:cut + 1].rstrip()

    cut = head.rsplit(' ', 1)[0].rstrip(' ,;:-')
    return f'{cut}\u2026'


def description_lines(text, limit=None, max_lines=None):
    """Split a stored description into bullet-sized lines.

    Returns already-escaped lines. Empty lines and the bullet markers the author
    typed are dropped, so both a blank line and a ``- item`` render as one CV
    bullet.

    ``limit`` shortens each line and ``max_lines`` keeps only the first few: the
    opening lines of a description carry the point, and the later ones are what
    push the CV onto a second page.
    """
    lines = []
    for raw in (text or '').splitlines():
        line = raw.strip()
        while line[:1] in _BULLET_PREFIXES:
            line = line[1:].strip()
        if not line:
            continue
        if limit:
            line = shorten(line, limit)
        lines.append(latex_escape(line))
    if max_lines is not None:
        return lines[:max_lines]
    return lines


# ---------------------------------------------------------------------------
# Reading the site's content
# ---------------------------------------------------------------------------


def _contact_entries(profile):
    """The header contact line, as (label, hyperlink) pairs.

    An entry with no URL is rendered as plain text — the location and the phone
    number — which is why this returns pairs rather than a single string.

    An entry needs *both* a label and (for a link) a URL. Getting that wrong is
    easy and invisible: ``f'mailto:{profile.email}'`` is the truthy string
    'mailto:' when there is no email, which produced an entry with an empty
    label and left a stray "|" in the header.
    """
    entries = []

    def add_text(value):
        if value:
            entries.append({'label': latex_escape(value), 'url': ''})

    def add_link(label, url):
        if label and url:
            entries.append({
                'label': latex_escape(label),
                'url': latex_escape_url(url),
            })

    if profile:
        add_text(profile.location)
        add_link(profile.email, f'mailto:{profile.email}' if profile.email else '')
        add_text(profile.phone)
        add_link('LinkedIn', profile.linkedin_url)
        add_link('GitHub', profile.github_url)

    add_link('Portfolio', settings.SITE_URL)
    return entries


# ---------------------------------------------------------------------------
# What goes into the CV
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResumeOptions:
    """Which of the site's content a particular CV contains.

    Each id collection is tri-state, and the difference is the whole point:

    * ``None`` — nothing was chosen, so the site decides: its most relevant rows,
      already within the one-page budget. This is what the plain "Download CV"
      button asks for, and what the generator form starts from.
    * an empty tuple — that section was deliberately cleared, so it is left out.
    * a non-empty tuple — exactly those rows, still capped by the budget, since
      the document has to fit one page whatever is asked for.

    Ids are never trusted by themselves. The form resolves them against
    querysets (see ``forms.ResumeOptionsForm``), so an id belonging to an
    unpublished project, or to a different model, fails validation rather than
    reaching a query.
    """

    experience_ids: tuple = None
    education_ids: tuple = None
    project_ids: tuple = None
    skill_ids: tuple = None
    certificate_ids: tuple = None
    include_summary: bool = True
    include_languages: bool = True


def _select(queryset, ids, limit, *, field=None):
    """One section's rows: an explicit choice, or the site's own priority.

    The queryset supplies the order (newest first, or the admin's display
    order), and the cap is what keeps the CV to a single page no matter what is
    selected. The generator page states the caps, so a selection that exceeds one
    is trimmed visibly rather than silently.

    ``field`` names the section, and is only used to apply
    :data:`_PREFER_DESCRIBED` to the *default* selection. An explicit choice is
    always honoured as given.
    """
    if ids is not None:
        return list(queryset.filter(pk__in=ids)[:limit])

    rows = list(queryset)
    if field in _PREFER_DESCRIBED:
        describable = [row for row in rows if row.description.strip()]
        rest = [row for row in rows if not row.description.strip()]
        # Order inside each group is untouched, so this is still newest-first;
        # it just stops a bare name taking a slot from a project that can be
        # explained.
        rows = describable + rest
    return rows[:limit]


# Sections whose default selection prefers rows that can actually be described.
#
# A work entry with no bullets still reads fine — a role and its dates say
# something on their own — but a project with no bullets prints a name with
# nothing under it, which reads as an unfinished CV. Nearly every project on
# this site has a description, so preferring those costs nothing and produces a
# visibly better page. An explicit selection is never reordered.
_PREFER_DESCRIBED = frozenset({'projects'})


# The selectable rows, one function per section. The generator's form uses these
# same querysets to validate what it is sent, so "what the form accepts" and
# "what the CV can contain" cannot drift apart — an id for an unpublished
# project, or for a different model, is rejected by validation rather than
# reaching a query.


def selectable_experience():
    return WorkExperience.objects.all()


def selectable_education():
    return Education.objects.all()


def selectable_projects():
    return Project.objects.filter(is_published=True).prefetch_related('skills_used__parent')


def selectable_skills():
    return Skill.objects.filter(is_published=True, parent__isnull=True)


def selectable_certificates():
    return Certificate.objects.all()


SELECTABLE = {
    'experiences': (selectable_experience, 'RESUME_MAX_EXPERIENCE'),
    'educations': (selectable_education, 'RESUME_MAX_EDUCATION'),
    'projects': (selectable_projects, 'RESUME_MAX_PROJECTS'),
    'skills': (selectable_skills, 'RESUME_MAX_SKILLS'),
    'certificates': (selectable_certificates, 'RESUME_MAX_CERTIFICATES'),
}


def default_selection():
    """The selection the plain "Download CV" button uses.

    Used as the generator form's initial state, so the page opens on the same CV
    the button produces — the recommendation — and the visitor only changes what
    they want to differ. Includes the two on/off sections, not just the ids, so
    the checkboxes start in the right state too.
    """
    selection = {
        field: [
            row.pk
            for row in _select(queryset(), None, getattr(settings, cap), field=field)
        ]
        for field, (queryset, cap) in SELECTABLE.items()
    }
    selection['include_summary'] = True
    selection['include_languages'] = True
    return selection


def budget_caps():
    """The per-section caps, for the generator page to state them."""
    return {field: getattr(settings, cap) for field, (_queryset, cap) in SELECTABLE.items()}


def _experience_entries(options):
    rows = _select(
        selectable_experience(),
        options.experience_ids,
        settings.RESUME_MAX_EXPERIENCE,
        field='experiences',
    )
    return [
        {
            'company': latex_escape(experience.company),
            'role': latex_escape(experience.role),
            'location': latex_escape(experience.location),
            'dates': format_date_range(experience.start_date, experience.end_date),
            'bullets': description_lines(
                experience.description,
                limit=settings.RESUME_MAX_BULLET_CHARS,
                max_lines=settings.RESUME_MAX_EXPERIENCE_BULLETS,
            ),
        }
        for experience in rows
    ]


def _education_entries(options):
    rows = _select(
        selectable_education(),
        options.education_ids,
        settings.RESUME_MAX_EDUCATION,
        field='educations',
    )
    return [
        {
            'institution': latex_escape(education.institution),
            'degree': latex_escape(education.degree),
            # Education has no location on older rows; the template omits an
            # empty one rather than printing a stray gap.
            'location': latex_escape(education.location),
            'dates': format_date_range(education.start_date, education.end_date),
            'bullets': description_lines(
                education.description,
                limit=settings.RESUME_MAX_BULLET_CHARS,
                max_lines=settings.RESUME_MAX_EDUCATION_BULLETS,
            ),
        }
        for education in rows
    ]


def _project_entries(options):
    """The selected projects, with the technologies each one used."""
    rows = _select(
        selectable_projects(),
        options.project_ids,
        settings.RESUME_MAX_PROJECTS,
        field='projects',
    )
    return [
        {
            'name': latex_escape(project.name),
            'link': latex_escape_url(project.link),
            # A project can carry a dozen tags. On the site they are a browsing
            # aid; on a CV they sit in the heading, where a long list wraps to
            # three lines and dwarfs the entry. The site's own order is kept.
            'technologies': ', '.join(
                latex_escape(skill.name)
                for skill in project.display_skills()[: settings.RESUME_MAX_PROJECT_TECHNOLOGIES]
            ),
            'bullets': description_lines(
                project.description,
                limit=settings.RESUME_MAX_BULLET_CHARS,
                max_lines=settings.RESUME_MAX_PROJECT_BULLETS,
            ),
        }
        for project in rows
    ]


def _skill_rows(options):
    """Main skills paired with their sub-skills, both published.

    The two-level tree already groups technologies the way a CV wants them — the
    main skill is the heading and the sub-skills are the technologies under it —
    so the tree *is* the categorisation, and the generator's "choose categories"
    step picks from these main skills. No second, hand-written taxonomy exists to
    drift out of step with the site.
    """
    main_skills = _select(
        selectable_skills(),
        options.skill_ids,
        settings.RESUME_MAX_SKILLS,
        field='skills',
    )
    subskills_by_parent = {}
    if main_skills:
        for subskill in (
            Skill.objects.filter(
                is_published=True, parent__in=[skill.pk for skill in main_skills],
            ).order_by('display_order', 'start_date', 'name')
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
    """Every skill name, for the PDF's keyword metadata.

    Taken from the rows actually rendered, so a CV for a specific application
    does not advertise skills it left out.
    """
    names = [skill.name for skill, _subskills in rows]
    names += [subskill.name for _skill, subskills in rows for subskill in subskills]
    return latex_escape(', '.join(names))


def _certificate_entries(options):
    """The selected certificates, newest first (see ``Certificate.Meta``)."""
    rows = _select(
        selectable_certificates(),
        options.certificate_ids,
        settings.RESUME_MAX_CERTIFICATES,
        field='certificates',
    )
    return [
        {
            'name': latex_escape(certificate.name),
            'institution': latex_escape(certificate.institution),
            'year': certificate.issue_date.year,
        }
        for certificate in rows
    ]


def build_resume_context(options=None):
    """Everything the template needs, with every value already LaTeX-escaped.

    ``options`` is what ``ResumeOptions()`` means by default: the site's own
    selection, sized to fit one page.
    """
    options = options or ResumeOptions()
    profile = Profile.objects.first()
    skill_rows = _skill_rows(options)
    languages = [
        {'name': latex_escape(name), 'level': latex_escape(level)}
        for name, level in LANGUAGES
    ] if options.include_languages else []
    return {
        'name': latex_escape(profile.name if profile else ''),
        'title': latex_escape(profile.title.upper() if profile and profile.title else ''),
        'contact': _contact_entries(profile),
        'summary': (
            latex_escape(profile.professional_summary or '') if profile else ''
        ) if options.include_summary else '',
        'keywords': _skill_keywords(skill_rows),
        'experiences': _experience_entries(options),
        'educations': _education_entries(options),
        'projects': _project_entries(options),
        'skills': _skill_entries(skill_rows),
        'languages': languages,
        'certificates': _certificate_entries(options),
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


def build_latex_source(options=None):
    """Convenience wrapper: options -> LaTeX source."""
    return render_latex(build_resume_context(options))


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
            # `--keep-logs` keeps resume.log, which is where the engine records
            # "Output written on ... (N pages)" — how the page count is read
            # back without a PDF library.
            return [
                self.path, '--chatter', 'minimal', '--untrusted', '--keep-logs',
                '--outdir', str(output_dir), str(source_path),
            ]
        # pdflatex writes its log next to the output by default.
        # -no-shell-escape is explicit rather than relying on the TeX Live
        # default: the document is built from database content, and an engine
        # that can run commands is not worth the risk. Tectonic gets the
        # equivalent --untrusted above.
        return [
            self.path, '-no-shell-escape', '-interaction=nonstopmode',
            '-halt-on-error', '-file-line-error',
            f'-output-directory={output_dir}', str(source_path),
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
    same command works on a developer machine, in CI and on the server without a
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

    Deliberately a small allow-list rather than ``os.environ.copy()``. The
    engine is a large third-party program being fed content that originated in
    the database; it has no business seeing ``SECRET_KEY``,
    ``EMAIL_HOST_PASSWORD`` or ``GITHUB_TOKEN``. Only the variables a TeX
    distribution actually needs to find its binaries and configuration are
    passed through.

    Tectonic keeps the support files it downloads in a cache under the user's
    home directory. The systemd unit that runs the site makes /home read-only
    apart from the project directory, so the location has to be overridable:
    point ``RESUME_LATEX_CACHE_DIR`` at a writable directory (the prune service
    already documents /var/lib/portfolio as the writable one).
    """
    allowed = ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LANGUAGE', 'LC_ALL',
               'LC_CTYPE', 'TZ', 'SOURCE_DATE_EPOCH')
    environment = {name: os.environ[name] for name in allowed if name in os.environ}
    environment.setdefault('PATH', os.defpath)

    if settings.RESUME_LATEX_CACHE_DIR:
        environment['TECTONIC_CACHE_DIR'] = settings.RESUME_LATEX_CACHE_DIR
    return environment


@dataclass(frozen=True)
class Compilation:
    """A compiled document, and how many pages it came to.

    The page count is read from the engine's own log rather than by parsing the
    PDF: xdvipdfmx writes PDF 1.5, which hides the page tree inside a compressed
    object stream, so counting it from the bytes needs either a PDF library (a
    dependency, and something else to keep current on the server) or a hand-written
    object-stream parser. The engine already knows, and says so in the log.
    """

    pdf: bytes
    pages: int


# "Output written on resume.xdv (2 pages, 57664 bytes)."
_PAGES_RE = re.compile(r'Output written on .*?\((\d+)\s+pages?')


def _pages_from_log(workdir):
    """Pages the engine reported, defaulting to 1 (a CV is normally one page)."""
    log = Path(workdir) / 'resume.log'
    try:
        text = log.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return 1
    match = _PAGES_RE.search(text)
    return int(match.group(1)) if match else 1


def compile_latex(source, engine=None):
    """Compile LaTeX source and return a :class:`Compilation`.

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

        return Compilation(pdf=output.read_bytes(), pages=_pages_from_log(workdir))


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


# Bumped whenever the cached value's shape changes. Without it, an entry written
# by the previous release (which cached bare PDF bytes) would be read back as the
# new dict and fail with an AttributeError on the first download after a deploy
# — the key is derived from the source, so it does not change on its own.
_CACHE_VERSION = 'v2'


def _cache_key(source, engine):
    digest = hashlib.sha256(source.encode('utf-8')).hexdigest()
    return f'resume-pdf:{_CACHE_VERSION}:{engine.name}:{digest}'


# The compile lock is an OS advisory lock, not a cache entry. Django's
# FileBasedCache implements add() as has_key() followed by set(), which is not
# atomic: two processes can both think they acquired it and start two engines at
# once, which is exactly the memory problem the lock exists to prevent. flock()
# is released by the kernel when the holder dies, so a killed worker cannot
# leave a lock behind either.
# The uid keeps a second local account from pre-creating a mode-0600 file at
# this path in a shared /tmp and making every compile wait on a lock it did not
# take. The service itself has a private /tmp (PrivateTmp=true), so this only
# matters on a development machine.
_LOCK_FILENAME = (
    f'portfolio-resume-compile-{getattr(os, "getuid", lambda: 0)()}.lock'
)
_LOCK_FALLBACK_KEY = 'resume-pdf:lock'
_BUDGET_KEY = 'resume-pdf:budget'


def _lock_path():
    return Path(tempfile.gettempdir()) / _LOCK_FILENAME


def _acquire_compile_lock():
    """Return a token for the compile lock, or None if another process holds it."""
    if fcntl is None:  # pragma: no cover - Windows development only
        if cache.add(
            _LOCK_FALLBACK_KEY,
            'compiling',
            timeout=settings.RESUME_LATEX_TIMEOUT + 30,
        ):
            return True
        return None

    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _release_compile_lock(token):
    if token is None:
        return
    if token is True:  # pragma: no cover - Windows development only
        cache.delete(_LOCK_FALLBACK_KEY)
        return
    try:
        fcntl.flock(token, fcntl.LOCK_UN)
    finally:
        os.close(token)


def _compile_lock_is_held():
    """Whether some process is compiling right now."""
    if fcntl is None:  # pragma: no cover - Windows development only
        return bool(cache.get(_LOCK_FALLBACK_KEY))

    token = _acquire_compile_lock()
    if token is None:
        return True
    _release_compile_lock(token)
    return False


def _within_compile_budget():
    """Count this compile against the global hourly ceiling.

    Per-IP limits keep one visitor from occupying the worker; this bounds the
    total work a request spread over many addresses can cause. ``cache.incr``
    is not cross-process atomic on Django's file cache, so with more than one
    worker the count can drift low — it is a backstop, not a security boundary.
    """
    limit = settings.RESUME_LATEX_MAX_COMPILES_PER_HOUR
    if limit <= 0:
        return True

    try:
        used = cache.incr(_BUDGET_KEY)
    except ValueError:
        # Key not present yet (Django raises ValueError rather than starting at 0).
        cache.set(_BUDGET_KEY, 1, 3600)
        used = 1
    return used <= limit


def _from_entry(entry):
    """Rebuild a :class:`Compilation` from its cached form."""
    if entry is None:
        return None
    return Compilation(pdf=entry['pdf'], pages=entry['pages'])


def generate_pdf(options=None, *, use_cache=True, force=False, enforce_budget=True):
    """Produce the CV PDF, compiling only when the cache cannot answer.

    The cache key is a hash of the exact LaTeX source — which is itself a
    function of the content *and* the chosen ``options`` — plus the engine name,
    so changing the site or selecting different items produces a different key
    and the stale entry is simply never read again. No invalidation logic is
    needed, and a compile can never serve a PDF built from other content.

    A cache miss takes the compile lock. A second request arriving during a
    compile waits for the first one's result — on the production server, three
    simultaneous pdflatex processes would be a memory problem, and the waiting
    request gets the PDF rather than an error.

    ``force`` recompiles even when a cached PDF exists, which ``build_resume``
    uses to refresh the cache without waiting for its contents to change.
    ``enforce_budget`` is disabled by that command: the hourly abuse ceiling is
    for visitor traffic, and must not stop an operator warming the cache after
    a restart.
    """
    engine = find_engine()
    if engine is None:
        raise LatexEngineMissing(
            'No LaTeX engine found. Run scripts/install-latex.sh, or set '
            'RESUME_LATEX_ENGINE to a pdflatex/tectonic binary.'
        )

    source = build_latex_source(options)
    key = _cache_key(source, engine)

    if not use_cache:
        return compile_latex(source, engine=engine)

    if not force:
        cached = _from_entry(cache.get(key))
        if cached is not None:
            return cached

    # One compile at a time, enforced with an OS lock that a killed worker
    # cannot leave behind. The TTL-based cache lock this replaced was not
    # actually atomic on Django's file cache.
    lock = _acquire_compile_lock()
    if lock is None:
        pdf = _wait_for_compile(key, deadline=settings.RESUME_LATEX_WAIT_SECONDS)
        if pdf is not None:
            return pdf
        # Deliberately no second compile here. On production the site runs a
        # single synchronous worker, so waiting and then compiling could run two
        # long operations inside one request and have the worker killed — taking
        # the whole site down with it.
        raise LatexCompileError(
            'The CV is already being rebuilt. Please try again in a moment.'
        )

    try:
        if enforce_budget and not force and not _within_compile_budget():
            raise LatexCompileError(
                'The CV has been rebuilt many times in the last hour. '
                'Please try again later.'
            )

        compilation = compile_latex(source, engine=engine)
        if compilation.pages > 1:
            # Not fatal — a longer CV is still a CV — but it means the content
            # budget in settings no longer holds the document to one page, which
            # is the one thing the design promises. Worth knowing before a
            # visitor's download is what reveals it.
            logger.warning(
                'Generated CV runs to %s pages; lower RESUME_MAX_* to bring it '
                'back to one.',
                compilation.pages,
            )
        cache.set(
            key,
            {'pdf': compilation.pdf, 'pages': compilation.pages},
            timeout=settings.RESUME_LATEX_CACHE_SECONDS,
        )
        return compilation
    finally:
        _release_compile_lock(lock)


def _wait_for_compile(key, deadline, interval=0.5):
    """Wait up to `deadline` seconds for the in-flight compile's result.

    Polls the cache rather than blocking on a real lock, so a worker that dies
    mid-compile cannot hold a request open indefinitely. Returns None when the
    deadline passes or the lock disappears without a result.
    """
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        time.sleep(interval)
        cached = _from_entry(cache.get(key))
        if cached is not None:
            return cached
        if not _compile_lock_is_held():
            # The holder is gone without leaving a result.
            return None
    return None
