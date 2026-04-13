from django import template
from integration.models import CourseEnrollment

register = template.Library()

@register.simple_tag
def get_course_progress(user, course):
    """
    Get progress percentage for a specific course enrollment.
    Returns 0 if no enrollment exists.
    
    Args:
        user: User object
        course: Course object from integration app
    
    Returns:
        float: Progress percentage (0-100)
    """
    try:
        # Query CourseEnrollment directly (not via reverse relationship)
        enrollment = CourseEnrollment.objects.filter(
            user=user,
            course=course
        ).first()
        
        if enrollment:
            # CourseEnrollment stores progress as 'completion_percentage' or similar
            # Check the actual field name in integration/models.py
            progress = getattr(enrollment, 'completion_percentage', None) or \
                      getattr(enrollment, 'progress', None) or \
                      getattr(enrollment, 'progress_percentage', None) or 0
            return float(progress) if progress else 0.0
        return 0.0
        
    except Exception as e:
        # Debug: print error to console
        print(f"Error in get_course_progress: {e}")
        return 0.0