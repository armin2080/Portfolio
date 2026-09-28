from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator


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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['start_date']

    def __str__(self):
        return self.name


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
    description = models.TextField(help_text="Project description")
    link = models.URLField()
    image = models.ImageField(upload_to='projects/', blank=True, null=True)
    skills_used = models.ManyToManyField(Skill, related_name='projects', blank=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='projects',
    )
    date = models.DateField(default=timezone.now)
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
