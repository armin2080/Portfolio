from django.db import models
from django.utils import timezone

# Create your models here.
class Profile(models.Model):
    name = models.CharField(max_length=100, default="Armin")
    title = models.CharField(max_length=200, default="Data Scientist & Developer")
    bio = models.TextField(default="I transform complex data into actionable insights and build intelligent solutions.")
    profile_picture = models.ImageField(upload_to='profile/', blank=True, null=True)
    available_for_hire = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Profile"
        verbose_name_plural = "Profile"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Ensure only one profile exists
        if not self.pk and Profile.objects.exists():
            raise ValueError("Only one profile can exist")
        return super(Profile, self).save(*args, **kwargs)

class Skill(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField()
    proficiency = models.IntegerField(choices=((i,i) for i in range(1, 101)))
    skill_type = models.CharField(max_length=100, choices=[('Technical', 'Technical'), ('Soft', 'Soft')], default='Technical')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


    def __str__(self):
        return self.name
    
class Project(models.Model):
    name = models.CharField(max_length=100)
    preview = models.TextField()
    description = models.TextField()
    link = models.URLField()
    skills_used = models.ManyToManyField(Skill, related_name='projects')
    date = models.DateField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    category = models.CharField(max_length=100)

    def __str__(self):
        return self.name
    
class Education(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField()
    institution = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
    
class WorkExperience(models.Model):
    role = models.CharField(max_length=100)
    company = models.CharField(max_length=100)
    location = models.CharField(max_length=100)
    description = models.TextField()
    start_date = models.DateField()
    end_date = models.DateField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.role
    

class Certificate(models.Model):
    name = models.CharField(max_length=100)
    institution = models.CharField(max_length=100)
    link = models.URLField()
    issue_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
