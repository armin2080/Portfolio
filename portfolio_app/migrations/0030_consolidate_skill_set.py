"""Consolidate the skills into a portfolio-level set.

The previous list mixed four levels of abstraction — languages (Python, SQL),
libraries (Pandas, Scikit-learn), tools (Git, PostgreSQL) and disciplines
(Machine Learning, Data Analysis) — which makes it impossible to read what level
of work is being claimed. Matching pandas against Python implies pandas is a peer
of a language rather than a library of one.

The set below is deliberately breadth-over-depth in the *label* and specific in
the *evidence*: "Data Analysis & Visualisation" is the term a reader searches
for, while pandas, numpy and matplotlib belong in project descriptions and in
the repository itself.

This migration is safe to re-run in the sense that matters: project links are
carried across before the old skill is removed, and a target that does not exist
yet is created by *renaming* the source, which also preserves its description
and any links.

IT Service Management is kept: it is the site owner's current job, so it is real
positioning rather than noise. Git and Linux are kept as well, though they are
the first candidates to drop if the list ever needs tightening.
"""

from django.db import migrations

# old skill name -> the skill it becomes
CONSOLIDATION = {
    # Languages and disciplines keep their own name.
    'Python': 'Python',
    'Machine Learning': 'Machine Learning',
    'Computer Vision': 'Computer Vision',
    'IT Service Management': 'IT Service Management',
    'Cloud & Deployment': 'Cloud & Deployment',
    'Git': 'Git',
    'Linux': 'Linux',
    # Libraries fold into the discipline they serve. A reader cannot do anything
    # with "Pandas" that they cannot do with "Data Analysis & Visualisation".
    'Pandas': 'Data Analysis & Visualisation',
    'Data Analysis': 'Data Analysis & Visualisation',
    'Scikit-learn': 'Machine Learning',
    # Tools fold into the platform or technology they belong to.
    'PostgreSQL': 'SQL & Databases',
    'SQL': 'SQL & Databases',
    'Django': 'Web Development',
    # Renamed to say plainly what the skill is.
    'Artificial Intelligence': 'AI & Language Models',
    'Statistical Learning': 'Statistical Modelling',
}

# Skill names the site should end up with, in the order they should read.
FINAL_ORDER = [
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

# Rules whose target skill is renamed or folded away.
SIGNAL_REMAP = {
    'Django': 'Web Development',
    'Pandas': 'Data Analysis & Visualisation',
    'Scikit-learn': 'Machine Learning',
    'PostgreSQL': 'SQL & Databases',
    'SQL': 'SQL & Databases',
    'Data Analysis': 'Data Analysis & Visualisation',
    'Artificial Intelligence': 'AI & Language Models',
    'Statistical Learning': 'Statistical Modelling',
}

# Rules that would create skills the set does not want. Deactivated rather than
# deleted so the reasoning stays visible in the admin.
SIGNALS_TO_DISABLE = [
    ('language', 'TeX', 'LaTeX'),
]


def consolidate(apps, schema_editor):
    Skill = apps.get_model('portfolio_app', 'Skill')

    # Work on a copy: targets are created below and must not be re-processed.
    sources = list(Skill.objects.all())
    by_name = {skill.name: skill for skill in sources}

    # 1. Make sure every target exists. A missing target is created by renaming
    #    the source, which keeps its description and project links intact.
    for old_name, new_name in CONSOLIDATION.items():
        if old_name == new_name:
            continue
        source = by_name.get(old_name)
        if source is None:
            continue
        if new_name in by_name:
            continue
        source.name = new_name
        source.save(update_fields=['name'])
        by_name[new_name] = source
        del by_name[old_name]

    # 2. Move project links onto the target, then remove the source.
    for old_name, new_name in CONSOLIDATION.items():
        if old_name == new_name:
            continue
        source = Skill.objects.filter(name=old_name).first()
        if source is None:
            continue
        target = Skill.objects.filter(name=new_name).first()
        if target is None or target.pk == source.pk:
            continue

        for project in source.projects.all():
            project.skills_used.add(target)

        # Keep the earliest start date: merging must not shorten experience.
        if source.start_date and (
            not target.start_date or source.start_date < target.start_date
        ):
            target.start_date = source.start_date
            target.save(update_fields=['start_date'])

        # A merged-away skill should not be left published on its own.
        source.delete()

    # 3. Point the detection rules at the surviving names.
    SkillSignal = apps.get_model('portfolio_app', 'SkillSignal')
    for signal in SkillSignal.objects.all():
        new_name = SIGNAL_REMAP.get(signal.skill_name)
        if not new_name:
            continue
        clash = SkillSignal.objects.filter(
            kind=signal.kind, pattern=signal.pattern, skill_name=new_name
        ).exclude(pk=signal.pk).exists()
        if clash:
            # The same rule already exists for the new name; drop the duplicate.
            signal.delete()
        else:
            signal.skill_name = new_name
            signal.save(update_fields=['skill_name'])

    for kind, pattern, skill_name in SIGNALS_TO_DISABLE:
        SkillSignal.objects.filter(
            kind=kind, pattern=pattern, skill_name=skill_name
        ).update(is_active=False)


def unconsolidate(apps, schema_editor):
    """Re-create the folded-away skills so the change can be reversed.

    The project links cannot be split back apart — a merge is lossy by nature —
    so this restores the skills themselves, not which project used which.
    """
    Skill = apps.get_model('portfolio_app', 'Skill')
    import datetime

    for old_name, new_name in CONSOLIDATION.items():
        if old_name == new_name:
            continue
        if Skill.objects.filter(name=old_name).exists():
            continue
        target = Skill.objects.filter(name=new_name).first()
        if target is None:
            continue
        Skill.objects.create(
            name=old_name,
            start_date=target.start_date or datetime.date.today(),
            description=target.description,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('portfolio_app', '0029_skill_suggestions'),
    ]

    operations = [
        migrations.RunPython(consolidate, unconsolidate),
    ]
