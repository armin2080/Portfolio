from django.shortcuts import render
from .models import Skill, Project, Education, WorkExperience, Certificate, Profile
from django.core.mail import send_mail
from django.shortcuts import render
from .forms import ContactForm


# Create your views here.

def index(req):
    skills = Skill.objects.all().order_by('-proficiency')[:3]
    projects = Project.objects.all().order_by('-date')[:3]
    profile = Profile.objects.first()  # Get the profile (only one should exist)

    return render(req, 'homepage.html', {
        'skills': skills,
        'projects': projects,
        'profile': profile
    })


def contact_view(req):
    if req.method == 'POST':
        form = ContactForm(req.POST)
        if form.is_valid():
            name = form.cleaned_data['name']
            email = form.cleaned_data['email']
            message = form.cleaned_data['message']

            send_mail(
                subject=f'New message from {name} | {email}',
                message=message,
                from_email=email,
                recipient_list=['arminmaddah.a@gmail.com'], 
            )

            return render(req, 'success.html')
            
    else:
        form = ContactForm()

    return render(req, 'contact.html', {'form': form})


def skills_view(req):
    skills = Skill.objects.all().order_by('-proficiency')
    return render(req, 'skills.html', {'skills': skills})

def projects_view(req):
    projects = Project.objects.all().order_by('-date')
    return render(req, 'projects.html', {'projects': projects})


def resume_view(req):
    educations = reversed(Education.objects.all())
    work_experiences = reversed(WorkExperience.objects.all())
    skills = Skill.objects.all().order_by('-proficiency')
    certificates = Certificate.objects.all().order_by('-issue_date')

    return render(req, 'resume.html', {'educations': educations , 'experiences': work_experiences , 'skills': skills, 'certificates': certificates})
