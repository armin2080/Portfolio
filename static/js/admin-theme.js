/* Live palette preview for the Themes admin (see ThemeAdmin.Media).
 *
 * Each colour field is a hex text input plus a picker swatch. This keeps the
 * two in sync, reads the current — possibly unsaved — values, and repaints both
 * the swatch strip and a small mock of the site in light and dark, so contrast
 * can be judged before saving.
 */
(function () {
    'use strict';

    var ROLES = [
        'primary', 'secondary', 'accent', 'emphasis',
        'page', 'surface', 'inverse', 'heading', 'ink', 'muted', 'border'
    ];

    var HEX = /^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

    /* Mirrors the compiled :root defaults in tailwind.src.css, so the preview
       stays sensible when a field is empty or holds something unparseable
       instead of blanking out mid-edit. */
    var LIGHT_DEFAULTS = {
        primary: '#1D3557', secondary: '#457B9D', accent: '#A8DADC',
        emphasis: '#E63946', page: '#F1FAEE', surface: '#FFFFFF',
        inverse: '#F1FAEE', heading: '#1D3557', ink: '#374151',
        muted: '#6B7280', border: '#D1D5DB'
    };

    /* '#abc' -> '#AABBCC'; '#1d3557' -> '#1D3557'. Returns null if not a colour. */
    function canonical(value) {
        var raw = (value || '').trim();
        if (!HEX.test(raw)) {
            return null;
        }
        raw = raw.replace('#', '');
        if (raw.length === 3) {
            raw = raw[0] + raw[0] + raw[1] + raw[1] + raw[2] + raw[2];
        }
        return '#' + raw.toUpperCase();
    }

    function textInput(mode, role) {
        return document.getElementById('id_' + mode + '_' + role);
    }

    /* Returns the palette, with missing/invalid entries filled from `fallback`
       so one bad value never removes a preview panel. */
    function readPalette(mode, fallback) {
        var palette = {};
        ROLES.forEach(function (role) {
            var input = textInput(mode, role);
            var value = input ? canonical(input.value) : null;
            palette[role] = value || (fallback && fallback[role]) || LIGHT_DEFAULTS[role];
        });
        return palette;
    }

    /* Repaint the server-rendered swatches (they carry data-role/data-mode). */
    function paintSwatches(palette, mode) {
        document.querySelectorAll('.theme-swatch[data-mode="' + mode + '"]').forEach(function (el) {
            var colour = palette[el.getAttribute('data-role')];
            if (colour) {
                el.style.background = colour;
            }
        });
    }

    function mockMarkup(label, palette) {
        return '' +
            '<div class="theme-mock">' +
                '<div class="theme-mock-label" style="background:' + palette.page + ';color:' + palette.muted + '">' + label + '</div>' +
                '<div class="theme-mock-nav" style="background:' + palette.primary + ';color:' + palette.inverse + '">Armin</div>' +
                '<div class="theme-mock-body" style="background:' + palette.page + ';color:' + palette.ink + '">' +
                    '<div style="background:' + palette.surface + ';border:1px solid ' + palette.border + ';border-radius:8px;padding:10px">' +
                        '<strong style="color:' + palette.heading + '">Featured project</strong>' +
                        '<span style="color:' + palette.muted + '">A short description of the work.</span>' +
                        '<span class="theme-mock-btn" style="background:' + palette.emphasis + ';color:' + palette.inverse + '">Contact me</span>' +
                    '</div>' +
                    '<span style="display:inline-block;margin-top:8px;padding:2px 8px;border-radius:99px;background:' + palette.accent + ';color:' + palette.heading + '">Python</span>' +
                '</div>' +
            '</div>';
    }

    function render() {
        // Dark falls back to the light values, and light to the compiled
        // defaults, so an empty or invalid field never blanks a panel.
        var light = readPalette('light', LIGHT_DEFAULTS);
        var dark = readPalette('dark', light);

        paintSwatches(light, 'light');
        paintSwatches(dark, 'dark');

        var host = document.getElementById('theme-live-mock');
        if (host) {
            host.innerHTML = mockMarkup('Light', light) + mockMarkup('Dark', dark);
        }

        renderContrast(light, dark);
    }

    /* --- Contrast checking -------------------------------------------------
       Mirrors portfolio_app/contrast.py. The pairings and thresholds are not
       duplicated here: they arrive from the server in data-checks, so the
       live report and the saved-value report cannot disagree.
       ---------------------------------------------------------------------- */

    function luminance(hex) {
        var raw = (hex || '').replace('#', '');
        if (raw.length !== 6) {
            return null;
        }
        var channels = [0, 2, 4].map(function (i) {
            var c = parseInt(raw.substr(i, 2), 16) / 255;
            return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
        });
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
    }

    function contrast(a, b) {
        var la = luminance(a);
        var lb = luminance(b);
        if (la === null || lb === null) {
            return null;
        }
        var lighter = Math.max(la, lb);
        var darker = Math.min(la, lb);
        return (lighter + 0.05) / (darker + 0.05);
    }

    function readChecks() {
        var container = document.getElementById('theme-preview');
        if (!container || !container.dataset.checks) {
            return [];
        }
        try {
            return JSON.parse(container.dataset.checks);
        } catch (e) {
            return [];
        }
    }

    function escapeHtml(value) {
        return String(value).replace(/[&<>"']/g, function (ch) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
        });
    }

    function renderContrast(light, dark) {
        var report = document.getElementById('theme-contrast-report');
        if (!report) {
            return;
        }
        var checks = readChecks();
        var problems = [];

        [['Light', light], ['Dark', dark]].forEach(function (pair) {
            var mode = pair[0];
            var palette = pair[1];
            checks.forEach(function (check) {
                var ratio = contrast(palette[check.foreground], palette[check.background]);
                if (ratio === null || ratio >= check.minimum) {
                    return;
                }
                problems.push(
                    '<li><strong>' + escapeHtml(mode + ' \u00b7 ' + check.label) + '</strong> \u2014 ' +
                    escapeHtml(check.consequence) +
                    ' <span class="theme-contrast-ratio">' + ratio.toFixed(2) + ':1, needs ' +
                    check.minimum.toFixed(1) + ':1</span></li>'
                );
            });
        });

        if (!problems.length) {
            report.innerHTML = '<p class="theme-contrast-ok">No contrast problems detected.</p>';
        } else {
            report.innerHTML = '<ul class="theme-contrast-list">' + problems.join('') + '</ul>';
        }
    }

    /* Text field -> picker. Flags unparseable input without blocking typing. */
    function syncFromText(input) {
        var value = canonical(input.value);
        input.classList.toggle('portfolio-hex-invalid', input.value.trim() !== '' && !value);

        var picker = document.querySelector('.portfolio-color-picker[data-for="' + input.id + '"]');
        if (picker && value) {
            picker.value = value;
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        if (!document.getElementById('theme-preview')) {
            return; // Not on a theme form.
        }

        // Ensure the mock container exists next to the swatches.
        var preview = document.getElementById('theme-preview');
        if (preview && !document.getElementById('theme-live-mock')) {
            var host = document.createElement('div');
            host.id = 'theme-live-mock';
            preview.appendChild(host);
        }

        // Picker -> text field (then the text field drives the preview).
        document.querySelectorAll('.portfolio-color-picker').forEach(function (picker) {
            picker.addEventListener('input', function () {
                var input = document.getElementById(picker.getAttribute('data-for'));
                if (input) {
                    input.value = picker.value.toUpperCase();
                    input.classList.remove('portfolio-hex-invalid');
                }
                render();
            });
        });

        // Text field -> picker + preview.
        document.querySelectorAll('.portfolio-hex-input').forEach(function (input) {
            input.addEventListener('input', function () {
                syncFromText(input);
                render();
            });
            // Tidy the value into canonical form when leaving the field.
            input.addEventListener('blur', function () {
                var value = canonical(input.value);
                if (value) {
                    input.value = value;
                    input.classList.remove('portfolio-hex-invalid');
                }
                render();
            });
            syncFromText(input);
        });

        render();
    });
})();

