"""Tests for the admin-editable theming system."""

from datetime import date

from django.conf import settings
from django.contrib import admin as django_admin
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from .admin import HexColorInput, ThemeAdminForm
from .context_processors import profile_context
from .models import (
    COLOR_ROLES,
    DEFAULT_DARK_PALETTE,
    DEFAULT_LIGHT_PALETTE,
    FONT_STACKS,
    ROLE_HELP,
    Theme,
    Skill,
    hex_to_rgb_channels,
    normalize_hex_color,
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "portfolio-theme-tests",
    }
}


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
class NormalizeHexColorTests(TestCase):
    def test_accepts_the_spellings_people_actually_type(self):
        for value in ('#1d3557', '1d3557', '#1D3557', '  #1D3557  '):
            with self.subTest(value=value):
                self.assertEqual(normalize_hex_color(value), '#1D3557')

    def test_expands_three_digit_shorthand(self):
        self.assertEqual(normalize_hex_color('#abc'), '#AABBCC')
        self.assertEqual(normalize_hex_color('abc'), '#AABBCC')
        self.assertEqual(normalize_hex_color('#FFF'), '#FFFFFF')

    def test_rejects_anything_that_is_not_a_colour(self):
        for value in (
            None, '', '   ', 'red', '#12', '#12345', '#1234567',
            '#fff; } body { display: none }', 'url(https://evil.example)',
            'rgb(1,2,3)', '#gggggg',
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    normalize_hex_color(value)


class HexToChannelsTests(TestCase):
    def test_converts_hex_to_space_separated_channels(self):
        # Tailwind needs raw channels so it can still apply opacity.
        self.assertEqual(hex_to_rgb_channels('#1D3557'), '29 53 87')
        self.assertEqual(hex_to_rgb_channels('#FFFFFF'), '255 255 255')
        self.assertEqual(hex_to_rgb_channels('#000000'), '0 0 0')

    def test_is_case_insensitive(self):
        self.assertEqual(hex_to_rgb_channels('#a8dadc'), hex_to_rgb_channels('#A8DADC'))

    def test_normalises_before_converting(self):
        self.assertEqual(hex_to_rgb_channels('#abc'), '170 187 204')
        self.assertEqual(hex_to_rgb_channels('1d3557'), '29 53 87')

    def test_malformed_input_cannot_break_the_stylesheet(self):
        for value in ('', None, 'not-a-colour', '#12345'):
            with self.subTest(value=value):
                self.assertEqual(hex_to_rgb_channels(value), '0 0 0')


# ---------------------------------------------------------------------------
# Model behaviour
# ---------------------------------------------------------------------------
class ThemeModelTests(TestCase):
    def test_only_one_theme_can_be_active(self):
        first = Theme.objects.create(name='Nord', is_active=True)
        second = Theme.objects.create(name='Plum', is_active=True)

        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)
        self.assertEqual(Theme.objects.filter(is_active=True).count(), 1)

    def test_active_returns_none_when_nothing_is_configured(self):
        Theme.objects.create(name='Not active')
        self.assertIsNone(Theme.active())

    def test_active_returns_the_activated_theme(self):
        theme = Theme.objects.create(name='Nord', is_active=True)
        self.assertEqual(Theme.active(), theme)

    def test_defaults_match_the_shipped_palette(self):
        # An installation with no Theme row must look exactly as before, so every
        # role must default from the palette matching its own prefix. Checking all
        # of them guards against a light field silently taking a dark default.
        theme = Theme()
        for role, _label, _help in COLOR_ROLES:
            with self.subTest(role=role):
                self.assertEqual(getattr(theme, f'light_{role}'), DEFAULT_LIGHT_PALETTE[role])
                self.assertEqual(getattr(theme, f'dark_{role}'), DEFAULT_DARK_PALETTE[role])
        self.assertEqual(theme.font_body, Theme.FontFamily.INTER)
        self.assertEqual(theme.font_heading, Theme.FontFamily.INTER)

    def test_save_stores_colours_in_canonical_form(self):
        theme = Theme.objects.create(
            name='Shorthand', light_primary='#abc', dark_accent='1d3557',
        )
        theme.refresh_from_db()
        self.assertEqual(theme.light_primary, '#AABBCC')
        self.assertEqual(theme.dark_accent, '#1D3557')

    def test_every_role_has_help_text_for_its_own_field(self):
        # Guards a real regression: inserting a role into COLOR_ROLES shifted the
        # positional help-text lookups, so several fields described the wrong thing.
        for mode in ('light', 'dark'):
            for role, _label, expected in COLOR_ROLES:
                with self.subTest(field=f'{mode}_{role}'):
                    field = Theme._meta.get_field(f'{mode}_{role}')
                    self.assertEqual(field.help_text, expected)
                    self.assertEqual(field.help_text, ROLE_HELP[role])


class ThemeValidationTests(TestCase):
    def test_accepts_a_valid_hex_colour(self):
        for value in ('#1d3557', '1d3557', '#ABC'):
            with self.subTest(value=value):
                Theme(name='Nord', light_primary=value).full_clean()  # must not raise

    def test_rejects_a_non_colour_value(self):
        theme = Theme(name='Nord', light_primary='not-a-colour')
        with self.assertRaises(ValidationError):
            theme.full_clean()

    def test_rejects_css_injection_through_a_colour_field(self):
        # These values are interpolated into a <style> element, so only hex
        # spellings are accepted — no functional notation, no extra syntax.
        for payload in (
            '#fff; } body { display: none }',
            'red',
            '#12345',
            '#1234567',
            '#gggggg',
            'url(https://evil.example)',
            'rgb(1,2,3)',
        ):
            with self.subTest(payload=payload):
                theme = Theme(name='Evil', light_primary=payload)
                with self.assertRaises(ValidationError):
                    theme.full_clean()

    def test_injection_cannot_reach_the_stylesheet_even_if_the_db_is_bypassed(self):
        # .update() skips save()/validation, so css_variables() must normalise on
        # its own — the stylesheet is the security boundary.
        Theme.objects.create(name='Nord')
        Theme.objects.filter(name='Nord').update(
            light_primary='#fff; } body { display: none }', dark_heading='nonsense',
        )
        theme = Theme.objects.get(name='Nord')

        css = theme.css_variables()
        self.assertNotIn('display: none', css)
        self.assertNotIn('nonsense', css)
        self.assertIn('--c-primary: 0 0 0;', css)   # degrades to black
        self.assertIn('--c-heading: 0 0 0;', css)

    def test_rejects_an_unknown_font(self):
        theme = Theme(name='Nord', font_heading='comic-sans')
        with self.assertRaises(ValidationError):
            theme.full_clean()


# ---------------------------------------------------------------------------
# Generated CSS
# ---------------------------------------------------------------------------
class ThemeCSSVariableTests(TestCase):
    def setUp(self):
        self.theme = Theme.objects.create(
            name='Nord',
            light_primary='#1D3557',
            dark_primary='#0F3460',
            font_heading=Theme.FontFamily.SOURCE_SERIF,
            font_body=Theme.FontFamily.JETBRAINS_MONO,
        )

    def test_emits_both_light_and_dark_blocks(self):
        css = self.theme.css_variables()
        self.assertIn(':root {', css)
        self.assertIn('.dark {', css)

    def test_light_values_land_in_root_and_dark_values_in_dark(self):
        css = self.theme.css_variables()
        root_block, dark_block = css.split('.dark {')
        self.assertIn('--c-primary: 29 53 87;', root_block)
        self.assertIn('--c-primary: 15 52 96;', dark_block)

    def test_defines_every_role(self):
        css = self.theme.css_variables()
        for role, _label, _help in COLOR_ROLES:
            with self.subTest(role=role):
                # Once in :root and once in .dark.
                self.assertEqual(css.count(f'--c-{role}:'), 2)

    def test_uses_whitelisted_font_stacks(self):
        css = self.theme.css_variables()
        self.assertIn(FONT_STACKS['source-serif'], css)
        self.assertIn(FONT_STACKS['jetbrains-mono'], css)

    def test_output_contains_no_markup(self):
        # Defence in depth: the generated CSS is assembled only from validated
        # hex values and whitelisted font stacks, so it can never close the
        # <style> element or inject markup.
        css = self.theme.css_variables()
        self.assertNotIn('<', css)
        self.assertNotIn('>', css)
        self.assertEqual(css.count('{'), css.count('}'))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
@override_settings(CACHES=LOCMEM_CACHE)
class ThemeRenderingTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_no_template_syntax_leaks_into_the_rendered_page(self):
        # Guards a real regression: Django's {# #} comment shorthand is
        # single-line only, so a multi-line one is emitted as visible text.
        Theme.objects.create(name='Nord', is_active=True)
        for name in ('index', 'skills', 'projects', 'resume', 'contact', 'privacy'):
            with self.subTest(page=name):
                content = self.client.get(reverse(name)).content.decode()
                for marker in ('{#', '{%', '{{'):
                    self.assertNotIn(marker, content)

    def test_site_renders_without_any_theme(self):
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        # Nothing injected; the compiled stylesheet's :root defaults apply.
        self.assertNotContains(response, '--c-primary:')

    def test_active_theme_variables_are_injected(self):
        Theme.objects.create(name='Nord', is_active=True, light_primary='#112233')
        response = self.client.get(reverse('index'))
        self.assertContains(response, '--c-primary: 17 34 51;')

    def test_inactive_theme_is_not_injected(self):
        Theme.objects.create(name='Plum', is_active=False, light_primary='#112233')
        response = self.client.get(reverse('index'))
        self.assertNotContains(response, '--c-primary: 17 34 51;')

    def test_theme_edit_takes_effect_immediately(self):
        # The active theme is cached, so this also proves the cache is
        # invalidated on save rather than needing a manual clear.
        theme = Theme.objects.create(name='Nord', is_active=True, light_primary='#112233')
        self.assertContains(self.client.get(reverse('index')), '--c-primary: 17 34 51;')

        theme.light_primary = '#445566'
        theme.save()

        self.assertContains(self.client.get(reverse('index')), '--c-primary: 68 85 102;')

    def test_context_processor_caches_the_active_theme(self):
        # Content must exist, otherwise the (separately cached) "last updated"
        # value stays empty and its aggregates re-run on every request.
        Skill.objects.create(name='Python', start_date=date(2020, 1, 1))
        Theme.objects.create(name='Nord', is_active=True)

        profile_context(None)  # primes both cache entries

        # Second call issues one query (the profile); neither the theme nor the
        # content timestamp is re-fetched.
        with self.assertNumQueries(1):
            profile_context(None)


# ---------------------------------------------------------------------------
# Self-hosted fonts (GDPR: no request may leave this domain)
# ---------------------------------------------------------------------------
class SelfHostedFontTests(TestCase):
    def test_pages_do_not_reference_google_fonts(self):
        for name in ('index', 'skills', 'projects', 'resume', 'contact', 'privacy'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertNotContains(response, 'fonts.googleapis.com')
                self.assertNotContains(response, 'fonts.gstatic.com')

    def test_font_files_are_present_and_served_by_django(self):
        for filename in (
            'fonts/inter-latin.woff2',
            'fonts/source-serif-4-latin.woff2',
            'fonts/jetbrains-mono-latin.woff2',
        ):
            with self.subTest(font=filename):
                self.assertIsNotNone(finders.find(filename), f'{filename} is missing')

    def test_stylesheet_declares_the_fonts_locally(self):
        source = (settings.BASE_DIR / 'static' / 'css' / 'tailwind.src.css').read_text()
        self.assertEqual(source.count('@font-face'), 3)
        self.assertIn("url('../fonts/inter-latin.woff2')", source)
        self.assertNotIn('fonts.googleapis', source)

    def test_compiled_stylesheet_uses_variables_and_local_fonts(self):
        compiled = (settings.BASE_DIR / 'static' / 'css' / 'tailwind.css').read_text()
        self.assertIn('var(--c-primary)', compiled)
        self.assertIn('fonts/inter-latin.woff2', compiled)
        self.assertNotIn('fonts.googleapis', compiled)


class DarkModeOverrideRemovalTests(TestCase):
    """The old per-class dark patch layer must not come back."""

    def test_stylesheet_no_longer_patches_tailwind_classes_for_dark_mode(self):
        # Check the *compiled* bundle rather than the source, so explanatory
        # comments naming the removed rules do not count as a regression.
        # (The `.dark` palette block itself is emitted per-theme at runtime and
        # is covered by ThemeCSSVariableTests.)
        compiled = (settings.BASE_DIR / 'static' / 'css' / 'tailwind.css').read_text()
        for stale in ('.dark .bg-', '.dark .text-', '.dark .border-', '.dark body'):
            with self.subTest(rule=stale):
                self.assertNotIn(stale, compiled)


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
class ThemeAdminTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        self.staff = User.objects.create_user('owner', password='x', is_staff=True, is_superuser=True)
        self.client.force_login(self.staff)

    def test_theme_is_registered_in_the_admin(self):
        self.assertIn(Theme, django_admin.site._registry)

    def test_palette_fields_are_editable_hex_text_inputs(self):
        # A native colour picker alone cannot be typed into, so every palette
        # field must be a text input holding a hex code.
        form = ThemeAdminForm()
        for mode in ('light', 'dark'):
            for role, _label, _help in COLOR_ROLES:
                name = f'{mode}_{role}'
                with self.subTest(field=name):
                    widget = form.fields[name].widget
                    self.assertIsInstance(widget, HexColorInput)
                    self.assertEqual(widget.input_type, 'text')
                    # The class is applied when the widget renders.
                    self.assertIn('portfolio-hex-input', widget.render(name, '#112233'))

    def test_rendered_field_contains_both_a_text_box_and_a_picker(self):
        html = ThemeAdminForm()['light_primary'].as_widget()
        self.assertIn('type="text"', html)
        self.assertIn('type="color"', html)
        # The picker is a helper for the text input, which stays the real field.
        self.assertIn('data-for="id_light_primary"', html)
        self.assertIn('name="light_primary"', html)

    def test_hex_value_is_prefilled_and_uppercased(self):
        html = ThemeAdminForm()['light_primary'].as_widget()
        self.assertIn('#1D3557', html)

    def test_picker_falls_back_safely_for_an_unparseable_value(self):
        # A bound form can hold an invalid value. The picker must never receive
        # it (type=color would silently show black or throw), and the echoed text
        # value is escaped rather than interpolated raw.
        widget = ThemeAdminForm()['light_primary'].field.widget
        html = widget.render(
            'light_primary',
            '#fff; } body { display: none }',
            attrs={'id': 'id_light_primary'},
        )
        # Picker sanitised.
        self.assertIn('type="color" class="portfolio-color-picker" value="#000000"', html)
        # The invalid value only appears escaped inside the attribute, never as markup.
        self.assertNotIn('</span><script', html)
        self.assertEqual(html.count('<input'), 2)

    def test_quote_injection_in_the_echoed_value_is_escaped(self):
        # The typed value is echoed back so the user can correct it — that is an
        # attribute-injection sink if it is not escaped.
        widget = ThemeAdminForm()['light_primary'].field.widget
        html = widget.render(
            'light_primary',
            '" onfocus="alert(1)',
            attrs={'id': 'id_light_primary'},
        )
        self.assertNotIn('onfocus="alert(1)"', html)
        self.assertIn('&quot;', html)
        self.assertEqual(html.count('<input'), 2)

    def test_form_accepts_typed_hex_variants_and_stores_them_canonically(self):
        theme = Theme.objects.create(name='Nord')
        for typed, expected in (
            ('1d3557', '#1D3557'),
            ('#abc', '#AABBCC'),
            ('  #aabbcc  ', '#AABBCC'),
            ('#AbC', '#AABBCC'),
        ):
            with self.subTest(typed=typed):
                form = ThemeAdminForm(
                    dict(self._full_palette_data(), light_primary=typed), instance=theme
                )
                self.assertTrue(form.is_valid(), form.errors)
                saved = form.save()
                saved.refresh_from_db()
                self.assertEqual(saved.light_primary, expected)

    def test_form_rejects_a_non_hex_value(self):
        theme = Theme.objects.create(name='Nord')
        payload = dict(self._full_palette_data(), light_primary='#fff; } body{}')
        form = ThemeAdminForm(payload, instance=theme)
        self.assertFalse(form.is_valid())
        self.assertIn('light_primary', form.errors)

    def _full_palette_data(self):
        data = {
            'name': 'Nord',
            'font_heading': Theme.FontFamily.INTER,
            'font_body': Theme.FontFamily.INTER,
        }
        for role, _label, _help in COLOR_ROLES:
            data[f'light_{role}'] = '#112233'
            data[f'dark_{role}'] = '#445566'
        return data

    def test_add_form_loads_and_prefills_the_current_palette(self):
        response = self.client.get(reverse('admin:portfolio_app_theme_add'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '#1D3557')  # default primary
        self.assertContains(response, 'type="color"')
        self.assertContains(response, 'portfolio-hex-input')

    def test_change_form_loads_with_a_live_preview(self):
        theme = Theme.objects.create(name='Nord', is_active=True)
        response = self.client.get(reverse('admin:portfolio_app_theme_change', args=[theme.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'theme-preview')

    def test_activating_through_the_admin_keeps_a_single_active_theme(self):
        Theme.objects.create(name='Nord', is_active=True)
        plum = Theme.objects.create(name='Plum', is_active=False)

        self.client.post(
            reverse('admin:portfolio_app_theme_change', args=[plum.pk]),
            {
                'name': 'Plum',
                'is_active': 'on',
                'font_heading': Theme.FontFamily.INTER,
                'font_body': Theme.FontFamily.INTER,
                **{f'light_{r}': '#112233' for r, _l, _h in COLOR_ROLES},
                **{f'dark_{r}': '#445566' for r, _l, _h in COLOR_ROLES},
            },
        )

        self.assertEqual(Theme.objects.filter(is_active=True).count(), 1)
        self.assertTrue(Theme.objects.get(name='Plum').is_active)
