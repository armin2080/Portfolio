# Generated by Django 5.2 on 2025-05-02 21:05

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('portfolio_app', '0013_project_date'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='certificate',
            name='description',
        ),
    ]
