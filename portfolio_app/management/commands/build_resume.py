"""Build the CV now, instead of waiting for a visitor to ask for it.

The CV is generated on demand (see ``portfolio_app.resume_latex``), so the first
download after the content changes has to pay for the LaTeX compile. On a
low-powered production server that is the awkward part: a single synchronous
Gunicorn worker serves the whole site with a 30-second timeout, so a long
compile can have the worker killed — taking the site down, not just the download.

Running this command moves that cost off the request path. The PDF is built here
and left in the cache, so the visitor's download is a cache hit and the site
never spends a request compiling.

    python manage.py build_resume                 # build and cache
    python manage.py build_resume --force         # recompile, refresh the cache
    python manage.py build_resume --out CV.pdf    # also write a copy
    python manage.py build_resume --tex CV.tex    # also write the LaTeX source

``--tex`` is the useful one when tweaking the design: it writes exactly what the
engine is given, so a LaTeX error can be looked at directly.
"""

import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from portfolio_app import resume_latex


class Command(BaseCommand):
    help = 'Render the CV from the site content and compile it to PDF.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--out',
            metavar='PATH',
            help='Also write the generated PDF to this path.',
        )
        parser.add_argument(
            '--tex',
            metavar='PATH',
            help=(
                'Also write the generated LaTeX source to this path. Useful for '
                'inspecting what the engine was given when something will not '
                'compile.'
            ),
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help=(
                'Recompile even when a cached PDF for this content exists, and '
                'replace it. Without this the command is cheap to run: it only '
                'compiles when the content has actually changed.'
            ),
        )

    def handle(self, *args, **options):
        engine = resume_latex.find_engine()
        if engine is None:
            raise CommandError(
                'No LaTeX engine found. Run scripts/install-latex.sh, or set '
                'RESUME_LATEX_ENGINE to a pdflatex or tectonic binary.'
            )

        self.stdout.write(f'Using {engine.name} ({engine.path})')

        if options['tex']:
            source = resume_latex.build_latex_source()
            self._write(options['tex'], source, 'LaTeX source')

        started = time.monotonic()
        try:
            compilation = resume_latex.generate_pdf(
                force=options['force'], enforce_budget=False,
            )
        except resume_latex.LatexCompileError as exc:
            # The engine's own output is the only useful thing here; without it
            # a failed compile is just "it did not work".
            if exc.log:
                self.stderr.write(exc.log)
            raise CommandError(str(exc)) from exc
        elapsed = time.monotonic() - started

        self.stdout.write(
            self.style.SUCCESS(
                f'Built a {len(compilation.pdf) / 1024:.1f} KB PDF in {elapsed:.1f}s, '
                f'{compilation.pages} page(s)'
            )
        )

        if options['out']:
            self._write(options['out'], compilation.pdf, 'PDF')

    def _write(self, path, content, label):
        target = Path(path)
        try:
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding='utf-8')
        except OSError as exc:
            raise CommandError(f'Could not write {label} to {target}: {exc}') from exc
        self.stdout.write(f'Wrote {label} to {target}')
