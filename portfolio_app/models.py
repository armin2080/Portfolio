import re

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator, RegexValidator


class Profile(models.Model):
    name = models.CharField(max_length=100, default="Armin")
    title = models.CharField(max_length=200, default="Data Scientist & Developer")
    bio = models.TextField(default="I transform complex data into actionable insights and build intelligent solutions.")
    professional_summary = models.TextField(blank=True, help_text="Detailed summary shown on the resume page")
    profile_picture = models.ImageField(upload_to='profile/', blank=True, null=True)
    
    # Contact info (no more hardcoding in templates)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    location = models.CharField(max_length=200, blank=True)
    
    # Social links
    linkedin_url = models.URLField(blank=True)
    github_url = models.URLField(blank=True)
    telegram_url = models.URLField(blank=True)
    whatsapp_url = models.URLField(blank=True)
    
    # Resume files
    resume_en = models.FileField(upload_to='resumes/', blank=True, null=True)
    resume_de = models.FileField(upload_to='resumes/', blank=True, null=True)
    
    available_for_hire = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Profile"
        verbose_name_plural = "Profile"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.pk and Profile.objects.exists():
            raise ValueError("Only one profile can exist")
        return super().save(*args, **kwargs)


class Skill(models.Model):
    class SkillType(models.TextChoices):
        TECHNICAL = 'Technical', 'Technical'
        SOFT = 'Soft', 'Soft'

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    start_date = models.DateField(help_text="When I started working with this skill")
    skill_type = models.CharField(
        max_length=20,
        choices=SkillType.choices,
        default=SkillType.TECHNICAL,
    )
    is_published = models.BooleanField(
        default=True,
        help_text=(
            "Untick to hide this skill from the site. The GitHub sync creates a "
            "newly detected skill unticked, so nothing appears publicly until you "
            "have reviewed it and written a description."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['start_date']

    def __str__(self):
        return self.name


class SkillSignal(models.Model):
    """A detectable sign in a GitHub repository that implies a skill.

    The sync looks for these in every repository it imports, and links the
    matching skills to the project. Rules live in the database rather than in
    code so a mapping can be added from the admin without a deploy.

    `skill_name` is matched against ``Skill.name`` (case-insensitively). When no
    skill has that name yet the sync creates one, unpublished, so a newly
    detected technology shows up for review instead of appearing on the site.
    """

    class Kind(models.TextChoices):
        PATH = 'path', 'File or folder in the repository'
        DEPENDENCY = 'dependency', 'Package in a dependency file'
        LANGUAGE = 'language', "GitHub's main language"

    kind = models.CharField(
        max_length=16, choices=Kind.choices, default=Kind.PATH,
        help_text="What to look at in the repository.",
    )
    pattern = models.CharField(
        max_length=100,
        help_text=(
            "File rules use a glob, matched against every path and against each "
            "path segment: <code>manage.py</code>, <code>*.ipynb</code>, "
            "<code>.github/workflows/*</code>. Package rules match the start of "
            "a name in requirements.txt, pyproject.toml, Pipfile or "
            "package.json, so <code>psycopg2</code> finds "
            "<code>psycopg2-binary</code> and <code>django</code> finds "
            "<code>djangorestframework</code>. Keep a package rule specific: a "
            "short pattern over-matches. Language rules compare "
            "GitHub's reported language."
        ),
    )
    skill_name = models.CharField(
        max_length=100,
        help_text=(
            "The skill to attach, matched by name. Created (hidden) if no skill "
            "has this name yet."
        ),
    )
    note = models.CharField(
        max_length=200, blank=True,
        help_text="Optional note for your own reference.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Untick to stop using this rule without deleting it.",
    )

    class Meta:
        ordering = ['kind', 'pattern', 'skill_name']
        constraints = [
            models.UniqueConstraint(
                fields=['kind', 'pattern', 'skill_name'], name='unique_skill_signal'
            ),
        ]

    def __str__(self):
        return f'{self.get_kind_display()}: {self.pattern} \u2192 {self.skill_name}'


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ['name']

    def __str__(self):
        return self.name


class Project(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(
        blank=True,
        help_text=(
            "Shown on the project card. Filled in from GitHub on first import; "
            "if the repository has no description this is left blank."
        ),
    )
    link = models.URLField()
    image = models.ImageField(
        upload_to='projects/', blank=True, null=True,
        help_text="Optional. Cards show a placeholder until a screenshot is uploaded.",
    )
    skills_used = models.ManyToManyField(Skill, related_name='projects', blank=True)
    auto_skills = models.BooleanField(
        default=True,
        help_text=(
            "Keep the skill tags above in step with what is actually in the "
            "repository. The sync adds and removes tags to match, so a tag you "
            "remove by hand comes back on the next run. Untick this on a project "
            "whose tags you want to manage yourself."
        ),
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='projects',
    )
    date = models.DateField(default=timezone.now)

    # --- GitHub sync -------------------------------------------------------
    # Set only on projects imported from GitHub. `github_repo_id` is GitHub's
    # numeric id, which stays the same when a repository is renamed, so it is a
    # safer match key than the name.
    github_repo_id = models.BigIntegerField(
        null=True, blank=True, unique=True,
        help_text="GitHub's numeric repository id; how the sync recognises this project.",
    )
    github_full_name = models.CharField(
        max_length=200, blank=True,
        help_text="owner/repo on GitHub, for reference.",
    )
    github_synced_at = models.DateTimeField(
        null=True, blank=True, help_text="When this project was last seen by the sync.",
    )
    github_pushed_at = models.CharField(
        max_length=40, blank=True,
        help_text=(
            "GitHub's pushed_at as last seen. Reading a repository's contents "
            "costs API requests, so an unchanged repository is skipped."
        ),
    )

    is_published = models.BooleanField(
        default=True,
        help_text=(
            "Untick to hide this project from the site. The GitHub sync never "
            "changes this, so hidden projects stay hidden. "
            "Note: deleting a GitHub project does not remove it permanently — "
            "the next sync will import it again. Untick this instead."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return self.name


class Education(models.Model):
    degree = models.CharField(max_length=200, help_text="e.g. Bachelor of Science in Computer Science")
    institution = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.degree} @ {self.institution}"


class WorkExperience(models.Model):
    role = models.CharField(max_length=200)
    company = models.CharField(max_length=200)
    location = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Work Experiences"
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.role} @ {self.company}"


class Certificate(models.Model):
    name = models.CharField(max_length=200)
    institution = models.CharField(max_length=200)
    link = models.URLField(blank=True)
    issue_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-issue_date']

    def __str__(self):
        return self.name


class ContactMessage(models.Model):
    """A message submitted through the contact form.

    Stored as well as emailed, so a mail outage never loses a message.
    """

    name = models.CharField(max_length=100)
    email = models.EmailField()
    message = models.TextField()
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Contact message"
        verbose_name_plural = "Contact messages"

    def __str__(self):
        return f"{self.name} <{self.email}>"


class PageView(models.Model):
    """A single anonymous page view, used for the private dashboard.

    Intentionally stores no personal data: no IP address, no cookies, no user
    id. ``visitor_hash`` is a daily-rotating pseudonym (see
    ``portfolio_app.analytics.visitor_hash``), so unique visitors can be counted
    per day without being able to follow anyone across days.
    """

    class Device(models.TextChoices):
        DESKTOP = 'desktop', 'Desktop'
        MOBILE = 'mobile', 'Mobile'
        TABLET = 'tablet', 'Tablet'
        UNKNOWN = 'unknown', 'Unknown'

    path = models.CharField(max_length=255, db_index=True)
    viewed_at = models.DateTimeField(default=timezone.now, db_index=True)
    country = models.CharField(
        max_length=2, blank=True,
        help_text="ISO country code from Cloudflare (blank when unknown)",
    )
    device_type = models.CharField(max_length=10, choices=Device.choices, blank=True)
    browser = models.CharField(max_length=30, blank=True)
    os = models.CharField(max_length=20, blank=True)
    referrer = models.CharField(max_length=100, blank=True)
    visitor_hash = models.CharField(
        max_length=16, blank=True,
        help_text="Rotates daily; cannot be used to track a visitor over time",
    )
    is_bot = models.BooleanField(default=False)

    class Meta:
        ordering = ['-viewed_at']
        verbose_name = "Page view"
        verbose_name_plural = "Page views"
        indexes = [
            models.Index(fields=['-viewed_at']),
            models.Index(fields=['is_bot', '-viewed_at']),
        ]

    def __str__(self):
        return f"{self.path} @ {self.viewed_at:%Y-%m-%d %H:%M}"


# ---------------------------------------------------------------------------
# Theming
# ---------------------------------------------------------------------------

# Hex colours only: these values are interpolated into a <style> block, so a
# permissive field would be a CSS/HTML injection vector. The three-digit
# shorthand and a missing '#' are accepted here and canonicalised to '#RRGGBB'
# by normalize_hex_color() before anything is stored or emitted.
HEX_COLOR_VALIDATOR = RegexValidator(
    regex=r'^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$',
    message=(
        'Enter a hex colour such as #1D3557 (or the shorthand #abc). '
        'Letters A-F and digits 0-9 only.'
    ),
)

# role -> (label, help text). The template classes (bg-primary, text-muted, ...)
# map onto these, so a theme is a palette of roles rather than colour names.
COLOR_ROLES = (
    ('primary', 'Primary', 'Navigation, footer and primary buttons'),
    ('secondary', 'Secondary', 'Links and secondary actions'),
    ('accent', 'Accent', 'Soft highlights, badges and subtle section backgrounds'),
    ('emphasis', 'Emphasis', 'Strong accents and calls to action'),
    ('page', 'Page background', 'The overall page background'),
    ('surface', 'Surface', 'Cards and panels'),
    ('inverse', 'Inverse text', 'Text drawn on top of Primary'),
    ('heading', 'Headings', 'Headings and brand-coloured emphasis text'),
    ('ink', 'Body text', 'Main body copy'),
    ('muted', 'Muted text', 'Secondary text, captions and metadata'),
    ('border', 'Border', 'Dividers and input borders'),
)

# Whitelisted font stacks. Stored as a key so no arbitrary CSS can reach the
# page through the font fields.
FONT_STACKS = {
    'inter': "'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif",
    'source-serif': "'Source Serif 4', Georgia, 'Times New Roman', serif",
    'jetbrains-mono': "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
    'system': "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
}

# Values matching the colours the site shipped with, so an installation without
# a Theme row still renders exactly as before.
DEFAULT_LIGHT_PALETTE = {
    'primary': '#1D3557',
    'secondary': '#457B9D',
    'accent': '#A8DADC',
    'emphasis': '#E63946',
    'page': '#F1FAEE',
    'surface': '#FFFFFF',
    'inverse': '#F1FAEE',
    # Headings keep their brand colour in light mode...
    'heading': '#1D3557',
    'ink': '#374151',
    'muted': '#6B7280',
    'border': '#D1D5DB',
}

DEFAULT_DARK_PALETTE = {
    # `primary` is the chrome colour: the top bar, footer and section bands. It is
    # deliberately lighter than `page` so those surfaces read as distinct bands
    # rather than blending into the near-black background.
    'primary': '#2B3641',
    'secondary': '#457B9D',
    'accent': '#A8DADC',
    'emphasis': '#E63946',
    'page': '#1A1A2E',
    'surface': '#16213E',
    'inverse': '#F1FAEE',
    # ...but must go light in dark mode: a dark primary on a dark page would be
    # unreadable. This is why headings cannot simply reuse the `primary` token.
    'heading': '#E2E8F0',
    'ink': '#CBD5E1',
    'muted': '#94A3B8',
    'border': '#334155',
}


# role -> help text, so field definitions look it up by name. Indexing
# COLOR_ROLES positionally is fragile: inserting a role silently shifted the
# help text of every field after it.
ROLE_HELP = {role: help_text for role, _label, help_text in COLOR_ROLES}

# Accepted spellings of a hex colour, with or without the leading '#'.
_FULL_HEX = re.compile(r'^#?([0-9a-fA-F]{6})$')
_SHORT_HEX = re.compile(r'^#?([0-9a-fA-F]{3})$')


def normalize_hex_color(value):
    """Normalise a hex colour to the canonical ``#RRGGBB`` form.

    Accepts the spellings people actually type — ``#1d3557``, ``1d3557``,
    ``#1D3557`` and the three-digit shorthand ``#abc`` — and returns uppercase
    with a leading '#'. Raises ``ValidationError`` for anything else, so a
    non-colour value can never reach the stylesheet.
    """
    if value is None:
        raise ValidationError('Enter a colour as a hex value.')

    candidate = str(value).strip()

    match = _FULL_HEX.match(candidate)
    if not match:
        short = _SHORT_HEX.match(candidate)
        if short:
            # '#abc' -> '#AABBCC'
            candidate = '#' + ''.join(ch * 2 for ch in short.group(1))
            match = _FULL_HEX.match(candidate)

    if not match:
        raise ValidationError(
            'Enter a colour as a hex value, for example #1D3557.'
        )

    return '#' + match.group(1).upper()


def hex_to_rgb_channels(value):
    """'#1D3557' -> '29 53 87'.

    Tailwind cannot apply an opacity modifier to an opaque hex, so the custom
    properties hold raw channels and the config wraps them in rgb(... / <alpha>).

    Normalises first, so a shorthand or '#'-less value still resolves correctly
    and anything unparseable degrades to black rather than breaking the
    stylesheet.
    """
    try:
        value = normalize_hex_color(value)
    except ValidationError:
        return '0 0 0'
    value = value.lstrip('#')
    return f"{int(value[0:2], 16)} {int(value[2:4], 16)} {int(value[4:6], 16)}"


# Cache key for the active theme, read on every request by the context
# processor. Invalidated here so an admin edit takes effect immediately.
THEME_CACHE_KEY = 'portfolio:active-theme'


class Theme(models.Model):
    """A colour palette (light + dark) and typography, editable in the admin.

    The values are published as CSS custom properties; the Tailwind colour
    utilities resolve to those variables, so activating a theme restyles the
    site immediately with no rebuild and no restart.
    """

    class FontFamily(models.TextChoices):
        INTER = 'inter', 'Inter — clean sans-serif'
        SOURCE_SERIF = 'source-serif', 'Source Serif 4 — serif (editorial)'
        JETBRAINS_MONO = 'jetbrains-mono', 'JetBrains Mono — monospace'
        SYSTEM = 'system', 'System default'

    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(
        default=False,
        help_text='Only one theme can be active; activating this one deactivates the others.',
    )

    # --- Light palette ---
    light_primary = models.CharField(
        'Primary', max_length=7, default=DEFAULT_LIGHT_PALETTE['primary'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['primary'],
    )
    light_secondary = models.CharField(
        'Secondary', max_length=7, default=DEFAULT_LIGHT_PALETTE['secondary'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['secondary'],
    )
    light_accent = models.CharField(
        'Accent', max_length=7, default=DEFAULT_LIGHT_PALETTE['accent'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['accent'],
    )
    light_emphasis = models.CharField(
        'Emphasis', max_length=7, default=DEFAULT_LIGHT_PALETTE['emphasis'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['emphasis'],
    )
    light_page = models.CharField(
        'Page background', max_length=7, default=DEFAULT_LIGHT_PALETTE['page'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['page'],
    )
    light_surface = models.CharField(
        'Surface', max_length=7, default=DEFAULT_LIGHT_PALETTE['surface'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['surface'],
    )
    light_inverse = models.CharField(
        'Inverse text', max_length=7, default=DEFAULT_LIGHT_PALETTE['inverse'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['inverse'],
    )
    light_heading = models.CharField(
        'Headings', max_length=7, default=DEFAULT_LIGHT_PALETTE['heading'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['heading'],
    )
    light_ink = models.CharField(
        'Body text', max_length=7, default=DEFAULT_LIGHT_PALETTE['ink'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['ink'],
    )
    light_muted = models.CharField(
        'Muted text', max_length=7, default=DEFAULT_LIGHT_PALETTE['muted'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['muted'],
    )
    light_border = models.CharField(
        'Border', max_length=7, default=DEFAULT_LIGHT_PALETTE['border'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['border'],
    )

    # --- Dark palette ---
    dark_primary = models.CharField(
        'Primary', max_length=7, default=DEFAULT_DARK_PALETTE['primary'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['primary'],
    )
    dark_secondary = models.CharField(
        'Secondary', max_length=7, default=DEFAULT_DARK_PALETTE['secondary'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['secondary'],
    )
    dark_accent = models.CharField(
        'Accent', max_length=7, default=DEFAULT_DARK_PALETTE['accent'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['accent'],
    )
    dark_emphasis = models.CharField(
        'Emphasis', max_length=7, default=DEFAULT_DARK_PALETTE['emphasis'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['emphasis'],
    )
    dark_page = models.CharField(
        'Page background', max_length=7, default=DEFAULT_DARK_PALETTE['page'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['page'],
    )
    dark_surface = models.CharField(
        'Surface', max_length=7, default=DEFAULT_DARK_PALETTE['surface'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['surface'],
    )
    dark_inverse = models.CharField(
        'Inverse text', max_length=7, default=DEFAULT_DARK_PALETTE['inverse'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['inverse'],
    )
    dark_heading = models.CharField(
        'Headings', max_length=7, default=DEFAULT_DARK_PALETTE['heading'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['heading'],
    )
    dark_ink = models.CharField(
        'Body text', max_length=7, default=DEFAULT_DARK_PALETTE['ink'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['ink'],
    )
    dark_muted = models.CharField(
        'Muted text', max_length=7, default=DEFAULT_DARK_PALETTE['muted'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['muted'],
    )
    dark_border = models.CharField(
        'Border', max_length=7, default=DEFAULT_DARK_PALETTE['border'],
        validators=[HEX_COLOR_VALIDATOR], help_text=ROLE_HELP['border'],
    )

    # --- Typography ---
    font_heading = models.CharField(
        max_length=20, choices=FontFamily.choices, default=FontFamily.INTER,
        help_text='Used for headings and the site name.',
    )
    font_body = models.CharField(
        max_length=20, choices=FontFamily.choices, default=FontFamily.INTER,
        help_text='Used for body copy and interface text.',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_active', 'name']
        verbose_name = "Theme"
        verbose_name_plural = "Themes"

    def __str__(self):
        return f"{self.name}{' (active)' if self.is_active else ''}"

    def save(self, *args, **kwargs):
        # Store colours in one canonical form ('#RRGGBB') regardless of how they
        # were typed, so the database and the generated CSS are always consistent.
        for mode in ('light', 'dark'):
            for role, _label, _help in COLOR_ROLES:
                field = f'{mode}_{role}'
                try:
                    setattr(self, field, normalize_hex_color(getattr(self, field)))
                except ValidationError:
                    # Leave it alone; full_clean()/the form reports the problem.
                    pass

        super().save(*args, **kwargs)
        if self.is_active:
            # Exactly one active theme. Uses .update() to avoid recursion; the
            # cache is cleared below so the change is picked up on next request.
            Theme.objects.exclude(pk=self.pk).filter(is_active=True).update(is_active=False)
        cache.delete(THEME_CACHE_KEY)

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        cache.delete(THEME_CACHE_KEY)
        return result

    @classmethod
    def active(cls):
        """The active theme, or None when nothing is configured yet."""
        return cls.objects.filter(is_active=True).first()

    def light_palette(self):
        return {role: getattr(self, f'light_{role}') for role, _label, _help in COLOR_ROLES}

    def dark_palette(self):
        return {role: getattr(self, f'dark_{role}') for role, _label, _help in COLOR_ROLES}

    def css_variables(self):
        """The :root and .dark custom-property blocks for this theme.

        Safe to mark as ``|safe`` in the template: every colour is validated as
        a 6-digit hex value and every font comes from the whitelist above, so no
        attacker-controlled text can reach the style element.
        """
        lines = [':root {']
        for role, _label, _help in COLOR_ROLES:
            lines.append(f'  --c-{role}: {hex_to_rgb_channels(self.light_palette()[role])};')
        lines.append(f'  --font-heading: {FONT_STACKS[self.font_heading]};')
        lines.append(f'  --font-body: {FONT_STACKS[self.font_body]};')
        lines.append('}')
        lines.append('')
        lines.append('.dark {')
        for role, _label, _help in COLOR_ROLES:
            lines.append(f'  --c-{role}: {hex_to_rgb_channels(self.dark_palette()[role])};')
        lines.append('}')
        return '\n'.join(lines)

    def contrast_warnings(self):
        """Readability problems in either palette (see portfolio_app.contrast).

        Advisory only — a low-contrast palette is saved but reported in the admin.
        """
        from .contrast import palette_contrast_warnings

        return (
            palette_contrast_warnings(self.light_palette(), mode='light')
            + palette_contrast_warnings(self.dark_palette(), mode='dark')
        )
