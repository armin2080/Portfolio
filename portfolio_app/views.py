from django.shortcuts import render
from .models import Skill, Project, Education, WorkExperience, Certificate, Profile, Category
from django.core.mail import send_mail
from django.shortcuts import render
from .forms import ContactForm
from django.conf import settings


def index(req):
    skills = Skill.objects.all()[:3]
    projects = Project.objects.all()[:3]
    profile = Profile.objects.first()

    return render(req, 'homepage.html', {
        'skills': skills,
        'projects': projects,
        'profile': profile,
    })


def contact_view(req):
    if req.method == 'POST':
        form = ContactForm(req.POST)
        if form.is_valid():
            name = form.cleaned_data['name']
            email = form.cleaned_data['email']
            message = form.cleaned_data['message']

            full_message = f"New message from: {name} ({email})\n\n{message}"
            send_mail(
                subject=f'Portfolio: New message from {name}',
                message=full_message,
                from_email=settings.EMAIL_HOST_USER,
                recipient_list=['arminmaddah.a@gmail.com'],
            )

            return render(req, 'success.html')

    else:
        form = ContactForm()

    return render(req, 'contact.html', {'form': form})


def skills_view(req):
    skills = Skill.objects.all()
    return render(req, 'skills.html', {'skills': skills})


def projects_view(req):
    category_slug = req.GET.get('category', '')
    projects = Project.objects.all()
    categories = Category.objects.all()

    if category_slug:
        projects = projects.filter(category__slug=category_slug)

    return render(req, 'projects.html', {
        'projects': projects,
        'categories': categories,
        'active_category': category_slug,
    })


def resume_view(req):
    profile = Profile.objects.first()
    educations = Education.objects.all()
    work_experiences = WorkExperience.objects.all()
    skills = Skill.objects.all()
    certificates = Certificate.objects.all()

    return render(req, 'resume.html', {
        'profile': profile,
        'educations': educations,
        'experiences': work_experiences,
        'skills': skills,
        'certificates': certificates,
    })
