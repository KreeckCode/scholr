import csv
import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


def client_ip(request):
    """
    The address used for rate limiting.
    X-Real-IP is set by Nginx itself; failing that, the last X-Forwarded-For hop is the one
    the proxy added. The first hop is never used because any client can write it.
    """
    real_ip = (request.META.get('HTTP_X_REAL_IP') or '').strip()
    if real_ip:
        return real_ip
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[-1].strip()
    return request.META.get('REMOTE_ADDR', '')


def rate_limited(key, limit, window_seconds):
    """
    Fixed-window counter in the cache. Returns True once `limit` hits have been made in the window.
    cache.add only sets the key when it is missing, so the window starts on the first hit.
    """
    cache_key = f'partners:rl:{key}'
    if cache.add(cache_key, 1, window_seconds):
        return False
    try:
        count = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, window_seconds)
        return False
    return count > limit


FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def safe_cell(value):
    """Neutralise spreadsheet formulas in exported text so a name like '=HYPERLINK(...)' stays text."""
    if value is None:
        return ''
    text = str(value)
    if text.startswith(FORMULA_PREFIXES):
        return f"'{text}"
    return text


def write_csv(response, header, rows):
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(value) for value in row])
    return response


def enqueue(task, *args):
    """
    Send a Celery task without letting a broker outage break the request.
    The data change has already been committed; a lost notification is logged instead.
    """
    try:
        task.delay(*args)
    except Exception:
        logger.exception('Could not queue %s%s', getattr(task, 'name', task), args)
        return False
    return True
