"""WCAG 2.1 contrast helpers for theme palettes.

Deliberately free of Django imports so the maths can be unit-tested and reused
by the model, the admin and the tests without touching the database.
"""

# WCAG 2.1 thresholds: 4.5:1 for normal text (SC 1.4.3), 3:1 for non-text
# boundaries (SC 1.4.11), and a lower bar for hairlines that only need to be
# perceptible rather than a formal boundary.
TEXT_MIN = 4.5
BOUNDARY_MIN = 3.0
HAIRLINE_MIN = 1.15

# label, foreground role, background role, minimum, what goes wrong
#
# Two deliberate omissions, because in this design those boundaries are drawn by
# a border rather than by a step in tone:
#
#  * no `surface` vs `page` check — cards carry a hairline border and a minimal
#    shadow, and are meant to sit close to the page (the shipped palettes are
#    ~1.08:1 there by intent);
#  * no `primary` vs `page` check — the top bar is the same graphite as other
#    surfaces and is delineated by `border-b`, which the 'Hairlines' check below
#    covers. A dark graphite bar cannot separate from a near-black page by tone
#    without becoming a bright strip, which the design deliberately avoids.
#
# `heading` is checked as text because it is used for headings *and* for
# brand-coloured captions. `accent` is deliberately NOT checked as text: it is an
# on-dark signal colour, so readable text on a light surface must use
# heading/ink/muted instead.
CONTRAST_CHECKS = (
    ('Top bar links', 'inverse', 'primary', TEXT_MIN,
     'the navigation links will be hard to read on the bar'),
    ('Top bar hover', 'accent', 'primary', TEXT_MIN,
     'the hover colour on the navigation links will be hard to read'),
    ('Body text', 'ink', 'page', TEXT_MIN,
     'body copy will be hard to read'),
    ('Text on cards', 'ink', 'surface', TEXT_MIN,
     'text inside cards and panels will be hard to read'),
    ('Headings', 'heading', 'page', TEXT_MIN,
     'headings and brand-coloured text will be hard to read'),
    ('Headings on cards', 'heading', 'surface', TEXT_MIN,
     'headings and brand-coloured text on cards will be hard to read'),
    ('Muted text', 'muted', 'page', TEXT_MIN,
     'captions and metadata will be hard to read'),
    ('Links', 'secondary', 'page', TEXT_MIN,
     'links will be hard to read'),
    ('Links on cards', 'secondary', 'surface', TEXT_MIN,
     'links inside cards will be hard to read'),
    ('Button labels', 'inverse', 'emphasis', TEXT_MIN,
     'button labels will be hard to read'),
    ('Hairlines', 'border', 'page', HAIRLINE_MIN,
     'borders will be invisible, so panels and the top bar lose their edge'),
)


def _channels(value):
    """'#1D3557' -> (29, 53, 87). Accepts shorthand and a missing '#'. None if unusable."""
    if not value:
        return None
    raw = str(value).strip().lstrip('#')
    if len(raw) == 3:
        raw = ''.join(character * 2 for character in raw)
    if len(raw) != 6:
        return None
    try:
        return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def relative_luminance(value):
    """WCAG relative luminance, or None when the value is not a usable colour."""
    channels = _channels(value)
    if channels is None:
        return None

    def linearise(channel):
        c = channel / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    red, green, blue = (linearise(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first, second):
    """WCAG contrast ratio between two colours, or None if either is unusable."""
    luminance_a = relative_luminance(first)
    luminance_b = relative_luminance(second)
    if luminance_a is None or luminance_b is None:
        return None
    lighter, darker = max(luminance_a, luminance_b), min(luminance_a, luminance_b)
    return (lighter + 0.05) / (darker + 0.05)


def palette_contrast_warnings(palette, mode='light'):
    """Contrast problems in a ``{role: hex}`` palette.

    Returns a list of dicts rather than raising: a low-contrast palette is a
    design smell to warn about, not something to block saving on.
    """
    warnings = []
    for label, foreground, background, minimum, consequence in CONTRAST_CHECKS:
        ratio = contrast_ratio(palette.get(foreground), palette.get(background))
        if ratio is None:
            continue
        if ratio < minimum:
            warnings.append({
                'mode': mode,
                'label': label,
                'foreground': foreground,
                'background': background,
                'foreground_value': palette.get(foreground),
                'background_value': palette.get(background),
                'ratio': round(ratio, 2),
                'minimum': minimum,
                'consequence': consequence,
            })
    return warnings


def checks_as_dicts():
    """The check definitions, for handing to the admin's live client-side copy.

    Serialising from here keeps the thresholds and pairings in one place, so the
    server-rendered report and the JavaScript preview cannot drift apart.
    """
    return [
        {
            'label': label,
            'foreground': foreground,
            'background': background,
            'minimum': minimum,
            'consequence': consequence,
        }
        for label, foreground, background, minimum, consequence in CONTRAST_CHECKS
    ]
