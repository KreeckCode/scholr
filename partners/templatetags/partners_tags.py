from django import template

from partners.models.base import country_name

register = template.Library()

NEUTRAL = 'bg-slate-100 text-slate-700 ring-slate-200'
GOOD = 'bg-teal-50 text-teal-800 ring-teal-200'
WARN = 'bg-amber-50 text-amber-800 ring-amber-200'
BAD = 'bg-red-50 text-red-700 ring-red-200'
INFO = 'bg-sky-50 text-sky-800 ring-sky-200'

TONES = {
    'active': GOOD, 'running': GOOD, 'passed': GOOD, 'present': GOOD, 'completed': GOOD,
    'launched': GOOD, 'held': GOOD, 'paid': GOOD, 'enrolled': INFO, 'planned': INFO,
    'preparing': INFO, 'draft': NEUTRAL, 'in_progress': NEUTRAL, 'queued': INFO,
    'not_invoiced': NEUTRAL, 'invoiced': WARN, 'waived': NEUTRAL, 'late': WARN, 'excused': NEUTRAL,
    'suspended': WARN, 'paused': WARN, 'incomplete': WARN, 'expired': WARN, 'withdrawn': NEUTRAL,
    'none': NEUTRAL, 'failed': BAD, 'absent': BAD, 'terminated': BAD, 'cancelled': NEUTRAL,
    'lost': BAD, 'closed': NEUTRAL, 'error': BAD, 'conflict': WARN,
}


@register.filter
def tone(value):
    """Tailwind badge classes for a status value."""
    return TONES.get(str(value), NEUTRAL)


@register.filter
def country(code):
    return country_name(code)


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.inclusion_tag('partners/_field.html')
def field(bound_field, wide=False):
    return {'field': bound_field, 'wide': wide}
