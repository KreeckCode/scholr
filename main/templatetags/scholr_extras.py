from django import template

register = template.Library()

@register.filter
def split(value, arg=','):
    return value.split(arg)


@register.filter
def translated(value):
    """Translate English text that came from Python, such as a status name."""
    from scholr.i18n import translated as translate
    return translate(value)
