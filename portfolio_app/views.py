import logging
from datetime import timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.core.mail import send_mail
from django.conf import settings
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.shortcuts import render, redirect
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from .models import (
    Category,
    Certificate,
    ContactMessage,
    Education,
    PageView,
    Profile,
    Project,
    Skill,
    WorkExperience,
)
from .forms import ContactForm
from .utils import client_ip

logger = logging.getLogger(__name__)


def index(req):
    skills = Skill.objects.all()[:3]
    # select_related/prefetch_related avoid extra queries in the card markup.
    projects = (
        Project.objects.select_related('category')
        .prefetch_related('skills_used')[:3]
    )
    profile = Profile.objects.first()

    return render(req, 'homepage.html', {
        'skills': skills,
        'projects': projects,
        'profile': profile,
    })


@ratelimit(key='ip', rate='3/h', method='POST', block=False)
def contact_view(req):
    was_limited = getattr(req, 'limited', False)

    if req.method == 'POST':
        # Rate limit check
        if was_limited:
            return render(req, 'contact.html', {
                'form': ContactForm(),
                'rate_limited': True,
            })

        # Honeypot check — if filled, silently pretend success
        if req.POST.get('url', '') != '':
            return redirect('contact_success')

        form = ContactForm(req.POST)
        if form.is_valid():
            name = form.cleaned_data['name']
            email = form.cleaned_data['email']
            message = form.cleaned_data['message']

            # Save before emailing so a mail failure never loses the message.
            ContactMessage.objects.create(
                name=name,
                email=email,
                message=message,
                ip_address=client_ip(req),
            )

            try:
                send_mail(
                    subject=f'Portfolio: New message from {name}',
                    message=f"New message from: {name} ({email})\n\n{message}",
                    from_email=settings.EMAIL_HOST_USER,
                    recipient_list=['arminmaddah.a@gmail.com'],
                )
            except Exception:
                # The message is already stored and visible in /admin/.
                logger.exception("Failed to send contact form email from %s", email)

            # POST-redirect-GET so a refresh does not resend the message
            return redirect('contact_success')
    else:
        form = ContactForm()

    return render(req, 'contact.html', {'form': form})


def contact_success(req):
    return render(req, 'success.html')


def privacy_view(req):
    """Privacy notice. Required because the site stores contact-form data and
    records anonymous visitor statistics (GDPR Art. 13)."""
    return render(req, 'privacy.html', {
        'retention_days': settings.ANALYTICS_RETENTION_DAYS,
    })


@staff_member_required
def dashboard(req):
    """Private visitor statistics. Only signed-in staff can open this."""
    days = 30
    now = timezone.now()
    since = now - timedelta(days=days)

    humans = PageView.objects.filter(is_bot=False)
    recent = humans.filter(viewed_at__gte=since)

    today = timezone.localdate()

    # Daily series, including days with no traffic so the chart has no gaps.
    counts = {
        row['day']: row['views']
        for row in recent.annotate(day=TruncDate('viewed_at'))
        .values('day')
        .annotate(views=Count('id'))
    }
    chart = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        chart.append({'day': day, 'views': counts.get(day, 0)})

    peak = max((point['views'] for point in chart), default=0) or 1
    for point in chart:
        # Rounded up so a non-zero day always draws a visible bar.
        point['percent'] = max(2, round(point['views'] / peak * 100)) if point['views'] else 0

    def top(field, limit=8):
        """Most frequent values for one column, ignoring blanks."""
        return list(
            recent.exclude(**{field: ''})
            .values(field)
            .annotate(views=Count('id'))
            .order_by('-views')[:limit]
        )

    context = {
        'window_days': days,
        'views_today': humans.filter(viewed_at__date=today).count(),
        'views_window': recent.count(),
        'views_total': humans.count(),
        'unique_today': humans.filter(viewed_at__date=today)
        .exclude(visitor_hash='')
        .values('visitor_hash')
        .distinct()
        .count(),
        'unique_window': recent.exclude(visitor_hash='')
        .values('visitor_hash')
        .distinct()
        .count(),
        'bot_views': PageView.objects.filter(is_bot=True).count(),
        'chart': chart,
        'top_pages': top('path'),
        'top_countries': top('country'),
        'devices': top('device_type', limit=5),
        'browsers': top('browser', limit=5),
        'operating_systems': top('os', limit=5),
        'referrers': top('referrer', limit=8),
        'messages_count': ContactMessage.objects.count(),
        'retention_days': settings.ANALYTICS_RETENTION_DAYS,
        'analytics_enabled': settings.ANALYTICS_ENABLED,
    }
    return render(req, 'dashboard.html', context)


def skills_view(req):
    skills = Skill.objects.all()
    return render(req, 'skills.html', {'skills': skills})


def projects_view(req):
    category_slug = req.GET.get('category', '')
    projects = Project.objects.select_related('category').prefetch_related('skills_used')
    categories = Category.objects.all()

    if category_slug:
        projects = projects.filter(category__slug=category_slug)

    return render(req, 'projects.html', {
        'projects': projects,
        'categories': categories,
        'active_category': category_slug,
    })


def resume_view(req):
    profile = Profile.objects.first()
    educations = Education.objects.all()
    work_experiences = WorkExperience.objects.all()
    skills = Skill.objects.all()
    certificates = Certificate.objects.all()

    return render(req, 'resume.html', {
        'profile': profile,
        'educations': educations,
        'experiences': work_experiences,
        'skills': skills,
        'certificates': certificates,
    })
