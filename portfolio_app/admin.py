from django.contrib import admin
from .models import Skill, Project, Education, WorkExperience, Certificate, Profile, Category


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('name', 'title', 'location', 'available_for_hire', 'updated_at')
    fieldsets = (
        ('Personal Information', {
            'fields': ('name', 'title', 'bio', 'professional_summary', 'profile_picture')
        }),
        ('Contact', {
            'fields': ('email', 'phone', 'location')
        }),
        ('Social Links', {
            'fields': ('linkedin_url', 'github_url', 'telegram_url', 'whatsapp_url')
        }),
        ('Resume Files', {
            'fields': ('resume_en', 'resume_de')
        }),
        ('Status', {
            'fields': ('available_for_hire',)
        }),
    )

    def has_add_permission(self, request):
        return not Profile.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Skill)
class SkillsAdmin(admin.ModelAdmin):
    list_display = ('name', 'skill_type', 'start_date', 'experience_years')
    list_filter = ('skill_type',)
    search_fields = ('name',)

    @admin.display(description='Experience')
    def experience_years(self, obj):
        from django.utils import timezone
        from dateutil.relativedelta import relativedelta
        years = relativedelta(timezone.now().date(), obj.start_date).years
        return f"{years} year{'s' if years != 1 else ''}"


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'date', 'has_image')
    list_filter = ('category',)
    search_fields = ('name', 'short_description')

    def has_image(self, obj):
        return bool(obj.image)
    has_image.boolean = True
    has_image.short_description = 'Image'


@admin.register(Education)
class EducationAdmin(admin.ModelAdmin):
    list_display = ('degree', 'institution', 'start_date', 'end_date')
    search_fields = ('degree', 'institution')


@admin.register(WorkExperience)
class WorkExperienceAdmin(admin.ModelAdmin):
    list_display = ('role', 'company', 'location', 'start_date', 'end_date')
    search_fields = ('role', 'company')
    list_filter = ('location',)


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = ('name', 'institution', 'issue_date')
    search_fields = ('name', 'institution')



