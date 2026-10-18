"""Measure the generated CV against a database, to tune the one-page budget.

Runs outside Django's test runner so it can use a real database copy:

    DB_NAME=/tmp/prod-db.sqlite3 DEBUG=True SECRET_KEY=x \
        .venv/bin/python scripts/measure-resume.py

Prints the size of each section and the page count the engine reported, so the
caps in settings are tuned against real content instead of guessed at. Exits
non-zero when the document is not one page, so it can gate a deploy.
"""

import os
import sys
from pathlib import Path

import django

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Personal_Portfolio.settings')
django.setup()

from django.conf import settings  # noqa: E402

from portfolio_app import resume_latex as rl  # noqa: E402
from portfolio_app.models import (  # noqa: E402
    Certificate, Education, Profile, Project, Skill, WorkExperience,
)


def main():
    print(f'engine: {rl.find_engine()}')

    available = {
        'experience': WorkExperience.objects.count(),
        'education': Education.objects.count(),
        'projects': Project.objects.filter(is_published=True).count(),
        'skills': Skill.objects.filter(is_published=True, parent__isnull=True).count(),
        'certificates': Certificate.objects.count(),
    }
    budgets = {
        'experience': settings.RESUME_MAX_EXPERIENCE,
        'education': settings.RESUME_MAX_EDUCATION,
        'projects': settings.RESUME_MAX_PROJECTS,
        'skills': settings.RESUME_MAX_SKILLS,
        'certificates': settings.RESUME_MAX_CERTIFICATES,
    }
    print(f'profile: {Profile.objects.first()}')
    print()
    print('--- available vs budget ---')
    for key, count in available.items():
        print(f'  {key:14} {count:3} available, budget {budgets[key]}')
    print()

    context = rl.build_resume_context()
    source = rl.render_latex(context)
    compilation = rl.compile_latex(source)

    print('--- rendered ---')
    for key in ('experiences', 'educations', 'projects', 'skills', 'certificates', 'languages'):
        print(f'  {key:14} {len(context[key]):3} items')
    bullets = sum(
        len(item['bullets'])
        for key in ('experiences', 'educations', 'projects')
        for item in context[key]
    )
    print(f'  {"bullets":14} {bullets:3} across those sections')
    print()
    print(f'PDF: {len(compilation.pdf) / 1024:.1f} KB, {compilation.pages} page(s)')

    Path('/tmp/measure.tex').write_text(source, encoding='utf-8')
    Path('/tmp/measure.pdf').write_bytes(compilation.pdf)
    print('wrote /tmp/measure.tex and /tmp/measure.pdf')

    if compilation.pages != 1:
        print(f'\nNOT ONE PAGE ({compilation.pages}). Lower the RESUME_MAX_* caps.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
