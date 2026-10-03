from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
import json

from .contrast import checks_as_dicts
from .models import (
    COLOR_ROLES,
    Category,
    Certificate,
    ContactMessage,
    Education,
    PageView,
    Profile,
    Project,
    Skill,
    SkillSignal,
    SkillSuggestion,
    Theme,
    WorkExperience,
    normalize_hex_color,
)


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
    list_display = (
        'name', 'skill_type', 'start_date', 'suggested_date', 'experience_years',
        'project_count', 'is_published',
    )
    list_editable = ('is_published',)
    list_filter = ('skill_type', 'is_published')
    search_fields = ('name', 'description')
    actions = ('publish_skills', 'unpublish_skills', 'apply_suggested_dates')
    readonly_fields = ('suggested_start_date',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(projects_total=Count('projects'))

    @admin.display(description='Experience')
    def experience_years(self, obj):
        from django.utils import timezone
        from dateutil.relativedelta import relativedelta
        years = relativedelta(timezone.now().date(), obj.start_date).years
        return f"{years} year{'s' if years != 1 else ''}"

    @admin.display(description='Projects', ordering='projects_total')
    def project_count(self, obj):
        return obj.projects_total

    @admin.display(description='Suggested date', ordering='suggested_start_date')
    def suggested_date(self, obj):
        """An earlier date GitHub evidence supports, waiting to be confirmed."""
        if not obj.suggested_start_date or obj.suggested_start_date >= obj.start_date:
            return '-'
        return format_html(
            '<strong>{}</strong> <em>(earlier)</em>', obj.suggested_start_date
        )

    @admin.action(description='Publish selected skills')
    def publish_skills(self, request, queryset):
        updated = queryset.update(is_published=True)
        self.message_user(request, f'{updated} skill(s) published.')

    @admin.action(description='Hide selected skills')
    def unpublish_skills(self, request, queryset):
        updated = queryset.update(is_published=False)
        self.message_user(request, f'{updated} skill(s) hidden.')

    @admin.action(description='Apply suggested experience dates')
    def apply_suggested_dates(self, request, queryset):
        """Move start dates earlier where GitHub evidence supports it.

        Only ever earlier: applying suggestions must not be able to shorten an
        experience claim the site owner set by hand.
        """
        applied = 0
        for skill in queryset.filter(suggested_start_date__isnull=False):
            if skill.suggested_start_date < skill.start_date:
                skill.start_date = skill.suggested_start_date
                skill.suggested_start_date = None
                skill.save(update_fields=['start_date', 'suggested_start_date', 'updated_at'])
                applied += 1
            else:
                skill.suggested_start_date = None
                skill.save(update_fields=['suggested_start_date', 'updated_at'])
        self.message_user(
            request, f'Applied a suggested date to {applied} skill(s).'
        )


@admin.register(SkillSuggestion)
class SkillSuggestionAdmin(admin.ModelAdmin):
    """Review screen for the skills the sync believes each project demonstrates.

    Nothing is tagged until a suggestion is accepted here. Accepting adds the
    skill to the project; it does not publish the skill, so a newly detected
    technology still needs publishing under Skills before it appears on the site.
    """

    list_display = ('project', 'skill', 'skill_published', 'evidence', 'created_at')
    list_filter = ('skill', 'project', 'skill__is_published')
    search_fields = ('project__name', 'skill__name', 'evidence')
    autocomplete_fields = ('project', 'skill')
    actions = ('accept_suggestions', 'dismiss_suggestions')
    readonly_fields = ('created_at', 'updated_at')

    @admin.display(description='Skill published', boolean=True)
    def skill_published(self, obj):
        return obj.skill.is_published

    @admin.action(description='Accept: add these skills to their projects')
    def accept_suggestions(self, request, queryset):
        accepted = 0
        unpublished = set()
        for suggestion in queryset.select_related('project', 'skill'):
            suggestion.project.skills_used.add(suggestion.skill)
            if not suggestion.skill.is_published:
                unpublished.add(suggestion.skill.name)
            accepted += 1
        queryset.delete()
        self.message_user(
            request,
            f'Added {accepted} skill tag(s) to their projects.'
            + (
                ' Still hidden until published: ' + ', '.join(sorted(unpublished))
                if unpublished
                else ''
            ),
        )

    @admin.action(description='Dismiss: discard without tagging')
    def dismiss_suggestions(self, request, queryset):
        count = queryset.count()
        queryset.delete()
        self.message_user(
            request,
            f'Dismissed {count} suggestion(s). They return if the repository '
            'changes or after a sync with --force-skills.',
        )


@admin.register(SkillSignal)
class SkillSignalAdmin(admin.ModelAdmin):
    """The rules that turn repository contents into skill suggestions.

    Editing these is how a new technology gets recognised without a deploy. An
    unchanged repository is not re-read, so run
    ``manage.py sync_github_projects --force-skills`` after changing a rule.
    """

    list_display = ('kind', 'pattern', 'skill_status', 'is_active', 'note')
    list_editable = ('is_active',)
    list_filter = ('kind', 'is_active')
    search_fields = ('pattern', 'skill_name', 'note')
    fieldsets = (
        (None, {
            'fields': ('kind', 'pattern', 'skill_name', 'is_active', 'note'),
            'description': (
                'A rule that matches a repository adds its skill to that project. '
                'After changing a rule, run \u201csync_github_projects --force-skills\u201d '
                'so repositories that have not changed are read again.'
            ),
        }),
    )

    @admin.display(description='Skill')
    def skill_status(self, obj):
        if Skill.objects.filter(name__iexact=obj.skill_name).exists():
            return obj.skill_name
        return format_html('{} <em>(created hidden when first matched)</em>', obj.skill_name)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    prepopulated_fields = {'slug': ('name',)}


class ProjectSourceFilter(admin.SimpleListFilter):
    title = 'source'
    parameter_name = 'source'

    def lookups(self, request, model_admin):
        return (('github', 'Imported from GitHub'), ('manual', 'Added manually'))

    def queryset(self, request, queryset):
        if self.value() == 'github':
            return queryset.exclude(github_repo_id=None)
        if self.value() == 'manual':
            return queryset.filter(github_repo_id=None)


class ProjectImageFilter(admin.SimpleListFilter):
    """Find imported projects that still need a screenshot."""

    title = 'image'
    parameter_name = 'has_image'

    def lookups(self, request, model_admin):
        return (('yes', 'Has an image'), ('no', 'Still needs an image'))

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.exclude(Q(image='') | Q(image__isnull=True))
        if self.value() == 'no':
            return queryset.filter(Q(image='') | Q(image__isnull=True))


class ProjectSuggestionFilter(admin.SimpleListFilter):
    """Find projects the sync has suggestions waiting for."""

    title = 'skill suggestions'
    parameter_name = 'has_suggestions'

    def lookups(self, request, model_admin):
        return (('yes', 'Has suggestions to review'), ('no', 'No suggestions'))

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(skill_suggestions__isnull=False).distinct()
        if self.value() == 'no':
            return queryset.filter(skill_suggestions__isnull=True)


class SkillSuggestionInline(admin.TabularInline):
    """Suggestions for one project, so they can be judged where the tags are."""

    model = SkillSuggestion
    extra = 0
    can_delete = True
    autocomplete_fields = ('skill',)
    verbose_name = 'suggested skill'
    verbose_name_plural = (
        'suggested skills — accept these on the Skill suggestions page, or with '
        'the "Accept all pending skill suggestions" action'
    )


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'category', 'date', 'is_published', 'has_image', 'has_description',
        'source', 'suggestions_count',
    )
    list_editable = ('is_published',)
    list_filter = (
        'is_published', ProjectSourceFilter, ProjectImageFilter,
        ProjectSuggestionFilter, 'suggest_skills', 'category',
    )
    search_fields = ('name', 'description', 'github_full_name')
    readonly_fields = (
        'github_full_name', 'github_repo_id', 'github_synced_at', 'github_pushed_at',
    )
    actions = ('refresh_from_github', 'accept_skill_suggestions')
    inlines = (SkillSuggestionInline,)
    fieldsets = (
        (None, {
            'fields': ('name', 'description', 'link', 'date', 'is_published'),
        }),
        ('Presentation', {
            'fields': ('image', 'category', 'skills_used', 'suggest_skills'),
            'description': (
                'Cards show a placeholder graphic until an image is uploaded. '
                'Set these yourself — the GitHub sync never changes them, and its '
                'skill suggestions stay suggestions until you accept them.'
            ),
        }),
        ('GitHub', {
            'fields': (
                'github_full_name', 'github_repo_id', 'github_synced_at',
                'github_pushed_at',
            ),
            'classes': ('collapse',),
            'description': (
                'Imported from GitHub. The daily sync only refreshes the '
                'bookkeeping fields here; use the "Refresh from GitHub" action to '
                'pull the name, description and link again.'
            ),
        }),
    )

    @admin.display(description='Source')
    def source(self, obj):
        return 'GitHub' if obj.github_repo_id else 'Manual'

    @admin.display(description='Suggested skills')
    def suggestions_count(self, obj):
        """A count with a link, so reviewing is one click from the list."""
        count = len(obj.skill_suggestions.all())
        if not count:
            return '-'
        url = reverse('admin:portfolio_app_skillsuggestion_changelist')
        return format_html(
            '<a href="{}?project__id__exact={}">{} to review</a>', url, obj.pk, count
        )

    @admin.action(description='Accept all pending skill suggestions')
    def accept_skill_suggestions(self, request, queryset):
        accepted = 0
        unpublished = set()
        for project in queryset.prefetch_related('skill_suggestions__skill'):
            for suggestion in project.skill_suggestions.all():
                project.skills_used.add(suggestion.skill)
                if not suggestion.skill.is_published:
                    unpublished.add(suggestion.skill.name)
                suggestion.delete()
                accepted += 1
        self.message_user(
            request,
            f'Added {accepted} suggested skill tag(s).'
            + (
                ' Still hidden until published: ' + ', '.join(sorted(unpublished))
                if unpublished
                else ''
            ),
        )

    @admin.display(description='Image', boolean=True)
    def has_image(self, obj):
        return bool(obj.image)

    @admin.display(description='Description', boolean=True)
    def has_description(self, obj):
        return bool(obj.description)

    @admin.action(description='Refresh name, description and link from GitHub')
    def refresh_from_github(self, request, queryset):
        from .github_sync import refresh_project_from_github

        refreshed, failed = 0, []
        for project in queryset:
            if not project.github_repo_id:
                failed.append(f'{project.name} (not linked to GitHub)')
                continue
            try:
                refresh_project_from_github(project)
                refreshed += 1
            except Exception as exc:
                failed.append(f'{project.name} ({exc})')

        if refreshed:
            self.message_user(
                request,
                f'Refreshed {refreshed} project(s) from GitHub. '
                'Photos, categories and skills were left untouched.',
            )
        if failed:
            self.message_user(
                request,
                'Could not refresh: ' + '; '.join(failed),
                level=messages.WARNING,
            )


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


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    """Read-only: messages only arrive through the contact form."""

    list_display = ('name', 'email', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('name', 'email', 'message')
    readonly_fields = ('name', 'email', 'message', 'ip_address', 'created_at')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False


@admin.register(PageView)
class PageViewAdmin(admin.ModelAdmin):
    """Read-only raw log. The summary lives at /dashboard/.

    The verbose name is overridden so this does not look like a second,
    competing place to read the statistics.
    """

    list_display = (
        'viewed_at', 'path', 'country', 'device_type', 'browser', 'os',
        'referrer', 'is_bot',
    )
    list_filter = ('is_bot', 'device_type', 'browser', 'country')
    search_fields = ('path', 'referrer')
    date_hierarchy = 'viewed_at'
    ordering = ('-viewed_at',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # Read-only: rows are written by the middleware, never edited by hand.
        return False


# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------

class HexColorInput(forms.TextInput):
    """A text field for typing/pasting a hex colour, plus a picker swatch.

    A native ``<input type="color">`` alone only allows clicking, so this renders
    an editable hex field next to the picker and keeps the two in sync. Either
    workflow works, and the value is normalised to '#RRGGBB' on save.
    """

    def build_attrs(self, base_attrs, extra_attrs=None):
        attrs = super().build_attrs(base_attrs, extra_attrs)
        classes = set((attrs.get('class') or '').split())
        classes.add('portfolio-hex-input')
        attrs['class'] = ' '.join(sorted(classes))
        attrs.setdefault('placeholder', '#RRGGBB')
        attrs.setdefault('autocomplete', 'off')
        attrs.setdefault('spellcheck', 'false')
        return attrs

    def render(self, name, value, attrs=None, renderer=None):
        attrs = self.build_attrs(self.attrs, attrs)
        element_id = attrs.get('id', f'id_{name}')

        # The picker needs a valid '#rrggbb'; fall back to black when the field
        # is empty or holds something we cannot parse.
        try:
            picker_value = normalize_hex_color(value) if value else '#000000'
        except ValidationError:
            picker_value = '#000000'

        picker = format_html(
            '<input type="color" class="portfolio-color-picker" value="{}" '
            'data-for="{}" aria-label="{}" tabindex="-1">',
            picker_value,
            element_id,
            'Colour picker',
        )
        text_input = super().render(name, value, attrs, renderer)
        return format_html(
            '<span class="portfolio-hex-field">{}{}</span>', picker, text_input
        )


class ThemeAdminForm(forms.ModelForm):
    class Meta:
        model = Theme
        fields = '__all__'
        widgets = {
            f'{mode}_{role}': HexColorInput()
            for mode in ('light', 'dark')
            for role, _label, _help in COLOR_ROLES
        }


@admin.register(Theme)
class ThemeAdmin(admin.ModelAdmin):
    """Create palettes here and activate one; the site restyles immediately."""

    form = ThemeAdminForm
    list_display = ('name', 'is_active', 'palette_preview', 'font_heading', 'font_body', 'updated_at')
    list_editable = ('is_active',)
    readonly_fields = ('live_preview', 'updated_at', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name',)

    fieldsets = (
        (None, {
            'fields': ('name', 'is_active'),
            'description': (
                'Activating a theme applies it to the live site straight away — '
                'no restart. Only one theme can be active at a time.'
            ),
        }),
        ('Light mode palette', {
            'fields': tuple(f'light_{role}' for role, _l, _h in COLOR_ROLES),
        }),
        ('Dark mode palette', {
            'fields': tuple(f'dark_{role}' for role, _l, _h in COLOR_ROLES),
            'description': (
                'Used when a visitor switches on dark mode. Set these so both '
                'palettes stay readable — they are two versions of one theme.'
            ),
        }),
        ('Typography', {
            'fields': ('font_heading', 'font_body'),
            'description': (
                'All fonts are self-hosted, so no data leaves this server and '
                'no consent is required.'
            ),
        }),
        ('Preview', {
            'fields': ('live_preview',),
        }),
        ('Meta', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    class Media:
        css = {'all': ('css/admin-theme.css',)}
        js = ('js/admin-theme.js',)

    @admin.display(description='Palette')
    def palette_preview(self, obj):
        """Small swatch strip for the change list."""
        return format_html(
            '<span class="theme-swatches">{}</span>',
            format_html_join(
                '',
                '<span class="theme-swatch" style="background:{}" title="{}"></span>',
                ((getattr(obj, f'light_{role}'), label) for role, label, _h in COLOR_ROLES),
            ),
        )

    @admin.display(description='Preview')
    def live_preview(self, obj):
        """Swatches, a live mock of the site, and a contrast report.

        The check definitions are serialised from ``portfolio_app.contrast`` so
        the server-rendered report and the JavaScript live update share one
        source of truth for the pairings and thresholds.
        """
        swatches = format_html_join(
            '',
            '<div class="theme-swatch-item">'
            '<span class="theme-swatch" data-role="{}" data-mode="light" style="background:{}"></span>'
            '<span class="theme-swatch" data-role="{}" data-mode="dark" style="background:{}"></span>'
            '<code>{}</code></div>',
            (
                (role, getattr(obj, f'light_{role}'), role, getattr(obj, f'dark_{role}'), label)
                for role, label, _h in COLOR_ROLES
            ),
        ) if obj and obj.pk else 'Save the theme once to see a preview.'

        warnings = obj.contrast_warnings() if obj and obj.pk else []
        if not obj or not obj.pk:
            report = mark_safe('Save the theme once to run the contrast check.')
        elif not warnings:
            # Constant markup with nothing to interpolate — format_html() requires
            # at least one argument, so mark it safe explicitly.
            report = mark_safe('<p class="theme-contrast-ok">No contrast problems detected.</p>')
        else:
            report = format_html(
                '<ul class="theme-contrast-list">{}</ul>',
                format_html_join(
                    '',
                    '<li><strong>{}</strong> \u2014 {} '
                    '<span class="theme-contrast-ratio">{}</span></li>',
                    (
                        (
                            f'{warning["mode"].title()} \u00b7 {warning["label"]}',
                            warning['consequence'],
                            # Pre-formatted: format_html passes arguments through
                            # conditional_escape, which turns them into SafeString,
                            # and SafeString does not support numeric format codes.
                            f'{warning["ratio"]:.2f}:1, needs {warning["minimum"]:.1f}:1',
                        )
                        for warning in warnings
                    ),
                ),
            )

        return format_html(
            '<div class="theme-preview" id="theme-preview" data-checks="{}">'
            '<div class="theme-preview-swatches">{}</div>'
            '<p class="help">Left swatch = light mode, right swatch = dark mode. '
            'Adjust a colour above and the swatches and contrast report update '
            'before you save.</p>'
            '<h3 class="theme-preview-heading">Contrast</h3>'
            '<div id="theme-contrast-report">{}</div>'
            '</div>',
            json.dumps(checks_as_dicts()),
            swatches,
            report,
        )



