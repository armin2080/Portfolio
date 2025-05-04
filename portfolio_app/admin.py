from django.contrib import admin
from .models import Skill, Project, Education, WorkExperience, Certificate



admin.site.register(WorkExperience)


@admin.register(Skill)
class SkillsAdmin(admin.ModelAdmin):
    list_display = ('name', 'proficiency')
    search_fields = ('name',)
    ordering = ('-proficiency',)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.order_by('-proficiency')

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'date', 'category')
    ordering = ('-date',)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.order_by('-date')

@admin.register(Education)
class EducationAdmin(admin.ModelAdmin):
    list_display = ('institution', 'start_date', 'end_date')
    ordering = ('-start_date',)


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = ('name', 'institution', 'issue_date')
    ordering = ('-issue_date',)



