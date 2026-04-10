import requests
import logging
from typing import Dict, List, Optional, Any
from django.conf import settings
from .exceptions import MoodleConnectionError, MoodleAPIError, MoodleAuthenticationError

logger = logging.getLogger(__name__)


class MoodleClient:
    """
    Wrapper for Moodle Web Services API
    Handles all communication with Moodle instance
    """
    
    def __init__(self, base_url: str, token: str, timeout: int = 10):
        """
        Initialize Moodle client
        
        Args:
            base_url: Moodle base URL 
            token: Web service token
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.timeout = timeout
        self.webservice_url = f"{self.base_url}/webservice/rest/server.php"
        
        logger.info(f"Initialized Moodle client for {self.base_url}")
    
    def _flatten_params(self, params: Dict, prefix: str = '') -> Dict:
        """
        Flatten nested parameters for Moodle REST API
        
        Moodle expects: categories[0][name] instead of nested dicts
        
        Args:
            params: Parameters to flatten
            prefix: Current prefix for keys
            
        Returns:
            Flattened dict
        """
        flattened = {}
        
        for key, value in params.items():
            new_key = f"{prefix}[{key}]" if prefix else key
            
            if isinstance(value, dict):
                flattened.update(self._flatten_params(value, new_key))
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        flattened.update(self._flatten_params(item, f"{new_key}[{i}]"))
                    else:
                        flattened[f"{new_key}[{i}]"] = item
            else:
                flattened[new_key] = value
        
        return flattened
    
    def _make_request(self, function: str, params: Dict = None) -> Any:
        """
        Make a request to Moodle Web Services
        
        Args:
            function: Moodle web service function name
            params: Parameters to pass to the function
            
        Returns:
            Response data from Moodle
            
        Raises:
            MoodleConnectionError: If connection fails
            MoodleAuthenticationError: If token is invalid
            MoodleAPIError: If Moodle returns an error
        """
        if params is None:
            params = {}
        
        # Flatten nested parameters for Moodle REST API
        flattened_params = self._flatten_params(params)
        
        # Build request parameters
        request_params = {
            'wstoken': self.token,
            'wsfunction': function,
            'moodlewsrestformat': 'json',
            **flattened_params
        }
        
        try:
            logger.debug(f"Calling Moodle function: {function}")
            response = requests.post(
                self.webservice_url,
                data=request_params,
                timeout=self.timeout
            )
            response.raise_for_status()
            
            data = response.json()
            
            # Check for Moodle errors
            if isinstance(data, dict):
                if 'exception' in data:
                    error_msg = data.get('message', 'Unknown error')
                    error_code = data.get('errorcode', 'unknown')
                    
                    if error_code == 'invalidtoken':
                        raise MoodleAuthenticationError(f"Invalid token: {error_msg}")
                    
                    raise MoodleAPIError(f"Moodle error ({error_code}): {error_msg}")
                
                if 'error' in data:
                    raise MoodleAPIError(f"Moodle error: {data['error']}")
            
            logger.debug(f"Moodle function {function} executed successfully")
            return data
            
        except requests.exceptions.Timeout:
            raise MoodleConnectionError(f"Request to Moodle timed out after {self.timeout} seconds")
        
        except requests.exceptions.ConnectionError as e:
            raise MoodleConnectionError(f"Could not connect to Moodle: {str(e)}")
        
        except requests.exceptions.RequestException as e:
            raise MoodleConnectionError(f"Request failed: {str(e)}")
        
        except ValueError as e:
            raise MoodleAPIError(f"Invalid JSON response from Moodle: {str(e)}")
    
    def test_connection(self) -> Dict:
        """
        Test connection to Moodle
        
        Returns:
            Dict with connection status and site info
        """
        try:
            site_info = self._make_request('core_webservice_get_site_info')
            return {
                'success': True,
                'site_name': site_info.get('sitename'),
                'moodle_version': site_info.get('release'),
                'user': site_info.get('username'),
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    # ==================== USER OPERATIONS ====================
    
    def create_user(self, user_data: Dict) -> int:
        """
        Create a user in Moodle
        
        Args:
            user_data: Dictionary with user information
                Required: username, password, firstname, lastname, email
                
        Returns:
            Moodle user ID
        """
        users = [{
            'username': user_data['username'],
            'password': user_data['password'],
            'firstname': user_data['firstname'],
            'lastname': user_data['lastname'],
            'email': user_data['email'],
            'auth': 'manual',
            'lang': 'en',
            'timezone': 'Africa/Johannesburg',
            **{k: v for k, v in user_data.items() if k not in ['username', 'password', 'firstname', 'lastname', 'email']}
        }]
        
        response = self._make_request('core_user_create_users', {'users': users})
        
        if response and len(response) > 0:
            user_id = response[0]['id']
            logger.info(f"Created Moodle user: {user_data['username']} (ID: {user_id})")
            return user_id
        
        raise MoodleAPIError("Failed to create user - no ID returned")
    
    def get_user_by_username(self, username: str) -> Optional[Dict]:
        """
        Get user by username
        
        Args:
            username: Username to search for
            
        Returns:
            User data dict or None if not found
        """
        criteria = [{
            'key': 'username',
            'value': username
        }]
        
        response = self._make_request('core_user_get_users', {'criteria': criteria})
        
        if response and 'users' in response and len(response['users']) > 0:
            return response['users'][0]
        
        return None
    
    def update_user(self, user_id: int, user_data: Dict) -> bool:
        """
        Update user in Moodle
        
        Args:
            user_id: Moodle user ID
            user_data: Fields to update
            
        Returns:
            True if successful
        """
        users = [{
            'id': user_id,
            **user_data
        }]
        
        self._make_request('core_user_update_users', {'users': users})
        logger.info(f"Updated Moodle user ID: {user_id}")
        return True
    
    def delete_user(self, user_id: int) -> bool:
        """
        Delete user from Moodle
        
        Args:
            user_id: Moodle user ID
            
        Returns:
            True if successful
        """
        self._make_request('core_user_delete_users', {'userids': [user_id]})
        logger.info(f"Deleted Moodle user ID: {user_id}")
        return True
    
    # ==================== COURSE CATEGORY OPERATIONS ====================
    
    def create_category(self, name: str, description: str = '', parent_id: int = 0) -> int:
        """
        Create a course category in Moodle
        
        Args:
            name: Category name
            description: Category description
            parent_id: Parent category ID (0 for root)
            
        Returns:
            Moodle category ID
        """
        categories = [{
            'name': name,
            'description': description,
            'parent': parent_id,
        }]
        
        response = self._make_request('core_course_create_categories', {'categories': categories})
        
        if response and len(response) > 0:
            category_id = response[0]['id']
            logger.info(f"Created Moodle category: {name} (ID: {category_id})")
            return category_id
        
        raise MoodleAPIError("Failed to create category - no ID returned")
    
    def get_categories(self) -> List[Dict]:
        """
        Get all course categories from Moodle
        
        Returns:
            List of category dicts
        """
        response = self._make_request('core_course_get_categories')
        return response if response else []
    
    def get_category_by_name(self, name: str) -> Optional[Dict]:
        """
        Find category by name
        
        Args:
            name: Category name to search for
            
        Returns:
            Category dict or None
        """
        categories = self.get_categories()
        for category in categories:
            if category['name'] == name:
                return category
        return None
    
    # ==================== COURSE OPERATIONS ====================
    
    def create_course(self, course_data: Dict) -> int:
        """
        Create a course in Moodle
        
        Args:
            course_data: Dictionary with course information
                Required: fullname, shortname, categoryid
                
        Returns:
            Moodle course ID
        """
        courses = [{
            'fullname': course_data['fullname'],
            'shortname': course_data['shortname'],
            'categoryid': course_data['categoryid'],
            'summary': course_data.get('summary', ''),
            'summaryformat': 1,
            'format': 'topics',
            'visible': 1 if course_data.get('visible', True) else 0,
            'startdate': course_data.get('startdate', 0),
            'enddate': course_data.get('enddate', 0),
        }]
        
        response = self._make_request('core_course_create_courses', {'courses': courses})
        
        if response and len(response) > 0:
            course_id = response[0]['id']
            logger.info(f"Created Moodle course: {course_data['shortname']} (ID: {course_id})")
            return course_id
        
        raise MoodleAPIError("Failed to create course - no ID returned")
    
    def update_course(self, course_id: int, course_data: Dict) -> bool:
        """
        Update course in Moodle
        
        Args:
            course_id: Moodle course ID
            course_data: Fields to update
            
        Returns:
            True if successful
        """
        courses = [{
            'id': course_id,
            **course_data
        }]
        
        self._make_request('core_course_update_courses', {'courses': courses})
        logger.info(f"Updated Moodle course ID: {course_id}")
        return True
    
    def delete_course(self, course_id: int) -> bool:
        """
        Delete course from Moodle
        
        Args:
            course_id: Moodle course ID
            
        Returns:
            True if successful
        """
        self._make_request('core_course_delete_courses', {'courseids': [course_id]})
        logger.info(f"Deleted Moodle course ID: {course_id}")
        return True
    
    def get_courses(self) -> List[Dict]:
        """
        Get all courses from Moodle
        
        Returns:
            List of course dicts
        """
        response = self._make_request('core_course_get_courses')
        return response if response else []
    
    def get_course_by_id(self, course_id: int) -> Optional[Dict]:
        """
        Get course by ID
        
        Args:
            course_id: Moodle course ID
            
        Returns:
            Course dict or None
        """
        response = self._make_request('core_course_get_courses', {'options': {'ids': [course_id]}})
        
        if response and len(response) > 0:
            return response[0]
        
        return None
    
    def get_course_by_shortname(self, shortname: str) -> Optional[Dict]:
        """
        Get course by shortname
        
        Args:
            shortname: Course shortname to search for
            
        Returns:
            Course dict or None if not found
        """
        try:
            # Get all courses and filter by shortname
            # Moodle doesn't have a direct "search by shortname" API
            courses = self.get_courses()
            
            for course in courses:
                if course.get('shortname') == shortname:
                    logger.debug(f"Found course by shortname: {shortname} (ID: {course['id']})")
                    return course
            
            logger.debug(f"Course not found by shortname: {shortname}")
            return None
            
        except Exception as e:
            logger.error(f"Error searching course by shortname: {str(e)}")
            return None

    def course_exists(self, course_id: int) -> bool:
        """
        Check if course exists in Moodle
        
        Args:
            course_id: Moodle course ID
            
        Returns:
            True if course exists, False otherwise
        """
        try:
            course = self.get_course_by_id(course_id)
            return course is not None
        except Exception:
            return False
    
    # ==================== ENROLLMENT OPERATIONS ====================
    
    def enrol_user(self, user_id: int, course_id: int, role_id: int = 5) -> bool:
        """
        Enrol user in a course
        
        Args:
            user_id: Moodle user ID
            course_id: Moodle course ID
            role_id: Role ID (5 = student by default)
            
        Returns:
            True if successful
        """
        enrolments = [{
            'roleid': role_id,
            'userid': user_id,
            'courseid': course_id
        }]
        
        self._make_request('enrol_manual_enrol_users', {'enrolments': enrolments})
        logger.info(f"Enrolled user {user_id} in course {course_id}")
        return True
    
    def unenrol_user(self, user_id: int, course_id: int, role_id: int = 5) -> bool:
        """
        Unenrol user from a course
        
        Args:
            user_id: Moodle user ID
            course_id: Moodle course ID
            role_id: Role ID (5 = student by default)
            
        Returns:
            True if successful
        """
        enrolments = [{
            'roleid': role_id,
            'userid': user_id,
            'courseid': course_id
        }]
        
        self._make_request('enrol_manual_unenrol_users', {'enrolments': enrolments})
        logger.info(f"Unenrolled user {user_id} from course {course_id}")
        return True
    
    def get_enrolled_users(self, course_id: int) -> List[Dict]:
        """
        Get all enrolled users in a course
        
        Args:
            course_id: Moodle course ID
            
        Returns:
            List of enrolled user dicts
        """
        response = self._make_request('core_enrol_get_enrolled_users', {'courseid': course_id})
        return response if response else []
    
    # ==================== SSO / TOKEN OPERATIONS ====================
    
    def generate_login_token(self, user_id: int = None, username: str = None) -> str:
        """
        Generate a login token for SSO
        This requires the auth_userkey plugin to be installed in Moodle
        
        Args:
            user_id: Moodle user ID (deprecated, use username)
            username: Moodle username (preferred)
            
        Returns:
            Login URL with key
        """
        try:
            # Prefer username over user_id for auth_userkey
            if username:
                params = {'user': {'username': username}}
            elif user_id:
                params = {'user': {'id': user_id}}
            else:
                raise ValueError("Either user_id or username must be provided")
            
            response = self._make_request('auth_userkey_request_login_url', params)
            
            if 'loginurl' in response:
                return response['loginurl']
            
            raise MoodleAPIError("No login URL returned")
            
        except MoodleAPIError as e:
            # Fallback if auth_userkey not available
            logger.warning(f"auth_userkey plugin error: {str(e)}, using alternative login method")
            # Return direct login URL (requires manual login)
            return f"{self.base_url}/login/index.php"
    
    # ==================== HELPER METHODS ====================
    
    def get_user_courses(self, user_id: int) -> List[Dict]:
        """
        Get all courses a user is enrolled in
        
        Args:
            user_id: Moodle user ID
            
        Returns:
            List of course dicts
        """
        response = self._make_request('core_enrol_get_users_courses', {'userid': user_id})
        return response if response else []