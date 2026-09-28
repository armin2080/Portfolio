from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
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
    search_fields = ('name',)

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



