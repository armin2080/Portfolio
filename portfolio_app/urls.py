from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('contact/', views.contact_view, name='contact'),
    path('contact/success/', views.contact_success, name='contact_success'),
    path('skills/', views.skills_view, name='skills'),
    path('projects/', views.projects_view, name='projects'),
    path('resume/', views.resume_view, name='resume'),
    # The CV is generated from the site's content on each first download and
    # cached; see portfolio_app/resume_latex.py.
    path('resume/download/', views.resume_download_view, name='resume_download'),
    # The same CV, with the visitor choosing what goes into it.
    path('resume/generator/', views.resume_generator_view, name='resume_generator'),
    path('privacy/', views.privacy_view, name='privacy'),
    # Staff-only visitor statistics.
    path('dashboard/', views.dashboard, name='dashboard'),
]