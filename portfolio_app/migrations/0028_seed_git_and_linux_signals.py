"""Add rules for Git and Linux.

Some skills the site owner lists are true of almost every repository (Git) or are
used without leaving much of a trace in the file list (Linux). They cannot be
proved the way a Django import can, but there is still honest evidence:

* a `.gitignore` is a Git artefact;
* a shell script, a Makefile or Bash dotfiles are Unix tooling.

These rules are broad by nature — `.gitignore` will tag nearly every project with
Git. That is accurate rather than discriminating, which is why they are separate
from the more specific rules and easy to switch off in the admin.
"""

from django.db import migrations

NEW_SIGNALS = [
    ('path', '.gitignore', 'Git', 'Present in a repository that uses Git'),
    ('path', '*.sh', 'Linux', 'Shell script'),
    ('path', 'Makefile', 'Linux', 'Make is a Unix build tool'),
    ('path', '.bashrc', 'Linux', 'Bash configuration'),
]


def create_signals(apps, schema_editor):
    SkillSignal = apps.get_model('portfolio_app', 'SkillSignal')
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


class Migration(migrations.Migration):

    dependencies = [
        ('portfolio_app', '0027_seed_skill_signals'),
    ]

    operations = [
        migrations.RunPython(create_signals, remove_signals),
    ]
