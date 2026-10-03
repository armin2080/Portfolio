"""Add skill suggestions, and a pending suggested start date per skill.

`auto_skills` becomes `suggest_skills`: the sync no longer maintains the tags, it
proposes them. The rename is explicit so the existing value is kept — letting
makemigrations infer it would have dropped and re-added the column.
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('portfolio_app', '0028_seed_git_and_linux_signals'),
    ]

    operations = [
        migrations.RenameField(
            model_name='project',
            old_name='auto_skills',
            new_name='suggest_skills',
        ),
        migrations.AlterField(
            model_name='project',
            name='suggest_skills',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'Have the sync read this repository and propose skill tags for '
                    'review. Suggestions are only ideas: a tag is added when you '
                    'accept it, so the tags above stay exactly as you set them.'
                ),
                verbose_name='suggest skills from GitHub',
            ),
        ),
        migrations.AddField(
            model_name='skill',
            name='suggested_start_date',
            field=models.DateField(
                blank=True,
                help_text=(
                    'Earlier date suggested by GitHub evidence, waiting for your '
                    'confirmation. Apply it from the skills list; it never '
                    'overwrites the date above on its own.'
                ),
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name='skill',
            name='is_published',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'Untick to hide this skill from the site. A skill the GitHub '
                    'sync detects for the first time is created unticked, so '
                    'nothing appears publicly until you have reviewed it and '
                    'written a description.'
                ),
            ),
        ),
        migrations.CreateModel(
            name='SkillSuggestion',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'evidence',
                    models.CharField(
                        blank=True,
                        help_text=(
                            'What in the repository suggested this, for example '
                            '"manage.py".'
                        ),
                        max_length=300,
                    ),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'project',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='skill_suggestions',
                        to='portfolio_app.project',
                    ),
                ),
                (
                    'skill',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='suggestions',
                        to='portfolio_app.skill',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Skill suggestion',
                'verbose_name_plural': 'Skill suggestions',
                'ordering': ['project__name', 'skill__name'],
            },
        ),
        migrations.AddConstraint(
            model_name='skillsuggestion',
            constraint=models.UniqueConstraint(
                fields=('project', 'skill'), name='unique_project_skill_suggestion',
            ),
        ),
    ]
