import logging
from typing import List, Dict, Optional
from django.db import transaction
from django.utils import timezone
from django.contrib.auth import get_user_model
import requests

from .models import (
    MoodleInstance, Course, CourseCategory, MoodleUserMapping,
    CourseEnrollment, SyncLog
)
from .moodle_client import MoodleClient
from .exceptions import MoodleConnectionError, MoodleAPIError, UserNotSyncedError

User = get_user_model()
logger = logging.getLogger(__name__)


class MoodleIntegrationService:
    """
    Service layer for Moodle integration
    Handles all sync operations and business logic
    """
    
    def __init__(self, moodle_instance: MoodleInstance = None):
        """
        Initialize service with a Moodle instance
        
        Args:
            moodle_instance: MoodleInstance to use (uses primary if None)
        """
        if moodle_instance is None:
            moodle_instance = MoodleInstance.objects.filter(
                is_active=True,
                is_primary=True
            ).first()
            
            if not moodle_instance:
                raise ValueError("No active Moodle instance configured")
        
        self.moodle_instance = moodle_instance
        self.client = MoodleClient(
            base_url=moodle_instance.base_url,
            token=moodle_instance.ws_token
        )
    
    def test_connection(self) -> Dict:
        """
        Test connection to Moodle and update health status
        
        Returns:
            Dict with connection test results
        """
        try:
            result = self.client.test_connection()
            
            if result['success']:
                self.moodle_instance.mark_healthy()
                logger.info(f"Moodle connection healthy: {result['site_name']}")
            else:
                self.moodle_instance.mark_offline(result.get('error', 'Unknown error'))
                logger.error(f"Moodle connection failed: {result.get('error')}")
            
            return result
            
        except Exception as e:
            error_msg = str(e)
            self.moodle_instance.mark_offline(error_msg)
            logger.exception("Moodle connection test failed")
            return {
                'success': False,
                'error': error_msg
            }
    
    # ==================== CATEGORY SYNC ====================
    
    def sync_categories(self, initiated_by: User = None) -> SyncLog:
        """
        Sync course categories to Moodle
        Creates categories that don't exist, updates existing ones
        
        Args:
            initiated_by: User who triggered the sync
            
        Returns:
            SyncLog instance with results
        """
        sync_log = SyncLog.objects.create(
            sync_type='categories',
            initiated_by=initiated_by,
            moodle_instance=self.moodle_instance
        )
        
        try:
            sync_log.mark_running()
            
            categories = CourseCategory.objects.filter(is_active=True)
            sync_log.total_items = categories.count()
            sync_log.save(update_fields=['total_items'])
            
            # Get existing Moodle categories
            moodle_categories = {cat['name']: cat for cat in self.client.get_categories()}
            
            results = {
                'created': [],
                'updated': [],
                'skipped': [],
                'errors': []
            }
            
            for category in categories:
                try:
                    moodle_cat = moodle_categories.get(category.name)
                    
                    if moodle_cat:
                        # Category exists, update if needed
                        category.moodle_category_id = moodle_cat['id']
                        category.last_synced = timezone.now()
                        category.save(update_fields=['moodle_category_id', 'last_synced'])
                        
                        results['skipped'].append(category.name)
                        sync_log.skipped_items += 1
                    else:
                        # Create new category
                        moodle_id = self.client.create_category(
                            name=category.name,
                            description=category.description
                        )
                        
                        category.moodle_category_id = moodle_id
                        category.last_synced = timezone.now()
                        category.save(update_fields=['moodle_category_id', 'last_synced'])
                        
                        results['created'].append(category.name)
                        sync_log.successful_items += 1
                    
                except Exception as e:
                    error_msg = f"Failed to sync category {category.name}: {str(e)}"
                    results['errors'].append(error_msg)
                    sync_log.failed_items += 1
                    logger.error(error_msg)
            
            sync_log.details = results
            sync_log.mark_completed()
            
            logger.info(f"Category sync completed: {sync_log.successful_items} successful, {sync_log.failed_items} failed")
            return sync_log
            
        except Exception as e:
            error_msg = f"Category sync failed: {str(e)}"
            sync_log.mark_failed(error_msg)
            logger.exception(error_msg)
            return sync_log
    
    # ==================== USER SYNC ====================
    
    def sync_users(self, initiated_by: User = None, user_ids: List[int] = None) -> SyncLog:
        """
        Sync verified users to Moodle
        Only syncs users who are verified and don't have Moodle accounts
        
        Args:
            initiated_by: User who triggered the sync
            user_ids: Specific user IDs to sync (syncs all verified if None)
            
        Returns:
            SyncLog instance with results
        """
        sync_log = SyncLog.objects.create(
            sync_type='users',
            initiated_by=initiated_by,
            moodle_instance=self.moodle_instance
        )
        
        try:
            sync_log.mark_running()
            
            # Get users to sync - only verified students
            users_query = User.objects.filter(
                role=User.STUDENT,
                is_verified=True,
                is_active=True
            )
            
            if user_ids:
                users_query = users_query.filter(id__in=user_ids)
            
            users = users_query.select_related('profile')
            sync_log.total_items = users.count()
            sync_log.save(update_fields=['total_items'])
            
            results = {
                'created': [],
                'updated': [],
                'skipped': [],
                'errors': []
            }
            
            for user in users:
                try:
                    # Check if user already has mapping
                    mapping, created = MoodleUserMapping.objects.get_or_create(
                        cas_user=user,
                        defaults={
                            'moodle_username': user.username,
                            'sync_status': 'pending'
                        }
                    )
                    
                    # If user already synced, skip
                    if mapping.sync_status == 'synced' and mapping.moodle_user_id:
                        results['skipped'].append(user.username)
                        sync_log.skipped_items += 1
                        continue
                    
                    # Check if user exists in Moodle
                    existing_user = self.client.get_user_by_username(user.username)
                    
                    if existing_user:
                        # User exists, just map it
                        mapping.moodle_user_id = existing_user['id']
                        mapping.mark_synced()
                        
                        results['skipped'].append(user.username)
                        sync_log.skipped_items += 1
                    else:
                        # Create new user in Moodle
                        user_data = {
                            'username': user.username,
                            'password': f"JH{user.username}123!",  # Default password
                            'firstname': user.first_name,
                            'lastname': user.last_name,
                            'email': user.email or f"{user.username}@judea.local",
                            'phone1': user.phone_number,
                            'city': user.profile.region if hasattr(user, 'profile') else '',
                            'country': self._get_country_code(user.profile.country) if hasattr(user, 'profile') else 'ZA',
                        }
                        
                        moodle_user_id = self.client.create_user(user_data)
                        
                        mapping.moodle_user_id = moodle_user_id
                        mapping.mark_synced()
                        
                        results['created'].append(user.username)
                        sync_log.successful_items += 1
                        
                        # Auto-enroll if enabled
                        self._auto_enroll_user(user, moodle_user_id)
                    
                except Exception as e:
                    error_msg = f"Failed to sync user {user.username}: {str(e)}"
                    results['errors'].append(error_msg)
                    
                    if hasattr(user, 'moodle_mapping'):
                        user.moodle_mapping.mark_failed(error_msg)
                    
                    sync_log.failed_items += 1
                    logger.error(error_msg)
            
            sync_log.details = results
            sync_log.mark_completed()
            
            logger.info(f"User sync completed: {sync_log.successful_items} successful, {sync_log.failed_items} failed")
            return sync_log
            
        except Exception as e:
            error_msg = f"User sync failed: {str(e)}"
            sync_log.mark_failed(error_msg)
            logger.exception(error_msg)
            return sync_log
    
    def _auto_enroll_user(self, user: User, moodle_user_id: int):
        """
        Auto-enroll user in courses with auto_enroll_enabled
        
        Args:
            user: CAS user
            moodle_user_id: Moodle user ID
        """
        auto_enroll_courses = Course.objects.filter(
            auto_enroll_enabled=True,
            visible=True,
            sync_status='synced',
            moodle_course_id__isnull=False
        )
        
        for course in auto_enroll_courses:
            try:
                # Check if already enrolled
                enrollment, created = CourseEnrollment.objects.get_or_create(
                    user=user,
                    course=course,
                    defaults={
                        'is_active': True,
                        'sync_status': 'pending'
                    }
                )
                
                if created:
                    # Enroll in Moodle
                    self.client.enrol_user(moodle_user_id, course.moodle_course_id)
                    enrollment.mark_synced()
                    logger.info(f"Auto-enrolled {user.username} in {course.short_name}")
                    
            except Exception as e:
                logger.error(f"Failed to auto-enroll {user.username} in {course.short_name}: {str(e)}")
    
    # ==================== COURSE SYNC ====================
    
    def sync_courses(self, initiated_by: User = None, course_ids: List[int] = None) -> SyncLog:
        """
        Sync courses to Moodle with smart duplicate handling
        
        Flow:
        1. If course has moodle_course_id → verify it exists
        2. If not found by ID → search by shortname
        3. If found → update it and reconcile
        4. If not found → create new
        5. Handle duplicates gracefully
        """
        sync_log = SyncLog.objects.create(
            sync_type='courses',
            initiated_by=initiated_by,
            moodle_instance=self.moodle_instance
        )
        
        try:
            sync_log.mark_running()
            
            courses_query = Course.objects.filter(
                sync_status__in=['pending', 'modified', 'failed']
            ).select_related('category')
            
            if course_ids:
                courses_query = courses_query.filter(id__in=course_ids)
            
            courses = courses_query
            sync_log.total_items = courses.count()
            sync_log.save(update_fields=['total_items'])
            
            results = {
                'created': [],
                'updated': [],
                'reconciled': [],
                'errors': []
            }
            
            for course in courses:
                try:
                    # Ensure category exists
                    category_id = course.category.moodle_category_id

                    if category_id:
                        moodle_cat = self.client.get_categories()
                        valid_ids = [cat['id'] for cat in moodle_cat]
                        
                        if category_id not in valid_ids:
                            logger.warning(
                                f"Category {course.category.name} has stale ID {category_id}, clearing"
                            )
                            category_id = None
                            course.category.moodle_category_id = None

                    if not category_id:
                        moodle_cat_id = self.client.create_category(
                            name=course.category.name,
                            description=course.category.description
                        )
                        course.category.moodle_category_id = moodle_cat_id
                        course.category.save(update_fields=['moodle_category_id'])
                        category_id = moodle_cat_id
                    
                    import datetime

                    start_timestamp = 0
                    if course.start_date:
                        start_dt = datetime.datetime.combine(course.start_date, datetime.time.min)
                        start_timestamp = int(start_dt.timestamp())

                    end_timestamp = 0
                    if course.end_date:
                        end_dt = datetime.datetime.combine(course.end_date, datetime.time.min)
                        end_timestamp = int(end_dt.timestamp())

                    course_data = {
                        'fullname': course.title,
                        'shortname': course.short_name,
                        'categoryid': category_id,
                        'summary': course.summary or '',
                        'visible': 1 if course.visible else 0,
                        'startdate': start_timestamp,
                        'enddate': end_timestamp,
                    }
                    
                    # SMART SYNC LOGIC
                    existing_course = None
                    
                    if course.moodle_course_id:
                        existing_course = self.client.get_course_by_id(course.moodle_course_id)
                        
                        if not existing_course:
                            logger.warning(
                                f"Course {course.short_name} has moodle_course_id={course.moodle_course_id} "
                                f"but course doesn't exist in Moodle. Clearing ID."
                            )
                            course.moodle_course_id = None
                    
                    if not existing_course:
                        existing_course = self.client.get_course_by_shortname(course.short_name)
                        
                        if existing_course:
                            course.moodle_course_id = existing_course['id']
                            course.save(update_fields=['moodle_course_id'])
                            
                            logger.info(
                                f"Reconciled course {course.short_name}: "
                                f"found existing Moodle course ID {existing_course['id']}"
                            )
                            results['reconciled'].append(
                                f"{course.short_name} → ID {existing_course['id']}"
                            )
                    
                    if existing_course:
                        self.client.update_course(existing_course['id'], course_data)
                        course.mark_synced(existing_course['id'])
                        
                        results['updated'].append(course.short_name)
                        sync_log.successful_items += 1
                        
                        logger.info(f"Updated course {course.short_name} (ID: {existing_course['id']})")
                        
                    else:
                        try:
                            moodle_id = self.client.create_course(course_data)
                            course.mark_synced(moodle_id)
                            
                            results['created'].append(course.short_name)
                            sync_log.successful_items += 1
                            
                            logger.info(f"Created course {course.short_name} (ID: {moodle_id})")
                            
                        except MoodleAPIError as e:
                            if 'already exists' in str(e).lower() or 'duplicate' in str(e).lower():
                                existing_course = self.client.get_course_by_shortname(course.short_name)
                                
                                if existing_course:
                                    course.moodle_course_id = existing_course['id']
                                    course.mark_synced(existing_course['id'])
                                    
                                    results['reconciled'].append(
                                        f"{course.short_name} → ID {existing_course['id']} (duplicate)"
                                    )
                                    sync_log.successful_items += 1
                                    
                                    logger.info(
                                        f"Handled duplicate: reconciled {course.short_name} "
                                        f"to existing ID {existing_course['id']}"
                                    )
                                else:
                                    raise
                            else:
                                raise
                    
                except Exception as e:
                    error_msg = f"Failed to sync course {course.short_name}: {str(e)}"
                    results['errors'].append(error_msg)
                    course.mark_failed(error_msg)
                    sync_log.failed_items += 1
                    logger.error(error_msg)
            
            sync_log.details = results
            sync_log.mark_completed()
            
            logger.info(
                f"Course sync completed: {sync_log.successful_items} successful "
                f"({len(results['created'])} created, {len(results['updated'])} updated, "
                f"{len(results['reconciled'])} reconciled), {sync_log.failed_items} failed"
            )
            return sync_log
            
        except Exception as e:
            error_msg = f"Course sync failed: {str(e)}"
            sync_log.mark_failed(error_msg)
            logger.exception(error_msg)
            return sync_log
    

    
    # ==================== ENROLLMENT SYNC ====================
    
    def sync_enrollments(self, initiated_by: User = None) -> SyncLog:
        """
        Sync enrollments to Moodle
        Enrolls/unenrolls students based on CourseEnrollment records
        
        Args:
            initiated_by: User who triggered the sync
            
        Returns:
            SyncLog instance with results
        """
        sync_log = SyncLog.objects.create(
            sync_type='enrollments',
            initiated_by=initiated_by,
            moodle_instance=self.moodle_instance
        )
        
        try:
            sync_log.mark_running()
            
            # Get enrollments that need sync
            enrollments = CourseEnrollment.objects.filter(
                sync_status='pending'
            ).select_related('user', 'course', 'user__moodle_mapping')
            
            sync_log.total_items = enrollments.count()
            sync_log.save(update_fields=['total_items'])
            
            results = {
                'enrolled': [],
                'unenrolled': [],
                'errors': []
            }
            
            for enrollment in enrollments:
                try:
                    # Check if user has Moodle account
                    if not hasattr(enrollment.user, 'moodle_mapping'):
                        raise ValueError("User not synced to Moodle")
                    
                    mapping = enrollment.user.moodle_mapping
                    if not mapping.moodle_user_id:
                        raise ValueError("User has no Moodle ID")
                    
                    # Check if course synced
                    if not enrollment.course.moodle_course_id:
                        raise ValueError("Course not synced to Moodle")
                    
                    if enrollment.is_active:
                        # Enroll user
                        self.client.enrol_user(
                            mapping.moodle_user_id,
                            enrollment.course.moodle_course_id
                        )
                        enrollment.mark_synced()
                        
                        results['enrolled'].append(
                            f"{enrollment.user.username} → {enrollment.course.short_name}"
                        )
                        sync_log.successful_items += 1
                    else:
                        # Unenroll user
                        self.client.unenrol_user(
                            mapping.moodle_user_id,
                            enrollment.course.moodle_course_id
                        )
                        enrollment.mark_synced()
                        
                        results['unenrolled'].append(
                            f"{enrollment.user.username} ← {enrollment.course.short_name}"
                        )
                        sync_log.successful_items += 1
                    
                except Exception as e:
                    error_msg = f"Failed to sync enrollment {enrollment}: {str(e)}"
                    results['errors'].append(error_msg)
                    enrollment.mark_failed(error_msg)
                    sync_log.failed_items += 1
                    logger.error(error_msg)
            
            sync_log.details = results
            sync_log.mark_completed()
            
            logger.info(f"Enrollment sync completed: {sync_log.successful_items} successful, {sync_log.failed_items} failed")
            return sync_log
            
        except Exception as e:
            error_msg = f"Enrollment sync failed: {str(e)}"
            sync_log.mark_failed(error_msg)
            logger.exception(error_msg)
            return sync_log
    
    # ==================== FULL SYNC ====================
    
    def full_sync(self, initiated_by: User = None) -> SyncLog:
        """
        Perform a full sync of all entities
        Order: Categories → Courses → Users → Enrollments
        
        Args:
            initiated_by: User who triggered the sync
            
        Returns:
            SyncLog instance with results
        """
        sync_log = SyncLog.objects.create(
            sync_type='full',
            initiated_by=initiated_by,
            moodle_instance=self.moodle_instance
        )
        
        try:
            sync_log.mark_running()
            
            results = {
                'categories': None,
                'courses': None,
                'users': None,
                'enrollments': None
            }
            
            # Step 1: Sync categories
            cat_log = self.sync_categories(initiated_by)
            results['categories'] = {
                'successful': cat_log.successful_items,
                'failed': cat_log.failed_items
            }
            
            # Step 2: Sync courses
            course_log = self.sync_courses(initiated_by)
            results['courses'] = {
                'successful': course_log.successful_items,
                'failed': course_log.failed_items
            }
            
            # Step 3: Sync users
            user_log = self.sync_users(initiated_by)
            results['users'] = {
                'successful': user_log.successful_items,
                'failed': user_log.failed_items
            }
            
            # Step 4: Sync enrollments
            enrol_log = self.sync_enrollments(initiated_by)
            results['enrollments'] = {
                'successful': enrol_log.successful_items,
                'failed': enrol_log.failed_items
            }
            
            # Calculate totals
            sync_log.total_items = sum([
                cat_log.total_items,
                course_log.total_items,
                user_log.total_items,
                enrol_log.total_items
            ])
            
            sync_log.successful_items = sum([
                cat_log.successful_items,
                course_log.successful_items,
                user_log.successful_items,
                enrol_log.successful_items
            ])
            
            sync_log.failed_items = sum([
                cat_log.failed_items,
                course_log.failed_items,
                user_log.failed_items,
                enrol_log.failed_items
            ])
            
            sync_log.details = results
            sync_log.mark_completed()
            
            logger.info(f"Full sync completed: {sync_log.successful_items}/{sync_log.total_items} successful")
            return sync_log
            
        except Exception as e:
            error_msg = f"Full sync failed: {str(e)}"
            sync_log.mark_failed(error_msg)
            logger.exception(error_msg)
            return sync_log
    
    # ==================== SSO ====================
    
    def generate_sso_url(self, user) -> str:
        """
        Generate SSO URL for user to access Moodle
        
        Args:
            user: CAS User instance
            
        Returns:
            SSO login URL
        """
        # Validate user mapping
        if not hasattr(user, 'moodle_mapping'):
            raise UserNotSyncedError(user.username)
        
        mapping = user.moodle_mapping
        
        if not mapping.moodle_user_id:
            raise UserNotSyncedError(user.username)
        
        # Generate SSO URL using username (preferred by auth_userkey)
        login_url = self.client.generate_login_token(
            username=mapping.moodle_username
        )
        
        # Update last SSO access
        mapping.last_sso_access = timezone.now()
        mapping.save(update_fields=['last_sso_access'])
        
        logger.info(f"Generated SSO URL for {user.username}")
        return login_url
    

    def delete_course(self, moodle_course_id):
        """
        Delete course from Moodle
        
        Args:
            moodle_course_id: Moodle course ID to delete
            
        Returns:
            bool: True if deleted successfully
            
        Raises:
            Exception: If deletion fails
        """
        if not self.moodle_instance:
            raise ValueError("No active Moodle instance configured")
        
        url = f"{self.moodle_instance.base_url}/webservice/rest/server.php"
        
        params = {
            'wstoken': self.moodle_instance.ws_token,
            'wsfunction': 'core_course_delete_courses',
            'moodlewsrestformat': 'json',
            'courseids[0]': moodle_course_id
        }
        
        response = requests.post(url, data=params)
        response.raise_for_status()
        
        data = response.json()
        
        # Check for Moodle errors
        if isinstance(data, dict) and 'exception' in data:
            raise Exception(f"Moodle error: {data.get('message', 'Unknown error')}")
        
        return True


    def update_course(self, course):
        """Update course in Moodle"""
        if not course.moodle_course_id:  # FIXED - was checking wrong field
            raise ValueError("Course has no Moodle ID - cannot update")
        
        if not self.moodle_instance:
            raise ValueError("No active Moodle instance configured")
        
        import datetime
        
        # Convert dates to timestamps
        start_timestamp = 0
        if course.start_date:
            start_dt = datetime.datetime.combine(course.start_date, datetime.time.min)
            start_timestamp = int(start_dt.timestamp())
        
        end_timestamp = 0
        if course.end_date:
            end_dt = datetime.datetime.combine(course.end_date, datetime.time.min)
            end_timestamp = int(end_dt.timestamp())
        
        url = f"{self.moodle_instance.base_url}/webservice/rest/server.php"
        
        params = {
            'wstoken': self.moodle_instance.ws_token,
            'wsfunction': 'core_course_update_courses',
            'moodlewsrestformat': 'json',
            'courses[0][id]': course.moodle_course_id,
            'courses[0][fullname]': course.title,
            'courses[0][shortname]': course.short_name,
            'courses[0][categoryid]': course.category.moodle_category_id if course.category else 1,
            'courses[0][summary]': course.description or course.summary or '',
            'courses[0][summaryformat]': 1,
            'courses[0][visible]': 1 if course.visible else 0,
            'courses[0][startdate]': start_timestamp,  # ADDED
            'courses[0][enddate]': end_timestamp,      # ADDED
        }
        
        response = requests.post(url, data=params)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, dict) and 'exception' in data:
            raise Exception(f"Moodle error: {data.get('message', 'Unknown error')}")
        
        # Update sync status
        course.sync_status = 'synced'
        course.last_synced_at = timezone.now()
        course.save(update_fields=['sync_status', 'last_synced_at'])
        
        return data
    
    # ==================== HELPER METHODS ====================
    
    def _get_country_code(self, country_name: str) -> str:
        """
        Convert country name to ISO 2-letter code
        Simple mapping for common African countries
        """
        country_map = {
            'south africa': 'ZA',
            'kenya': 'KE',
            'uganda': 'UG',
            'tanzania': 'TZ',
            'zimbabwe': 'ZW',
            'nigeria': 'NG',
            'ghana': 'GH',
            'ethiopia': 'ET',
            'rwanda': 'RW',
        }
        
        return country_map.get(country_name.lower(), 'ZA')