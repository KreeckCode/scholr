from django.core.cache import cache
from django.conf import settings
from functools import wraps
import hashlib
import json

def cache_key(*args, **kwargs):
    """
    Generate cache key from arguments
    """
    key_data = {
        'args': args,
        'kwargs': kwargs
    }
    key_string = json.dumps(key_data, sort_keys=True)
    key_hash = hashlib.md5(key_string.encode()).hexdigest()
    return key_hash


def cached_view(timeout=300, cache_name='default'):
    """
    Decorator to cache view results
    Usage: @cached_view(timeout=600)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key
            key = f'view:{func.__name__}:{cache_key(*args, **kwargs)}'
            
            # Try to get from cache
            result = cache.get(key, cache_name=cache_name)
            
            if result is not None:
                return result
            
            # Execute function
            result = func(*args, **kwargs)
            
            # Store in cache
            cache.set(key, result, timeout, cache_name=cache_name)
            
            return result
        
        return wrapper
    return decorator


def get_or_set_cache(key, callback, timeout=300, cache_name='default'):
    """
    Get from cache or execute callback and cache result
    """
    result = cache.get(key, cache_name=cache_name)
    
    if result is not None:
        return result
    
    result = callback()
    cache.set(key, result, timeout, cache_name=cache_name)
    
    return result


def invalidate_user_cache(user_id):
    """
    Invalidate all cache keys for a user
    """
    patterns = [
        f'user_courses:{user_id}',
        f'user_progress:{user_id}',
        f'user_enrollments:{user_id}',
        f'user_orders:{user_id}',
    ]
    
    for pattern in patterns:
        cache.delete(pattern)


def invalidate_product_cache(product_id=None):
    """
    Invalidate product catalog cache
    """
    if product_id:
        cache.delete(f'product:{product_id}')
    
    # Invalidate catalog pages
    cache.delete_pattern('product_catalog:*')
    cache.delete('active_bundles')