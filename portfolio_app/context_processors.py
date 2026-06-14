from django.db.models import Max
from .models import Profile, Skill, Project, Education, WorkExperience, Certificate


def profile_context(request):
    """Make global data available in all templates."""
    models = [Skill, Project, Education, WorkExperience, Certificate, Profile]
    latest = None
    for model in models:
        last = model.objects.aggregate(Max('updated_at'))['updated_at__max']
        if last and (latest is None or last > latest):
            latest = last

    return {
        'global_profile': Profile.objects.first(),
        'last_updated': latest,
    }
