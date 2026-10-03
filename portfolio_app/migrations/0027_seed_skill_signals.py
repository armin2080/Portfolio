"""Seed the default skill-detection rules.

These map things a repository can actually contain onto skill names. Rules whose
skill name does not exist yet (FastAPI, Flask, R, ...) will create that skill
hidden the first time one matches, so a newly noticed technology lands in the
admin for review rather than appearing on the site unannounced.

Everything here is editable in the admin, and nothing is irreversible: deleting a
rule only stops it matching.
"""

from django.db import migrations

# (kind, pattern, skill_name, note)
DEFAULT_SIGNALS = [
    # --- files and folders ------------------------------------------------
    ('path', 'manage.py', 'Django', 'Django project entry point'),
    ('path', 'requirements.txt', 'Python', ''),
    ('path', 'pyproject.toml', 'Python', ''),
    ('path', '*.ipynb', 'Machine Learning', 'Notebook-driven work'),
    ('path', '*.ipynb', 'Jupyter Notebook', ''),
    ('path', '*.sql', 'SQL', ''),
    ('path', 'Dockerfile', 'Cloud & Deployment', ''),
    ('path', 'docker-compose.yml', 'Cloud & Deployment', ''),
    ('path', '.github/workflows/*', 'Cloud & Deployment', 'CI/CD pipeline'),

    # --- packages in dependency files -------------------------------------
    ('dependency', 'django', 'Django', ''),
    ('dependency', 'pandas', 'Pandas', ''),
    ('dependency', 'scikit-learn', 'Scikit-learn', ''),
    ('dependency', 'scikit-learn', 'Machine Learning', ''),
    ('dependency', 'torch', 'Machine Learning', ''),
    ('dependency', 'tensorflow', 'Machine Learning', ''),
    ('dependency', 'torch', 'Artificial Intelligence', ''),
    ('dependency', 'tensorflow', 'Artificial Intelligence', ''),
    ('dependency', 'openai', 'Artificial Intelligence', ''),
    ('dependency', 'groq', 'Artificial Intelligence', 'LLM API client'),
    ('dependency', 'opencv-python', 'Computer Vision', ''),
    ('dependency', 'psycopg2', 'PostgreSQL', ''),
    ('dependency', 'psycopg2', 'SQL', ''),
    ('dependency', 'sqlalchemy', 'SQL', ''),
    ('dependency', 'numpy', 'Data Analysis', ''),
    ('dependency', 'matplotlib', 'Data Analysis', ''),
    ('dependency', 'fastapi', 'FastAPI', 'Created hidden until published'),
    ('dependency', 'flask', 'Flask', 'Created hidden until published'),
    ('dependency', 'react', 'React', 'Created hidden until published'),

    # --- GitHub's reported main language ----------------------------------
    ('language', 'Python', 'Python', ''),
    ('language', 'Jupyter Notebook', 'Machine Learning', ''),
    ('language', 'Shell', 'Linux', ''),
    ('language', 'SQL', 'SQL', ''),
    ('language', 'R', 'R', 'Created hidden until published'),
    ('language', 'Dockerfile', 'Docker', 'Created hidden until published'),
    ('language', 'TeX', 'LaTeX', 'Created hidden until published'),
    ('language', 'TypeScript', 'TypeScript', 'Created hidden until published'),
    ('language', 'JavaScript', 'JavaScript', 'Created hidden until published'),
]


def create_signals(apps, schema_editor):
    SkillSignal = apps.get_model('portfolio_app', 'SkillSignal')
    for kind, pattern, skill_name, note in DEFAULT_SIGNALS:
        SkillSignal.objects.get_or_create(
            kind=kind,
            pattern=pattern,
            skill_name=skill_name,
            defaults={'note': note},
        )


def remove_signals(apps, schema_editor):
    SkillSignal = apps.get_model('portfolio_app', 'SkillSignal')
    for kind, pattern, skill_name, _note in DEFAULT_SIGNALS:
        SkillSignal.objects.filter(
            kind=kind, pattern=pattern, skill_name=skill_name
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('portfolio_app', '0026_project_auto_skills_project_github_pushed_at_and_more'),
    ]

    operations = [
        migrations.RunPython(create_signals, remove_signals),
    ]
