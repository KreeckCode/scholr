from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model

from .models import User, UserProfile, LoginAttempt
from .forms import PastorRegistrationForm, CustomAuthenticationForm

User = get_user_model()


class UserModelTest(TestCase):
    """Test custom User model functionality"""
    
    def setUp(self):
        """Create test users with different roles"""
        self.student = User.objects.create_user(
            username='teststudent',
            password='testpass123',
            phone_number='+27123456789',
            first_name='John',
            last_name='Doe',
            role=User.STUDENT
        )
        
        self.dean = User.objects.create_user(
            username='testdean',
            password='testpass123',
            phone_number='+27123456790',
            first_name='Jane',
            last_name='Smith',
            role=User.DEAN
        )
        
        self.admin = User.objects.create_user(
            username='testadmin',
            password='testpass123',
            phone_number='+27123456791',
            first_name='Admin',
            last_name='User',
            role=User.ADMIN,
            is_staff=True
        )
    
    def test_user_creation(self):
        """Test basic user creation"""
        self.assertEqual(self.student.username, 'teststudent')
        self.assertEqual(self.student.role, User.STUDENT)
        self.assertFalse(self.student.is_verified)
    
    def test_get_full_name(self):
        """Test full name method"""
        self.assertEqual(self.student.get_full_name(), 'John Doe')
    
    def test_can_verify_users(self):
        """Test permission checks for user verification"""
        self.assertFalse(self.student.can_verify_users())
        self.assertTrue(self.dean.can_verify_users())
        self.assertTrue(self.admin.can_verify_users())
    
    def test_can_manage_content(self):
        """Test permission checks for content management"""
        self.assertFalse(self.student.can_manage_content())
        self.assertFalse(self.dean.can_manage_content())
        self.assertTrue(self.admin.can_manage_content())
    
    def test_phone_number_unique(self):
        """Test phone number uniqueness constraint"""
        with self.assertRaises(Exception):
            User.objects.create_user(
                username='duplicate',
                password='testpass123',
                phone_number='+27123456789',  # Same as student
            )
    
    def test_user_str_representation(self):
        """Test string representation of user"""
        self.assertIn('John Doe', str(self.student))
        self.assertIn('Student', str(self.student))


class UserProfileModelTest(TestCase):
    """Test UserProfile model functionality"""
    
    def setUp(self):
        """Create test user with profile"""
        self.user = User.objects.create_user(
            username='profiletest',
            password='testpass123',
            phone_number='+27123456792',
            first_name='Test',
            last_name='User'
        )
        
        self.profile = UserProfile.objects.create(
            user=self.user,
            country='South Africa',
            region='Limpopo',
            church_name='Test Church',
            education_level='undergraduate',
            church_size='medium',
            years_in_ministry=5
        )
    
    def test_profile_creation(self):
        """Test profile is created correctly"""
        self.assertEqual(self.profile.country, 'South Africa')
        self.assertEqual(self.profile.user, self.user)
    
    def test_profile_relationship(self):
        """Test one-to-one relationship with User"""
        self.assertEqual(self.user.profile, self.profile)
    
    def test_is_profile_complete(self):
        """Test profile completion check"""
        self.assertTrue(self.profile.is_profile_complete())
        
        # Remove required field
        self.profile.country = ''
        self.profile.save()
        self.assertFalse(self.profile.is_profile_complete())
    
    def test_get_age(self):
        """Test age calculation"""
        from datetime import date
        self.profile.date_of_birth = date(1990, 1, 1)
        self.profile.save()
        
        age = self.profile.get_age()
        self.assertIsNotNone(age)
        self.assertGreater(age, 0)
    
    def test_get_languages_list(self):
        """Test language list parsing"""
        self.profile.languages_spoken = 'English, Swahili, French'
        self.profile.save()
        
        languages = self.profile.get_languages_list()
        self.assertEqual(len(languages), 3)
        self.assertIn('English', languages)


class RegistrationFormTest(TestCase):
    """Test registration form validation and behaviour"""
    
    def test_valid_registration_form(self):
        """Test form with valid data"""
        form_data = {
            'username': 'newpastor',
            'first_name': 'New',
            'last_name': 'Pastor',
            'phone_number': '+27123456793',
            'email': 'pastor@test.com',
            'country': 'Kenya',
            'region': 'Nairobi',
            'church_name': 'Grace Church',
            'password1': 'SecurePass123!',
            'password2': 'SecurePass123!',
            'accept_terms': True,
        }
        
        form = PastorRegistrationForm(data=form_data)
        self.assertTrue(form.is_valid())
    
    def test_duplicate_phone_number(self):
        """Test form rejects duplicate phone numbers"""
        # Create existing user
        User.objects.create_user(
            username='existing',
            password='testpass123',
            phone_number='+27123456794'
        )
        
        form_data = {
            'username': 'newuser',
            'first_name': 'New',
            'last_name': 'User',
            'phone_number': '+27123456794',  # Duplicate
            'country': 'Kenya',
            'region': 'Nairobi',
            'church_name': 'Test Church',
            'password1': 'SecurePass123!',
            'password2': 'SecurePass123!',
            'accept_terms': True,
        }
        
        form = PastorRegistrationForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('phone_number', form.errors)
    
    def test_phone_number_format(self):
        """Test phone number format validation"""
        form_data = {
            'username': 'testuser',
            'first_name': 'Test',
            'last_name': 'User',
            'phone_number': '0123456789',  # Missing +
            'country': 'Kenya',
            'region': 'Nairobi',
            'church_name': 'Test Church',
            'password1': 'SecurePass123!',
            'password2': 'SecurePass123!',
            'accept_terms': True,
        }
        
        form = PastorRegistrationForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('phone_number', form.errors)
    
    def test_password_mismatch(self):
        """Test form rejects mismatched passwords"""
        form_data = {
            'username': 'testuser',
            'first_name': 'Test',
            'last_name': 'User',
            'phone_number': '+27123456795',
            'country': 'Kenya',
            'region': 'Nairobi',
            'church_name': 'Test Church',
            'password1': 'SecurePass123!',
            'password2': 'DifferentPass123!',
            'accept_terms': True,
        }
        
        form = PastorRegistrationForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('password2', form.errors)


class RegistrationViewTest(TestCase):
    """Test registration view functionality"""
    
    def setUp(self):
        self.client = Client()
        self.register_url = reverse('accounts:register')
    
    def test_registration_page_loads(self):
        """Test registration page is accessible"""
        response = self.client.get(self.register_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/register.html')
    
    def test_successful_registration(self):
        """Test complete registration flow"""
        form_data = {
            'username': 'newpastor',
            'first_name': 'New',
            'last_name': 'Pastor',
            'phone_number': '+27123456796',
            'email': 'pastor@test.com',
            'country': 'Kenya',
            'region': 'Nairobi',
            'church_name': 'Grace Church',
            'password1': 'SecurePass123!',
            'password2': 'SecurePass123!',
            'accept_terms': True,
        }
        
        response = self.client.post(self.register_url, form_data)
        
        # Check user was created
        self.assertTrue(User.objects.filter(username='newpastor').exists())
        
        # Check profile was created
        user = User.objects.get(username='newpastor')
        self.assertTrue(hasattr(user, 'profile'))
        self.assertEqual(user.profile.country, 'Kenya')
        
        # Check redirect
        self.assertEqual(response.status_code, 302)
    
    def test_authenticated_user_redirect(self):
        """Test authenticated users are redirected from registration"""
        user = User.objects.create_user(
            username='existing',
            password='testpass123',
            phone_number='+27123456797'
        )
        
        self.client.login(username='existing', password='testpass123')
        response = self.client.get(self.register_url)
        
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('accounts:dashboard'))


class LoginViewTest(TestCase):
    """Test login functionality"""
    
    def setUp(self):
        self.client = Client()
        self.login_url = reverse('accounts:login')
        
        # Create verified student
        self.student = User.objects.create_user(
            username='student',
            password='testpass123',
            phone_number='+27123456798',
            role=User.STUDENT,
            is_verified=True
        )
        
        # Create unverified student
        self.unverified = User.objects.create_user(
            username='unverified',
            password='testpass123',
            phone_number='+27123456799',
            role=User.STUDENT,
            is_verified=False
        )
    
    def test_login_page_loads(self):
        """Test login page is accessible"""
        response = self.client.get(self.login_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/login.html')
    
    def test_successful_login_with_username(self):
        """Test login with username"""
        response = self.client.post(self.login_url, {
            'username': 'student',
            'password': 'testpass123',
            'remember_me': True,
        })
        
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.wsgi_request.user.is_authenticated)
    
    def test_successful_login_with_phone(self):
        """Test login with phone number"""
        response = self.client.post(self.login_url, {
            'username': '+27123456798',  # Using phone instead of username
            'password': 'testpass123',
            'remember_me': True,
        })
        
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.wsgi_request.user.is_authenticated)
    
    def test_unverified_student_login_blocked(self):
        """Test unverified students cannot login"""
        response = self.client.post(self.login_url, {
            'username': 'unverified',
            'password': 'testpass123',
        })
        
        # Should not redirect (stays on login page)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
    
    def test_invalid_credentials(self):
        """Test login with wrong password"""
        response = self.client.post(self.login_url, {
            'username': 'student',
            'password': 'wrongpassword',
        })
        
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
    
    def test_login_attempt_tracking(self):
        """Test login attempts are logged"""
        initial_count = LoginAttempt.objects.count()
        
        self.client.post(self.login_url, {
            'username': 'student',
            'password': 'testpass123',
        })
        
        # Check login attempt was recorded
        self.assertEqual(LoginAttempt.objects.count(), initial_count + 1)
        
        latest_attempt = LoginAttempt.objects.latest('timestamp')
        self.assertTrue(latest_attempt.success)
        self.assertEqual(latest_attempt.username, 'student')


class DashboardViewTest(TestCase):
    """Test dashboard routing and access"""
    
    def setUp(self):
        self.client = Client()
        
        # Create users with different roles
        self.student = User.objects.create_user(
            username='student',
            password='testpass123',
            phone_number='+27123456800',
            role=User.STUDENT,
            is_verified=True
        )
        UserProfile.objects.create(
            user=self.student,
            country='South Africa',
            region='Limpopo',
            church_name='Test Church',
            education_level='undergraduate'
        )
        
        self.dean = User.objects.create_user(
            username='dean',
            password='testpass123',
            phone_number='+27123456801',
            role=User.DEAN
        )
        UserProfile.objects.create(
            user=self.dean,
            country='South Africa',
            region='Limpopo',
            church_name='Dean Church',
            education_level='postgraduate'
        )
        
        self.admin = User.objects.create_user(
            username='admin',
            password='testpass123',
            phone_number='+27123456802',
            role=User.ADMIN
        )
    
    def test_unauthenticated_redirect(self):
        """Test unauthenticated users redirected to login"""
        response = self.client.get(reverse('accounts:dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)
    
    def test_student_dashboard_routing(self):
        """Test student is routed to student dashboard"""
        self.client.login(username='student', password='testpass123')
        response = self.client.get(reverse('accounts:dashboard'))
        
        self.assertRedirects(response, reverse('accounts:student_dashboard'))
    
    def test_dean_dashboard_routing(self):
        """Test dean is routed to dean dashboard"""
        self.client.login(username='dean', password='testpass123')
        response = self.client.get(reverse('accounts:dashboard'))
        
        self.assertRedirects(response, reverse('accounts:dean_dashboard'))
    
    def test_admin_dashboard_routing(self):
        """Test admin is routed to admin dashboard"""
        self.client.login(username='admin', password='testpass123')
        response = self.client.get(reverse('accounts:dashboard'))
        
        self.assertRedirects(response, reverse('accounts:admin_dashboard'))
    
    def test_student_cannot_access_dean_dashboard(self):
        """Test students cannot access dean dashboard"""
        self.client.login(username='student', password='testpass123')
        response = self.client.get(reverse('accounts:dean_dashboard'))
        
        self.assertEqual(response.status_code, 403)  # Permission denied


class ProfileViewTest(TestCase):
    """Test profile viewing and editing"""
    
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123',
            phone_number='+27123456803',
            first_name='Test',
            last_name='User'
        )
        
        self.profile = UserProfile.objects.create(
            user=self.user,
            country='Kenya',
            region='Nairobi',
            church_name='Test Church',
            education_level='undergraduate'
        )
        
        self.client.login(username='testuser', password='testpass123')
    
    def test_profile_detail_view(self):
        """Test viewing own profile"""
        response = self.client.get(reverse('accounts:profile'))
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test User')
        self.assertContains(response, 'Kenya')
    
    def test_profile_edit_view(self):
        """Test profile edit page loads"""
        response = self.client.get(reverse('accounts:profile_edit'))
        
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/profile_edit.html')
    
    def test_profile_update(self):
        """Test updating profile"""
        update_data = {
            # User form fields
            'first_name': 'Updated',
            'last_name': 'Name',
            'email': 'updated@test.com',
            'phone_number': '+27123456803',
            
            # Profile form fields
            'country': 'Kenya',
            'region': 'Mombasa',  # Changed
            'village_town': 'Test Village',
            'church_name': 'Updated Church',  # Changed
            'church_affiliation': 'Test Network',
            'church_size': 'large',
            'years_in_ministry': 10,
            'position_title': 'Senior Pastor',
            'education_level': 'postgraduate',
            'languages_spoken': 'English, Swahili',
            'bio': 'Updated bio text',
        }
        
        response = self.client.post(reverse('accounts:profile_edit'), update_data)
        
        # Check redirect
        self.assertEqual(response.status_code, 302)
        
        # Verify changes
        self.user.refresh_from_db()
        self.profile.refresh_from_db()
        
        self.assertEqual(self.user.first_name, 'Updated')
        self.assertEqual(self.profile.region, 'Mombasa')
        self.assertEqual(self.profile.church_name, 'Updated Church')


class VerificationWorkflowTest(TestCase):
    """Test student verification workflow"""
    
    def setUp(self):
        self.client = Client()
        
        # Create dean
        self.dean = User.objects.create_user(
            username='dean',
            password='testpass123',
            phone_number='+27123456804',
            role=User.DEAN
        )
        UserProfile.objects.create(
            user=self.dean,
            country='South Africa',
            region='Limpopo',
            church_name='Dean Church',
            education_level='postgraduate'
        )
        
        # Create student in same region
        self.student = User.objects.create_user(
            username='student',
            password='testpass123',
            phone_number='+27123456805',
            role=User.STUDENT,
            is_verified=False
        )
        UserProfile.objects.create(
            user=self.student,
            country='South Africa',
            region='Limpopo',  # Same region as dean
            church_name='Student Church',
            education_level='secondary'
        )
        
        # Create student in different region
        self.other_student = User.objects.create_user(
            username='otherstudent',
            password='testpass123',
            phone_number='+27123456806',
            role=User.STUDENT,
            is_verified=False
        )
        UserProfile.objects.create(
            user=self.other_student,
            country='South Africa',
            region='Gauteng',  # Different region
            church_name='Other Church',
            education_level='primary'
        )
    
    def test_dean_can_verify_own_region_student(self):
        """Test dean can verify students in their region"""
        self.client.login(username='dean', password='testpass123')
        
        response = self.client.post(
            reverse('accounts:verify_student', args=[self.student.id])
        )
        
        self.assertEqual(response.status_code, 302)
        
        # Check student is verified
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_verified)
        self.assertEqual(self.student.verified_by, self.dean)
        self.assertIsNotNone(self.student.verified_at)
    
    def test_dean_cannot_verify_other_region_student(self):
        """Test dean cannot verify students outside their region"""
        self.client.login(username='dean', password='testpass123')
        
        response = self.client.post(
            reverse('accounts:verify_student', args=[self.other_student.id])
        )
        
        self.assertEqual(response.status_code, 403)  # Permission denied
        
        # Check student is not verified
        self.other_student.refresh_from_db()
        self.assertFalse(self.other_student.is_verified)
    
    def test_student_cannot_verify(self):
        """Test students cannot verify other students"""
        self.client.login(username='student', password='testpass123')
        
        response = self.client.post(
            reverse('accounts:verify_student', args=[self.other_student.id])
        )
        
        self.assertEqual(response.status_code, 403)


class StudentListViewTest(TestCase):
    """Test student listing for deans/admins"""
    
    def setUp(self):
        self.client = Client()
        
        # Create admin
        self.admin = User.objects.create_user(
            username='admin',
            password='testpass123',
            phone_number='+27123456807',
            role=User.ADMIN
        )
        
        # Create multiple students
        for i in range(30):
            user = User.objects.create_user(
                username=f'student{i}',
                password='testpass123',
                phone_number=f'+271234568{i:02d}',
                role=User.STUDENT,
                first_name=f'Student{i}',
                last_name='Test'
            )
            UserProfile.objects.create(
                user=user,
                country='Kenya' if i % 2 == 0 else 'Uganda',
                region='Nairobi',
                church_name=f'Church {i}',
                education_level='undergraduate'
            )
    
    def test_student_list_pagination(self):
        """Test student list is paginated"""
        self.client.login(username='admin', password='testpass123')
        
        response = self.client.get(reverse('accounts:student_list'))
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_paginated'])
        self.assertEqual(len(response.context['students']), 25)  # First page
    
    def test_student_search(self):
        """Test searching students"""
        self.client.login(username='admin', password='testpass123')
        
        response = self.client.get(
            reverse('accounts:student_list') + '?search=Student5'
        )
        
        self.assertEqual(response.status_code, 200)
        # Should find student5, student15, student25
        self.assertGreater(len(response.context['students']), 0)
    
    def test_country_filter(self):
        """Test filtering by country"""
        self.client.login(username='admin', password='testpass123')
        
        response = self.client.get(
            reverse('accounts:student_list') + '?country=Kenya'
        )
        
        self.assertEqual(response.status_code, 200)
        # All results should be from Kenya
        for student in response.context['students']:
            self.assertEqual(student.profile.country, 'Kenya')