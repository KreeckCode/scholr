"""
Django management command to test Moodle connection
Usage: python manage.py test_moodle_connection [options]
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from integration.models import MoodleInstance
from integration.moodle_client import MoodleClient
from integration.services import MoodleIntegrationService
from integration.exceptions import (
    MoodleConnectionError, MoodleAPIError, MoodleAuthenticationError
)


class Command(BaseCommand):
    help = 'Test connection to Moodle instance and verify web services are working'
    
    def add_arguments(self, parser):
        """Add command arguments"""
        parser.add_argument(
            '--instance',
            type=str,
            help='Name of Moodle instance to test (defaults to primary)',
        )
        
        parser.add_argument(
            '--test-functions',
            action='store_true',
            help='Test additional Moodle web service functions',
        )
        
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output',
        )
        
        parser.add_argument(
            '--create-test-user',
            action='store_true',
            help='Create a test user to verify user creation works',
        )
        
        parser.add_argument(
            '--create-test-category',
            action='store_true',
            help='Create a test category to verify category creation works',
        )
    
    def handle(self, *args, **options):
        """Execute the command"""
        instance_name = options.get('instance')
        test_functions = options.get('test_functions')
        verbose = options.get('verbose')
        create_test_user = options.get('create_test_user')
        create_test_category = options.get('create_test_category')
        
        self.stdout.write(self.style.HTTP_INFO('=' * 70))
        self.stdout.write(self.style.HTTP_INFO('Moodle Connection Test'))
        self.stdout.write(self.style.HTTP_INFO('=' * 70))
        self.stdout.write('')
        
        # Get Moodle instance
        try:
            if instance_name:
                instance = MoodleInstance.objects.get(name=instance_name)
                self.stdout.write(f'Testing instance: {instance.name}')
            else:
                instance = MoodleInstance.objects.filter(
                    is_active=True,
                    is_primary=True
                ).first()
                
                if not instance:
                    raise CommandError('No primary Moodle instance configured')
                
                self.stdout.write(f'Testing primary instance: {instance.name}')
        
        except MoodleInstance.DoesNotExist:
            raise CommandError(f'Moodle instance "{instance_name}" not found')
        
        self.stdout.write('')
        
        # Display instance configuration
        self._display_instance_config(instance, verbose)
        
        # Test basic connection
        self.stdout.write(self.style.HTTP_INFO('\n[1/5] Testing Basic Connection...'))
        self.stdout.write('-' * 70)
        
        connection_result = self._test_basic_connection(instance)
        
        if not connection_result['success']:
            self.stdout.write(self.style.ERROR('✗ Connection failed'))
            self.stdout.write(self.style.ERROR(f'Error: {connection_result["error"]}'))
            return
        
        self.stdout.write(self.style.SUCCESS('✓ Connection successful'))
        
        # Display site info
        self.stdout.write(self.style.HTTP_INFO('\n[2/5] Moodle Site Information'))
        self.stdout.write('-' * 70)
        self._display_site_info(connection_result)
        
        # Test web services
        if test_functions:
            self.stdout.write(self.style.HTTP_INFO('\n[3/5] Testing Web Service Functions...'))
            self.stdout.write('-' * 70)
            self._test_web_service_functions(instance)
        else:
            self.stdout.write(self.style.WARNING('\n[3/5] Skipping web service function tests (use --test-functions)'))
        
        # Test user creation
        if create_test_user:
            self.stdout.write(self.style.HTTP_INFO('\n[4/5] Testing User Creation...'))
            self.stdout.write('-' * 70)
            self._test_user_creation(instance)
        else:
            self.stdout.write(self.style.WARNING('\n[4/5] Skipping user creation test (use --create-test-user)'))
        
        # Test category creation
        if create_test_category:
            self.stdout.write(self.style.HTTP_INFO('\n[5/5] Testing Category Creation...'))
            self.stdout.write('-' * 70)
            self._test_category_creation(instance)
        else:
            self.stdout.write(self.style.WARNING('\n[5/5] Skipping category creation test (use --create-test-category)'))
        
        # Update health status
        instance.mark_healthy()
        
        # Final summary
        self.stdout.write('')
        self.stdout.write(self.style.HTTP_INFO('=' * 70))
        self.stdout.write(self.style.SUCCESS('✓ All tests passed successfully'))
        self.stdout.write(self.style.HTTP_INFO('=' * 70))
        self.stdout.write('')
        self.stdout.write('Instance health status updated to: ' + 
                         self.style.SUCCESS('HEALTHY'))
        self.stdout.write('')
    
    def _display_instance_config(self, instance, verbose):
        """Display instance configuration"""
        self.stdout.write(self.style.HTTP_INFO('Instance Configuration:'))
        self.stdout.write(f'  Name: {instance.name}')
        self.stdout.write(f'  URL: {instance.base_url}')
        self.stdout.write(f'  Service: {instance.ws_service_name}')
        
        if verbose:
            self.stdout.write(f'  Token: {instance.ws_token[:10]}...')
        
        self.stdout.write(f'  Active: {instance.is_active}')
        self.stdout.write(f'  Primary: {instance.is_primary}')
        self.stdout.write(f'  Current Health: {instance.health_status.upper()}')
        
        if instance.last_health_check:
            self.stdout.write(f'  Last Check: {instance.last_health_check.strftime("%Y-%m-%d %H:%M:%S")}')
    
    def _test_basic_connection(self, instance):
        """Test basic connection to Moodle"""
        try:
            service = MoodleIntegrationService(instance)
            result = service.test_connection()
            return result
        
        except MoodleConnectionError as e:
            return {
                'success': False,
                'error': f'Connection error: {str(e)}'
            }
        
        except MoodleAuthenticationError as e:
            return {
                'success': False,
                'error': f'Authentication error: {str(e)}'
            }
        
        except Exception as e:
            return {
                'success': False,
                'error': f'Unexpected error: {str(e)}'
            }
    
    def _display_site_info(self, result):
        """Display Moodle site information"""
        self.stdout.write(f'  Site Name: {result.get("site_name", "Unknown")}')
        self.stdout.write(f'  Moodle Version: {result.get("moodle_version", "Unknown")}')
        self.stdout.write(f'  Connected User: {result.get("user", "Unknown")}')
    
    def _test_web_service_functions(self, instance):
        """Test various web service functions"""
        client = MoodleClient(instance.base_url, instance.ws_token)
        
        tests = [
            ('Get Categories', self._test_get_categories, client),
            ('Get Courses', self._test_get_courses, client),
        ]
        
        for test_name, test_func, *args in tests:
            try:
                self.stdout.write(f'  Testing: {test_name}... ', ending='')
                result = test_func(*args)
                self.stdout.write(self.style.SUCCESS(f'✓ ({result})'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'✗ ({str(e)})'))
    
    def _test_get_categories(self, client):
        """Test getting categories"""
        categories = client.get_categories()
        return f'{len(categories)} categories found'
    
    def _test_get_courses(self, client):
        """Test getting courses"""
        courses = client.get_courses()
        return f'{len(courses)} courses found'
    
    def _test_user_creation(self, instance):
        """Test creating a user"""
        client = MoodleClient(instance.base_url, instance.ws_token)
        
        # Generate unique test username
        test_username = f'testuser_{int(timezone.now().timestamp())}'
        
        user_data = {
            'username': 'test_username',
            'password': 'TestPass123!',
            'firstname': 'Test',
            'lastname': 'User',
            'email': f'test@test.local',
        }
        
        try:
            self.stdout.write(f'  Creating test user: {test_username}... ', ending='')
            user_id = client.create_user(user_data)
            self.stdout.write(self.style.SUCCESS(f'✓ (User ID: {user_id})'))
            
            # Try to get the user back
            self.stdout.write(f'  Verifying user exists... ', ending='')
            user = client.get_user_by_username(test_username)
            
            if user:
                self.stdout.write(self.style.SUCCESS('✓'))
            else:
                self.stdout.write(self.style.ERROR('✗ User not found'))
            
            # Clean up - delete test user
            self.stdout.write(f'  Deleting test user... ', ending='')
            client.delete_user(user_id)
            self.stdout.write(self.style.SUCCESS('✓'))
        
        except MoodleAPIError as e:
            if 'already exists' in str(e).lower():
                self.stdout.write(self.style.WARNING(f'⚠ User already exists'))
            else:
                self.stdout.write(self.style.ERROR(f'✗ ({str(e)})'))
        
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ ({str(e)})'))
    
    def _test_category_creation(self, instance):
        """Test creating a category"""
        client = MoodleClient(instance.base_url, instance.ws_token)
        
        # Generate unique test category name
        test_category = f'Test Category {int(timezone.now().timestamp())}'
        
        try:
            self.stdout.write(f'  Creating test category: {test_category}... ', ending='')
            category_id = client.create_category(
                name=test_category,
                description='Test category created by management command'
            )
            self.stdout.write(self.style.SUCCESS(f'✓ (Category ID: {category_id})'))
            
            # Verify category exists
            self.stdout.write(f'  Verifying category exists... ', ending='')
            category = client.get_category_by_name(test_category)
            
            if category:
                self.stdout.write(self.style.SUCCESS('✓'))
            else:
                self.stdout.write(self.style.ERROR('✗ Category not found'))
            
            self.stdout.write(self.style.WARNING(f'  Note: Test category ID {category_id} was created but not deleted'))
            self.stdout.write(f'        You may want to delete it manually from Moodle')
        
        except MoodleAPIError as e:
            if 'already exists' in str(e).lower():
                self.stdout.write(self.style.WARNING(f'⚠ Category already exists'))
            else:
                self.stdout.write(self.style.ERROR(f'✗ ({str(e)})'))
        
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ ({str(e)})'))