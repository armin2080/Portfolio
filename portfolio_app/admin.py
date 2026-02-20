from django.contrib import admin
from .models import Skill, Project, Education, WorkExperience, Certificate, Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('name', 'title', 'available_for_hire', 'updated_at')
    fieldsets = (
        ('Personal Information', {
            'fields': ('name', 'title', 'bio')
        }),
        ('Profile Picture', {
            'fields': ('profile_picture',)
        }),
        ('Status', {
            'fields': ('available_for_hire',)
        }),
    )

    def has_add_permission(self, request):
        # Allow adding only if no profile exists
        return not Profile.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Prevent deletion of profile
        return False


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



