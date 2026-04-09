"""
Context processors for shop app

Makes cart and common data available in all templates
"""

from .cart import Cart


def cart(request):
    """
    Add cart to all templates
    
    Usage in template: {{ cart_count }}
    """
    cart = Cart(request)
    
    return {
        'cart': cart,
        'cart_count': len(cart),
        'cart_total': cart.get_total_price() if hasattr(cart, 'get_total_price') else 0,
    }


def shop_settings(request):
    """
    Add shop-specific settings to templates
    """
    from django.conf import settings
    
    return {
        'CURRENCY_SYMBOL': getattr(settings, 'CURRENCY_SYMBOL', 'R'),
        'CURRENCY_CODE': getattr(settings, 'CURRENCY_CODE', 'ZAR'),
        'SHOP_NAME': getattr(settings, 'SHOP_NAME', 'African Pastoral Academy'),
    }