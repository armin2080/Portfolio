from django import template
from django.utils import timezone
from dateutil.relativedelta import relativedelta

register = template.Library()


@register.filter
def splitlines(value):
    """Split a string by newlines and return a list."""
    if value:
        return value.split('\n')
    return []


@register.filter
def years_since(value):
    """Calculate full years from a date to now. Auto-updates live."""
    if not value:
        return 0
    return relativedelta(timezone.now().date(), value).years


@register.filter
def years_since_display(value):
    """Return a string like '3 years' or '1 year'."""
    years = years_since(value)
    if years == 0:
        return "Less than a year"
    return f"{years} year{'s' if years != 1 else ''}"
