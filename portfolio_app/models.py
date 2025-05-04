from django.db import models
from django.utils import timezone
# Create your models here.
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
