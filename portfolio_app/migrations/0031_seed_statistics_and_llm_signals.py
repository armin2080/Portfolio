"""Add rules for the statistics and language-model side of the work.

The consolidated skill set has labels that the original rules could not produce:
Statistical Modelling and AI & Language Models. These rules give the sync
something concrete to match so those labels are suggested by evidence rather
than needing to be added by hand.

Also retargets the front-end rules: TypeScript and JavaScript now suggest Web
Development rather than creating skills of their own, and a Dockerfile language
suggests Cloud & Deployment instead of a bare "Docker".
"""

from django.db import migrations

NEW_SIGNALS = [
    # Statistical modelling — the work behind the Bayesian and MCMC projects.
    ('dependency', 'pymc', 'Statistical Modelling', 'Probabilistic programming'),
    ('dependency', 'statsmodels', 'Statistical Modelling', ''),
    ('dependency', 'scipy', 'Statistical Modelling', ''),
    ('dependency', 'patsy', 'Statistical Modelling', ''),
    # Visualisation belongs with analysis.
    ('dependency', 'seaborn', 'Data Analysis & Visualisation', ''),
    ('dependency', 'plotly', 'Data Analysis & Visualisation', ''),
    ('dependency', 'streamlit', 'Data Analysis & Visualisation', 'Dashboard'),
    # Language-model work is its own story now.
    ('dependency', 'langchain', 'AI & Language Models', ''),
    ('dependency', 'transformers', 'AI & Language Models', ''),
    ('dependency', 'huggingface-hub', 'AI & Language Models', ''),
    ('dependency', 'anthropic', 'AI & Language Models', ''),
    ('dependency', 'sentence-transformers', 'AI & Language Models', ''),
    # Deep learning implies machine learning and often vision.
    ('dependency', 'torchvision', 'Computer Vision', ''),
    ('dependency', 'keras', 'Machine Learning', ''),
    # Web development covers the front end as well as Django.
    ('language', 'TypeScript', 'Web Development', ''),
    ('language', 'JavaScript', 'Web Development', ''),
    ('dependency', 'vite', 'Web Development', ''),
    ('dependency', 'next', 'Web Development', ''),
    # A container language is the deployment story, not a skill of its own.
    ('language', 'Dockerfile', 'Cloud & Deployment', ''),
]

# Targets that previously created a skill of their own and should now fold in.
RETARGET = [
    ('language', 'TypeScript', 'TypeScript', 'Web Development'),
    ('language', 'JavaScript', 'JavaScript', 'Web Development'),
    ('language', 'Dockerfile', 'Docker', 'Cloud & Deployment'),
    ('dependency', 'torchvision', 'Machine Learning', 'Computer Vision'),
]


def add_signals(apps, schema_editor):
    SkillSignal = apps.get_model('portfolio_app', 'SkillSignal')

    for kind, pattern, old_name, new_name in RETARGET:
        SkillSignal.objects.filter(
            kind=kind, pattern=pattern, skill_name=old_name
        ).update(skill_name=new_name)

    for kind, pattern, skill_name, note in NEW_SIGNALS:
        SkillSignal.objects.get_or_create(
            kind=kind,
            pattern=pattern,
            skill_name=skill_name,
            defaults={'note': note},
        )


def remove_signals(apps, schema_editor):
    SkillSignal = apps.get_model('portfolio_app', 'SkillSignal')

    for kind, pattern, skill_name, _note in NEW_SIGNALS:
        SkillSignal.objects.filter(
            kind=kind, pattern=pattern, skill_name=skill_name
        ).delete()

    for kind, pattern, old_name, new_name in RETARGET:
        SkillSignal.objects.filter(
            kind=kind, pattern=pattern, skill_name=new_name
        ).update(skill_name=old_name)


class Migration(migrations.Migration):

    dependencies = [
        ('portfolio_app', '0030_consolidate_skill_set'),
    ]

    operations = [
        migrations.RunPython(add_signals, remove_signals),
    ]
