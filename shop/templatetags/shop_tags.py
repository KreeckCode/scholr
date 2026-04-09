"""
Custom template tags for shop

Usage:
{% load shop_tags %}
{{ product.price|currency }}
"""

from django import template
from decimal import Decimal

register = template.Library()


@register.filter
def currency(value, symbol='R'):
    """
    Format currency
    
    Usage: {{ product.price|currency }}
    Output: R 1,250.00
    """
    try:
        value = Decimal(str(value))
        return f"{symbol} {value:,.2f}"
    except:
        return f"{symbol} 0.00"


@register.filter
def multiply(value, arg):
    """
    Multiply filter
    
    Usage: {{ item.price|multiply:item.quantity }}
    """
    try:
        return Decimal(str(value)) * Decimal(str(arg))
    except:
        return 0


@register.filter
def percentage(value, total):
    """
    Calculate percentage
    
    Usage: {{ completed|percentage:total }}
    """
    try:
        if Decimal(str(total)) == 0:
            return 0
        return (Decimal(str(value)) / Decimal(str(total))) * 100
    except:
        return 0


@register.simple_tag
def price_display(product):
    """
    Display product price with FREE indicator
    
    Usage: {% price_display product %}
    """
    if product.is_free:
        return '<span class="text-green-600 font-bold">FREE</span>'
    return f'<span class="text-gray-900">R {product.price:,.2f}</span>'