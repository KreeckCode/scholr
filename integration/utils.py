"""
Utility functions for Moodle integration
Helper methods for common operations
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from django.utils import timezone
from django.db.models import Count, Q

logger = logging.getLogger(__name__)


def format_moodle_timestamp(dt: datetime) -> int:
    """
    Convert Python datetime to Moodle timestamp (Unix timestamp)
    
    Args:
        dt: Python datetime object
        
    Returns:
        Unix timestamp as integer
    """
    if not dt:
        return 0
    
    # Ensure timezone-aware
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    
    return int(dt.timestamp())


def parse_moodle_timestamp(timestamp: int) -> Optional[datetime]:
    """
    Convert Moodle timestamp to Python datetime
    
    Args:
        timestamp: Unix timestamp
        
    Returns:
        Timezone-aware datetime or None
    """
    if not timestamp or timestamp == 0:
        return None
    
    try:
        dt = datetime.fromtimestamp(timestamp)
        return timezone.make_aware(dt)
    except (ValueError, OSError):
        logger.warning(f"Invalid timestamp: {timestamp}")
        return None


def sanitize_username(username: str) -> str:
    """
    Sanitize username for Moodle compatibility
    Removes special characters, converts to lowercase
    
    Args:
        username: Original username
        
    Returns:
        Sanitized username
    """
    # Remove special characters except alphanumeric, underscore, hyphen, dot
    sanitized = ''.join(
        c for c in username 
        if c.isalnum() or c in ['_', '-', '.']
    )
    
    # Convert to lowercase
    sanitized = sanitized.lower()
    
    # Ensure starts with letter or number
    if sanitized and not sanitized[0].isalnum():
        sanitized = 'u' + sanitized
    
    return sanitized


def generate_default_password(username: str) -> str:
    """
    Generate a default password for Moodle users
    Format: JH{username}123!
    
    Args:
        username: User's username
        
    Returns:
        Default password
    """
    return f"JH{username}123!"


def get_country_code(country_name: str) -> str:
    """
    Convert country name to ISO 2-letter code
    Extended mapping for African countries
    
    Args:
        country_name: Full country name
        
    Returns:
        2-letter country code
    """
    country_map = {
        'south africa': 'ZA',
        'kenya': 'KE',
        'uganda': 'UG',
        'tanzania': 'TZ',
        'zimbabwe': 'ZW',
        'zambia': 'ZM',
        'malawi': 'MW',
        'mozambique': 'MZ',
        'botswana': 'BW',
        'namibia': 'NA',
        'nigeria': 'NG',
        'ghana': 'GH',
        'ethiopia': 'ET',
        'rwanda': 'RW',
        'burundi': 'BI',
        'democratic republic of congo': 'CD',
        'congo': 'CG',
        'cameroon': 'CM',
        'ivory coast': 'CI',
        'senegal': 'SN',
        'mali': 'ML',
        'niger': 'NE',
        'chad': 'TD',
        'sudan': 'SD',
        'south sudan': 'SS',
        'egypt': 'EG',
        'morocco': 'MA',
        'algeria': 'DZ',
        'tunisia': 'TN',
        'libya': 'LY',
    }
    
    return country_map.get(country_name.lower().strip(), 'ZA')


def chunk_list(lst: List, chunk_size: int) -> List[List]:
    """
    Split list into chunks for batch processing
    
    Args:
        lst: List to chunk
        chunk_size: Size of each chunk
        
    Returns:
        List of chunks
    """
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


def calculate_sync_priority(entity_type: str, entity) -> int:
    """
    Calculate priority score for sync operations
    Higher score = higher priority
    
    Args:
        entity_type: Type of entity (course, user, enrollment)
        entity: The entity object
        
    Returns:
        Priority score (0-100)
    """
    priority = 50  # Default
    
    if entity_type == 'course':
        # Higher priority for visible courses
        if entity.visible:
            priority += 20
        
        # Higher priority for auto-enroll courses
        if entity.auto_enroll_enabled:
            priority += 15
        
        # Higher priority for failed syncs
        if entity.sync_status == 'failed':
            priority += 10
    
    elif entity_type == 'user':
        # Higher priority for recently verified users
        if hasattr(entity, 'verified_at') and entity.verified_at:
            days_since_verification = (timezone.now() - entity.verified_at).days
            if days_since_verification < 7:
                priority += 20
    
    elif entity_type == 'enrollment':
        # Higher priority for active enrollments
        if entity.is_active:
            priority += 15
    
    return min(priority, 100)


def format_duration(seconds: int) -> str:
    """
    Format duration in seconds to human-readable string
    
    Args:
        seconds: Duration in seconds
        
    Returns:
        Formatted string (e.g., "2m 30s")
    """
    if not seconds:
        return "0s"
    
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}s")
    
    return " ".join(parts)


def get_sync_statistics(days: int = 30) -> Dict:
    """
    Get sync statistics for the past N days
    
    Args:
        days: Number of days to analyze
        
    Returns:
        Dictionary with statistics
    """
    from .models import SyncLog
    
    since = timezone.now() - timedelta(days=days)
    
    logs = SyncLog.objects.filter(started_at__gte=since)
    
    stats = {
        'total_syncs': logs.count(),
        'successful_syncs': logs.filter(status='completed').count(),
        'failed_syncs': logs.filter(status='failed').count(),
        'partial_syncs': logs.filter(status='partial').count(),
        'total_items_synced': sum(log.successful_items for log in logs),
        'total_items_failed': sum(log.failed_items for log in logs),
        'average_duration': 0,
        'by_type': {},
    }
    
    # Calculate average duration
    completed_logs = logs.filter(
        status__in=['completed', 'partial'],
        duration_seconds__isnull=False
    )
    
    if completed_logs.exists():
        total_duration = sum(log.duration_seconds for log in completed_logs)
        stats['average_duration'] = total_duration // completed_logs.count()
    
    # Group by sync type
    by_type = logs.values('sync_type').annotate(
        count=Count('id')
    ).order_by('-count')
    
    for item in by_type:
        stats['by_type'][item['sync_type']] = item['count']
    
    return stats


def validate_sync_readiness() -> Dict:
    """
    Check if system is ready for sync
    Returns dict with readiness status and issues
    
    Returns:
        Dict with 'ready' bool and 'issues' list
    """
    from .models import MoodleInstance, CourseCategory
    
    issues = []
    
    # Check Moodle instance configured
    instance = MoodleInstance.objects.filter(
        is_active=True,
        is_primary=True
    ).first()
    
    if not instance:
        issues.append('No Moodle instance configured')
    elif instance.health_status == 'offline':
        issues.append('Moodle instance is offline')
    
    # Check categories exist
    if not CourseCategory.objects.filter(is_active=True).exists():
        issues.append('No course categories configured')
    
    return {
        'ready': len(issues) == 0,
        'issues': issues
    }


def get_pending_sync_summary() -> Dict:
    """
    Get summary of items pending sync
    
    Returns:
        Dictionary with pending counts by type
    """
    from .models import Course, MoodleUserMapping, CourseEnrollment, CourseCategory
    
    return {
        'categories': CourseCategory.objects.filter(
            is_active=True,
            moodle_category_id__isnull=True
        ).count(),
        'courses': Course.objects.filter(
            sync_status__in=['pending', 'modified', 'failed']
        ).count(),
        'users': MoodleUserMapping.objects.filter(
            sync_status__in=['pending', 'modified', 'failed']
        ).count(),
        'enrollments': CourseEnrollment.objects.filter(
            sync_status='pending'
        ).count(),
    }


def log_sync_event(event_type: str, message: str, level: str = 'INFO'):
    """
    Log sync-related events with consistent formatting
    
    Args:
        event_type: Type of event (e.g., 'SYNC_START', 'USER_CREATE')
        message: Log message
        level: Log level (DEBUG, INFO, WARNING, ERROR)
    """
    formatted_message = f"[MOODLE_SYNC] [{event_type}] {message}"
    
    if level == 'DEBUG':
        logger.debug(formatted_message)
    elif level == 'INFO':
        logger.info(formatted_message)
    elif level == 'WARNING':
        logger.warning(formatted_message)
    elif level == 'ERROR':
        logger.error(formatted_message)
    else:
        logger.info(formatted_message)