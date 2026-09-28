"""Tests for the WCAG contrast checker used by the Themes admin."""

import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .contrast import (
    BOUNDARY_MIN,
    CONTRAST_CHECKS,
    TEXT_MIN,
    checks_as_dicts,
    contrast_ratio,
    palette_contrast_warnings,
    relative_luminance,
)
from .models import (
    DEFAULT_DARK_PALETTE,
    DEFAULT_LIGHT_PALETTE,
    COLOR_ROLES,
    Theme,
)


# ---------------------------------------------------------------------------
# Maths
# ---------------------------------------------------------------------------
class ContrastMathTests(TestCase):
    def test_relative_luminance_of_black_and_white(self):
        self.assertAlmostEqual(relative_luminance('#000000'), 0.0, places=6)
        self.assertAlmostEqual(relative_luminance('#FFFFFF'), 1.0, places=6)

    def test_contrast_between_black_and_white_is_21(self):
        self.assertAlmostEqual(contrast_ratio('#000000', '#FFFFFF'), 21.0, places=2)

    def test_contrast_of_a_colour_with_itself_is_one(self):
        self.assertAlmostEqual(contrast_ratio('#1D3557', '#1D3557'), 1.0, places=6)

    def test_contrast_is_symmetric(self):
        self.assertAlmostEqual(
            contrast_ratio('#1D3557', '#F1FAEE'),
            contrast_ratio('#F1FAEE', '#1D3557'),
            places=6,
        )

    def test_accepts_shorthand_and_missing_hash(self):
        self.assertAlmostEqual(
            contrast_ratio('#abc', '#000000'), contrast_ratio('#AABBCC', '000000'), places=6
        )

    def test_returns_none_for_unusable_values(self):
        for bad in (None, '', 'red', '#12345', 'not-a-colour'):
            with self.subTest(value=bad):
                self.assertIsNone(contrast_ratio(bad, '#FFFFFF'))
                self.assertIsNone(relative_luminance(bad))

    def test_known_ratio_for_the_nav_text_on_the_bar(self):
        # Light nav text on the default light bar: comfortably readable.
        ratio = contrast_ratio('#F1FAEE', '#1D3557')
        self.assertGreater(ratio, 10)


# ---------------------------------------------------------------------------
# Palette warnings
# ---------------------------------------------------------------------------
class PaletteWarningTests(TestCase):
    def _clean_palette(self):
        """Every checked pair is black-on-white or white-on-black."""
        return {
            'primary': '#000000', 'inverse': '#FFFFFF', 'accent': '#FFFFFF',
            'page': '#FFFFFF', 'ink': '#000000', 'surface': '#FFFFFF',
            'muted': '#000000', 'emphasis': '#000000',
        }

    def test_a_palette_with_no_problems_returns_nothing(self):
        self.assertEqual(palette_contrast_warnings(self._clean_palette()), [])

    def test_detects_the_top_bar_blending_into_the_page(self):
        palette = dict(self._clean_palette(), primary='#0F3460', page='#0F3460')
        labels = {w['label'] for w in palette_contrast_warnings(palette, mode='dark')}
        self.assertIn('Top bar band', labels)

    def test_reports_the_ratio_and_the_minimum(self):
        palette = dict(self._clean_palette(), primary='#0F3460', page='#0F3460')
        warning = next(
            w for w in palette_contrast_warnings(palette) if w['label'] == 'Top bar band'
        )
        self.assertAlmostEqual(warning['ratio'], 1.0, places=1)
        self.assertEqual(warning['minimum'], BOUNDARY_MIN)
        self.assertIn('blend', warning['consequence'])

    def test_detects_unreadable_nav_text(self):
        palette = dict(self._clean_palette(), primary='#FFFFFF', inverse='#F0F0F0')
        labels = {w['label'] for w in palette_contrast_warnings(palette)}
        self.assertIn('Top bar links', labels)

    def test_warning_presence_matches_the_thresholds(self):
        # Consistency: a palette warns if and only if some pair falls below its
        # minimum, using the same comparison the reporter uses.
        for palette in (
            self._clean_palette(),
            dict(self._clean_palette(), primary='#0F3460', page='#0F3460'),
            dict(self._clean_palette(), muted='#FEFEFE'),
        ):
            with self.subTest(palette=palette['primary']):
                expected = any(
                    contrast_ratio(palette.get(fg), palette.get(bg)) < minimum
                    for _label, fg, bg, minimum, _c in CONTRAST_CHECKS
                    if contrast_ratio(palette.get(fg), palette.get(bg)) is not None
                )
                actual = bool(palette_contrast_warnings(palette))
                self.assertEqual(actual, expected)

    def test_every_check_names_a_real_role(self):
        roles = {role for role, _l, _h in COLOR_ROLES}
        for label, fg, bg, minimum, consequence in CONTRAST_CHECKS:
            with self.subTest(check=label):
                self.assertIn(fg, roles)
                self.assertIn(bg, roles)
                self.assertGreater(minimum, 1.0)
                self.assertTrue(consequence)

    def test_cards_are_not_flagged_for_lacking_a_colour_step(self):
        # Regression guard: cards are separated by a shadow, so a surface/page
        # contrast rule would flag the shipped design as broken.
        for palette in (DEFAULT_LIGHT_PALETTE, DEFAULT_DARK_PALETTE):
            with self.subTest(palette=palette['page']):
                labels = {w['label'] for w in palette_contrast_warnings(palette)}
                self.assertNotIn('Cards', labels)


class ShippedDefaultsTests(TestCase):
    """Documents the real contrast findings in the palette the site falls back to."""

    def test_light_palette_text_is_readable(self):
        warnings = palette_contrast_warnings(DEFAULT_LIGHT_PALETTE, mode='light')
        labels = {w['label'] for w in warnings}
        for ok in ('Top bar links', 'Top bar band', 'Body text', 'Text on cards', 'Muted text'):
            with self.subTest(check=ok):
                self.assertNotIn(ok, labels, f'{ok} unexpectedly fails in the light defaults')

    def test_light_button_labels_are_slightly_below_aa(self):
        # FINDING: cream text on the emphasis red is 3.9:1 — under the 4.5:1 AA
        # threshold for normal text. Passes only the 3:1 large-text bar, so this
        # is a real (pre-existing) issue rather than a contrast-checker bug. If
        # the button colour is ever darkened, this expectation should change.
        ratio = contrast_ratio(
            DEFAULT_LIGHT_PALETTE['inverse'], DEFAULT_LIGHT_PALETTE['emphasis']
        )
        self.assertGreater(ratio, 3.0)
        self.assertLess(ratio, TEXT_MIN)

    def test_dark_palette_bar_separation_is_the_known_problem(self):
        # FINDING: the shipped dark bar (primary #0F3460) is very close to the
        # dark page (#1A1A2E), so the navigation band has no visible edge.
        ratio = contrast_ratio(
            DEFAULT_DARK_PALETTE['primary'], DEFAULT_DARK_PALETTE['page']
        )
        self.assertLess(ratio, BOUNDARY_MIN)
        self.assertLess(ratio, 1.5)

    def test_dark_palette_text_is_readable(self):
        # The dark defaults get the text right even though the band does not.
        self.assertGreater(
            contrast_ratio(DEFAULT_DARK_PALETTE['inverse'], DEFAULT_DARK_PALETTE['primary']),
            TEXT_MIN,
        )
        self.assertGreater(
            contrast_ratio(DEFAULT_DARK_PALETTE['ink'], DEFAULT_DARK_PALETTE['page']),
            TEXT_MIN,
        )


class ThemeContrastWarningsTests(TestCase):
    def test_theme_reports_both_palettes(self):
        theme = Theme.objects.create(name='Bad', light_primary='#0F3460', light_page='#0F3460',
                                     dark_primary='#0F3460', dark_page='#0F3460')
        modes = {w['mode'] for w in theme.contrast_warnings()}
        self.assertEqual(modes, {'light', 'dark'})


# ---------------------------------------------------------------------------
# Admin integration
# ---------------------------------------------------------------------------
class ContrastReportAdminTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('owner', password='x', is_staff=True, is_superuser=True)
        self.client.force_login(self.staff)

    def test_change_form_reports_findings_for_the_defaults(self):
        theme = Theme.objects.create(name='Nord')
        response = self.client.get(
            reverse('admin:portfolio_app_theme_change', args=[theme.pk])
        )
        body = response.content.decode()
        self.assertIn('theme-contrast-report', body)
        self.assertIn('theme-contrast-list', body)
        # The two documented findings in the shipped palettes.
        self.assertIn('Top bar band', body)     # dark bar blends into the page
        self.assertIn('Button labels', body)    # cream on red is 3.9:1

    def test_change_form_says_ok_when_a_palette_is_clean(self):
        theme = Theme.objects.create(name='Clean')
        Theme.objects.filter(pk=theme.pk).update(
            **{
                f'light_{role}': value
                for role, value in (
                    ('primary', '#000000'), ('inverse', '#FFFFFF'), ('accent', '#FFFFFF'),
                    ('page', '#FFFFFF'), ('ink', '#000000'), ('surface', '#FFFFFF'),
                    ('muted', '#000000'), ('emphasis', '#000000'),
                )
            },
            **{
                f'dark_{role}': value
                for role, value in (
                    ('primary', '#000000'), ('inverse', '#FFFFFF'), ('accent', '#FFFFFF'),
                    ('page', '#FFFFFF'), ('ink', '#000000'), ('surface', '#FFFFFF'),
                    ('muted', '#000000'), ('emphasis', '#000000'),
                )
            },
        )
        response = self.client.get(
            reverse('admin:portfolio_app_theme_change', args=[theme.pk])
        )
        body = response.content.decode()
        self.assertIn('theme-contrast-ok', body)
        self.assertNotIn('theme-contrast-list', body)

    def test_change_form_flags_an_unreadable_palette(self):
        theme = Theme.objects.create(name='Bad')
        Theme.objects.filter(pk=theme.pk).update(
            **{f'dark_{role}': '#101010' for role, _l, _h in COLOR_ROLES}
        )
        response = self.client.get(
            reverse('admin:portfolio_app_theme_change', args=[theme.pk])
        )
        body = response.content.decode()
        self.assertIn('theme-contrast-list', body)
        self.assertIn('Top bar links', body)

    def test_check_definitions_are_embedded_for_the_live_script(self):
        theme = Theme.objects.create(name='Nord')
        response = self.client.get(
            reverse('admin:portfolio_app_theme_change', args=[theme.pk])
        )
        body = response.content.decode()
        # Escaped into the attribute, so match the escaped form.
        self.assertIn('data-checks=', body)
        self.assertIn('&quot;label&quot;', body)

    def test_embedded_checks_describe_the_same_rules_as_the_server(self):
        self.maxDiff = None
        self.assertEqual(checks_as_dicts(), [
            {
                'label': label,
                'foreground': fg,
                'background': bg,
                'minimum': minimum,
                'consequence': consequence,
            }
            for label, fg, bg, minimum, consequence in CONTRAST_CHECKS
        ])
        # Must be JSON-serialisable for the data attribute.
        json.dumps(checks_as_dicts())

    def test_saving_a_low_contrast_palette_is_allowed(self):
        # A warning, not a block: a work-in-progress palette must still save.
        theme = Theme.objects.create(name='Wip')
        payload = {
            'name': 'Wip',
            'font_heading': Theme.FontFamily.INTER,
            'font_body': Theme.FontFamily.INTER,
        }
        for role, _l, _h in COLOR_ROLES:
            payload[f'light_{role}'] = '#123456'
            payload[f'dark_{role}'] = '#123456'
        response = self.client.post(
            reverse('admin:portfolio_app_theme_change', args=[theme.pk]), payload
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Theme.objects.get(pk=theme.pk).light_primary, '#123456')
