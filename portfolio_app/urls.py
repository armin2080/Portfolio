from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('contact/', views.contact_view, name='contact'),
    path('contact/success/', views.contact_success, name='contact_success'),
    path('skills/', views.skills_view, name='skills'),
    path('projects/', views.projects_view, name='projects'),
    path('resume/', views.resume_view, name='resume'),
    path('privacy/', views.privacy_view, name='privacy'),
    # Staff-only visitor statistics.
    path('dashboard/', views.dashboard, name='dashboard'),
]