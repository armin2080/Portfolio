"""Give the skills a tree: main skills with the tools underneath them.

The flat list forced a choice between being readable and being specific. "Pandas"
next to "Python" reads as a peer of a language, but removing it loses the detail
that tells a visitor what the work actually involved. A two-level tree lets both
exist: main skills stay at the level a reader thinks in, and the specific
technologies sit underneath the main skill they belong to.

Each main skill gets sub-skills that were previously either removed for being too
specific (Pandas, Scikit-learn, PostgreSQL) or simply missing (PyMC, LangChain,
Docker). A technology is listed under exactly one main skill, so a project tagged
with it rolls up unambiguously.

Nothing is deleted: the existing twelve skills all keep their row, their
description, their dates and their project links. Main skills that match by name
are reused rather than recreated. A sub-skill takes its parent's start date so
the two do not disagree.

Rules in `SkillSignal` are pointed at the sub-skills they can now prove, with
`parent_skill_name` set so a detected technology is created in the right place
instead of landing at the top level.
"""

from django.db import migrations

# main skill -> its sub-skills, in display order
SKILL_TREE = [
    ('Python', ['Jupyter Notebook', 'FastAPI', 'NumPy']),
    ('Machine Learning', ['Scikit-learn', 'PyTorch', 'TensorFlow', 'Keras']),
    ('Statistical Modelling', ['PyMC', 'Statsmodels', 'SciPy', 'Bayesian Inference']),
    ('AI & Language Models', ['LangChain', 'Transformers', 'RAG', 'Prompt Engineering']),
    ('Computer Vision', ['OpenCV', 'torchvision', 'Image Processing']),
    ('Data Analysis & Visualisation', ['Pandas', 'Matplotlib', 'Seaborn', 'Plotly']),
    ('SQL & Databases', ['PostgreSQL', 'SQLite', 'SQLAlchemy']),
    ('Web Development', ['Django', 'REST APIs', 'Tailwind CSS', 'HTML & CSS']),
    ('Cloud & Deployment', ['Docker', 'CI/CD', 'Cloudflare', 'Nginx']),
    ('Linux', ['Shell Scripting', 'systemd', 'Server Administration', 'Raspberry Pi']),
    ('Git', ['GitHub Actions', 'Code Review']),
    ('IT Service Management', ['ITIL', 'Incident Management']),
]

# The order the cards should read in: what the site owner wants to lead with.
MAIN_SKILL_ORDER = [
    'Python',
    'Machine Learning',
    'Statistical Modelling',
    'AI & Language Models',
    'Computer Vision',
    'Data Analysis & Visualisation',
    'SQL & Databases',
    'Web Development',
    'Cloud & Deployment',
    'Linux',
    'Git',
    'IT Service Management',
]

# (kind, pattern, skill_name) -> the sub-skill it should now point at, so that
# detected detail lands under a main skill rather than at the top level.
RULE_RETARGET = {
    ('dependency', 'pandas'): 'Pandas',
    ('dependency', 'numpy'): 'NumPy',
    ('dependency', 'matplotlib'): 'Matplotlib',
    ('dependency', 'seaborn'): 'Seaborn',
    ('dependency', 'plotly'): 'Plotly',
    ('dependency', 'scikit-learn'): 'Scikit-learn',
    ('dependency', 'torch'): 'PyTorch',
    ('dependency', 'tensorflow'): 'TensorFlow',
    ('dependency', 'keras'): 'Keras',
    ('dependency', 'torchvision'): 'torchvision',
    ('dependency', 'opencv-python'): 'OpenCV',
    ('dependency', 'pymc'): 'PyMC',
    ('dependency', 'statsmodels'): 'Statsmodels',
    ('dependency', 'scipy'): 'SciPy',
    ('dependency', 'langchain'): 'LangChain',
    ('dependency', 'transformers'): 'Transformers',
    ('dependency', 'sentence-transformers'): 'Transformers',
    ('dependency', 'huggingface-hub'): 'Transformers',
    ('dependency', 'psycopg2'): 'PostgreSQL',
    ('dependency', 'sqlalchemy'): 'SQLAlchemy',
    ('dependency', 'django'): 'Django',
    ('dependency', 'fastapi'): 'FastAPI',
    ('dependency', 'vite'): 'REST APIs',
    ('language', 'Jupyter Notebook'): 'Jupyter Notebook',
    ('language', 'Dockerfile'): 'Docker',
    ('path', '*.ipynb'): 'Jupyter Notebook',
    ('path', 'manage.py'): 'Django',
    ('path', '.github/workflows/*'): 'CI/CD',
}


def build_tree(apps, schema_editor):
    Skill = apps.get_model('portfolio_app', 'Skill')
    SkillSignal = apps.get_model('portfolio_app', 'SkillSignal')

    # 1. Every main skill that does not exist yet. IT Service Management and the
    #    rest of the old set are already there and are reused by name.
    for order, name in enumerate(MAIN_SKILL_ORDER):
        skill, created = Skill.objects.get_or_create(
            name=name,
            parent=None,
            defaults={
                'start_date': _template_date(Skill),
                'display_order': order * 10,
            },
        )
        if not created and skill.display_order == 0:
            skill.display_order = order * 10
            skill.save(update_fields=['display_order'])

    parents = {s.name: s for s in Skill.objects.filter(parent=None)}

    # 2. Sub-skills, attached to their main skill.
    for parent_name, subskill_names in SKILL_TREE:
        parent = parents.get(parent_name)
        if parent is None:
            continue
        for order, name in enumerate(subskill_names):
            skill, created = Skill.objects.get_or_create(
                name=name,
                defaults={
                    'parent': parent,
                    # Inherit the parent's dates: keeping a separate history for
                    # each tool would be noise.
                    'start_date': parent.start_date,
                    'display_order': order * 10,
                },
            )
            if not created and skill.parent_id != parent.pk:
                skill.parent = parent
                skill.save(update_fields=['parent'])

    # 3. Point the detection rules at the sub-skills they can now prove.
    #
    # Several rules can share a (kind, pattern) pair — `torch` used to imply both
    # Machine Learning and Artificial Intelligence — and they now all point at
    # one sub-skill. `skill_name` is part of a unique constraint, so the extras
    # are deleted rather than updated into a collision.
    subskills = {s.name: s for s in Skill.objects.filter(parent__isnull=False)}
    for (kind, pattern), skill_name in RULE_RETARGET.items():
        target = subskills.get(skill_name)
        if target is None:
            continue
        signals = list(
            SkillSignal.objects.filter(kind=kind, pattern=pattern).order_by('pk')
        )
        if not signals:
            continue
        # Keep the row that already names the target if there is one, so its
        # note and active flag survive.
        keep = next((s for s in signals if s.skill_name == skill_name), signals[0])
        for signal in signals:
            if signal.pk != keep.pk:
                signal.delete()
        keep.skill_name = skill_name
        keep.parent_skill_name = target.parent.name
        keep.save(update_fields=['skill_name', 'parent_skill_name'])


def _template_date(Skill):
    """A sane start date for a main skill that does not exist yet.

    The earliest date already in use, so a newly added main skill does not claim
    more experience than the skills it sits beside.
    """
    earliest = (
        Skill.objects.filter(parent=None)
        .order_by('start_date')
        .values_list('start_date', flat=True)
        .first()
    )
    return earliest or '2020-01-01'


def remove_tree(apps, schema_editor):
    """Detach the sub-skills this migration added, and retarget the rules back.

    The sub-skill rows themselves are left in place: they may since have been
    tagged or linked, and dropping rows on a reverse migration would lose that.
    """
    Skill = apps.get_model('portfolio_app', 'Skill')
    SkillSignal = apps.get_model('portfolio_app', 'SkillSignal')

    for parent_name, subskill_names in SKILL_TREE:
        Skill.objects.filter(name__in=subskill_names, parent__name=parent_name).update(
            parent=None
        )

    for (kind, pattern), skill_name in RULE_RETARGET.items():
        SkillSignal.objects.filter(kind=kind, pattern=pattern).update(
            parent_skill_name=''
        )


class Migration(migrations.Migration):

    dependencies = [
        ('portfolio_app', '0033_skill_hierarchy'),
    ]

    operations = [
        migrations.RunPython(build_tree, remove_tree),
    ]
