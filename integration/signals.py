"""
Django signals for automatic sync triggers
Handles post-save actions to mark items for sync
"""

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from accounts.models import User
from .models import Course, CourseEnrollment, MoodleUserMapping


@receiver(post_save, sender=User)
def create_moodle_mapping_on_verification(sender, instance, created, **kwargs):
    """
    Automatically create MoodleUserMapping when student is verified
    Only applies to students who are verified
    """
    # Only for students
    if instance.role != User.STUDENT:
        return
    
    # Only if verified
    if not instance.is_verified:
        return
    
    # Check if mapping already exists
    if hasattr(instance, 'moodle_mapping'):
        # User already has mapping, mark as modified if needed
        mapping = instance.moodle_mapping
        if mapping.sync_status == 'synced':
            mapping.sync_status = 'modified'
            mapping.save(update_fields=['sync_status'])
        return
    
    # Create new mapping
    MoodleUserMapping.objects.create(
        cas_user=instance,
        moodle_username=instance.username,
        sync_status='pending'
    )


@receiver(pre_save, sender=Course)
def mark_course_for_sync_on_change(sender, instance, **kwargs):
    """
    Mark course for sync when it's modified
    Only if already synced to Moodle
    """
    if not instance.pk:
        # New course, will be pending by default
        return
    
    try:
        old_instance = Course.objects.get(pk=instance.pk)
    except Course.DoesNotExist:
        return
    
    # Only mark as modified if currently synced
    if old_instance.sync_status != 'synced':
        return
    
    # Check if any synced fields changed
    synced_fields = [
        'title', 'summary', 'visible', 'start_date', 'end_date'
    ]
    
    for field in synced_fields:
        if getattr(old_instance, field) != getattr(instance, field):
            instance.sync_status = 'modified'
            break


@receiver(post_save, sender=CourseEnrollment)
def mark_enrollment_for_sync(sender, instance, created, **kwargs):
    """
    Mark enrollment for sync when created or modified
    """
    # If just created, already pending
    if created:
        return
    
    # If status changed, mark for sync
    if instance.sync_status == 'synced':
        instance.sync_status = 'pending'
        instance.save(update_fields=['sync_status'])


@receiver(post_save, sender=Course)
def auto_enroll_verified_students(sender, instance, created, **kwargs):
    """
    Automatically enroll verified students when course is created
    Only if auto_enroll_enabled is True
    """
    # Only for newly created courses
    if not created:
        return
    
    # Only if auto-enroll enabled
    if not instance.auto_enroll_enabled:
        return
    
    # Only if course is visible
    if not instance.visible:
        return
    
    # Get all verified students
    verified_students = User.objects.filter(
        role=User.STUDENT,
        is_verified=True,
        is_active=True
    )
    
    # Create enrollments
    enrollments = []
    for student in verified_students:
        enrollment, created_enrollment = CourseEnrollment.objects.get_or_create(
            user=student,
            course=instance,
            defaults={
                'is_active': True,
                'sync_status': 'pending'
            }
        )
        
        if created_enrollment:
            enrollments.append(enrollment)
    
    if enrollments:
        from .utils import log_sync_event
        log_sync_event(
            'AUTO_ENROLL',
            f'Auto-enrolled {len(enrollments)} students in {instance.short_name}',
            'INFO'
        )