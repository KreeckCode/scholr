from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.core.exceptions import ValidationError
from .models import User, UserProfile


class PastorRegistrationForm(UserCreationForm):
    """
    Self-registration form for pastors
    Collects essential information while keeping process simple
    Remember: many users have limited technical literacy
    """
    
    # User fields
    first_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'First Name',
            'autocomplete': 'given-name'
        }),
        help_text='Your given name'
    )
    
    last_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Last Name',
            'autocomplete': 'family-name'
        }),
        help_text='Your family name'
    )
    
    phone_number = forms.CharField(
        max_length=17,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '+27123456789',
            'autocomplete': 'tel'
        }),
        help_text='Include country code (e.g., +27 for South Africa)'
    )
    
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'email@example.com (optional)',
            'autocomplete': 'email'
        }),
        help_text='Email is optional but helpful for account recovery'
    )
    
    # Profile fields - only essentials during registration
    country = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Country',
            'autocomplete': 'country-name'
        })
    )
    
    region = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Region/Province/State'
        }),
        help_text='The region where you minister'
    )
    
    church_name = forms.CharField(
        max_length=200,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Church or Ministry Name'
        })
    )
    
    # Terms acceptance
    accept_terms = forms.BooleanField(
        required=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        label='I agree to the terms and conditions'
    )
    
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'phone_number', 'email', 
                  'password1', 'password2']
        widgets = {
            'username': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Username',
                'autocomplete': 'username'
            }),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Customize password fields
        self.fields['password1'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Password',
            'autocomplete': 'new-password'
        })
        self.fields['password2'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Confirm Password',
            'autocomplete': 'new-password'
        })
        
        # Make username help text clearer
        self.fields['username'].help_text = (
            'Letters, numbers and @/./+/-/_ only. Will be used for login.'
        )
    
    def clean_phone_number(self):
        """
        Validate phone number is unique and properly formatted
        This is critical since phone is primary contact method
        """
        phone = self.cleaned_data.get('phone_number')
        
        # Check if phone already exists
        if User.objects.filter(phone_number=phone).exists():
            raise ValidationError(
                'This phone number is already registered. Please contact your dean if you need help.'
            )
        
        # Basic format check - ensure it starts with +
        if not phone.startswith('+'):
            raise ValidationError(
                'Phone number must include country code starting with +'
            )
        
        return phone
    
    def clean_email(self):
        """
        Validate email is unique if provided
        Email is optional but if given must be unique
        """
        email = self.cleaned_data.get('email')
        if email and User.objects.filter(email=email).exists():
            raise ValidationError(
                'This email is already registered. Leave blank or use a different email.'
            )
        return email
    
    def clean(self):
        """
        Additional validation across multiple fields
        Ensures data consistency for profile creation
        """
        cleaned_data = super().clean()
        
        # Ensure at least email or phone for contact
        # Phone is already required, but good to double-check
        phone = cleaned_data.get('phone_number')
        email = cleaned_data.get('email')
        
        if not phone and not email:
            raise ValidationError(
                'You must provide at least a phone number or email address.'
            )
        
        return cleaned_data
    
    def save(self, commit=True):
        """
        Create both User and UserProfile in single transaction
        All new registrations default to student role and unverified status
        """
        user = super().save(commit=False)
        user.role = User.STUDENT
        user.is_verified = False
        
        # Make email blank instead of None if not provided
        if not user.email:
            user.email = ''
        
        if commit:
            user.save()
            
            # Create profile with data from form
            UserProfile.objects.create(
                user=user,
                country=self.cleaned_data['country'],
                region=self.cleaned_data['region'],
                church_name=self.cleaned_data['church_name']
            )
        
        return user


class CustomAuthenticationForm(AuthenticationForm):
    """
    Custom login form supporting username or phone number
    Styled consistently with registration form
    """
    
    username = forms.CharField(
        label='Username or Phone Number',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Username or Phone Number',
            'autocomplete': 'username',
            'autofocus': True
        })
    )
    
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Password',
            'autocomplete': 'current-password'
        })
    )
    
    remember_me = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        label='Keep me signed in'
    )
    
    def clean_username(self):
        """
        Allow login with phone number or username
        Try to find user by phone if username doesn't match
        """
        username = self.cleaned_data.get('username')
        
        # If it looks like a phone number (starts with +), try that
        if username.startswith('+'):
            try:
                user = User.objects.get(phone_number=username)
                return user.username
            except User.DoesNotExist:
                pass
        
        return username


class UserProfileForm(forms.ModelForm):
    """
    Complete profile form for updating all demographic information
    Used after initial registration or by admins
    """
    
    class Meta:
        model = UserProfile
        exclude = ['user', 'registration_notes', 'created_at', 'updated_at']
        widgets = {
            'date_of_birth': forms.DateInput(attrs={
                'type': 'date',
                'class': 'form-control'
            }),
            'village_town': forms.TextInput(attrs={'class': 'form-control'}),
            'church_affiliation': forms.TextInput(attrs={'class': 'form-control'}),
            'church_size': forms.Select(attrs={'class': 'form-control'}),
            'years_in_ministry': forms.NumberInput(attrs={'class': 'form-control'}),
            'position_title': forms.TextInput(attrs={'class': 'form-control'}),
            'education_level': forms.Select(attrs={'class': 'form-control'}),
            'languages_spoken': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., English, Swahili, French'
            }),
            'whatsapp_number': forms.TextInput(attrs={'class': 'form-control'}),
            'bio': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4,
                'maxlength': 500
            }),
            'profile_photo': forms.FileInput(attrs={'class': 'form-control'}),
        }
    
    def clean_languages_spoken(self):
        """
        Validate languages format
        Should be comma-separated list
        """
        languages = self.cleaned_data.get('languages_spoken', '')
        if languages:
            # Basic cleanup
            languages = ', '.join([lang.strip() for lang in languages.split(',')])
        return languages


class ProfileCompletionForm(UserProfileForm):
    """
    Only the fields the completion page shows. Posting the full profile form from that
    page would clear every field it does not render.
    """

    REQUIRED = ('country', 'region', 'church_name', 'education_level')

    class Meta(UserProfileForm.Meta):
        exclude = None
        fields = [
            'country', 'region', 'church_name', 'church_size',
            'position_title', 'education_level', 'languages_spoken',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.REQUIRED:
            self.fields[name].required = True


class UserUpdateForm(forms.ModelForm):
    """
    Form for updating basic user information
    Separate from profile to handle User model fields
    """
    
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'phone_number']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone_number': forms.TextInput(attrs={'class': 'form-control'}),
        }
    
    def clean_phone_number(self):
        """Ensure phone number remains unique when updating"""
        phone = self.cleaned_data.get('phone_number')
        user_id = self.instance.id
        
        if User.objects.filter(phone_number=phone).exclude(id=user_id).exists():
            raise ValidationError('This phone number is already in use.')
        
        return phone
    
    def clean_email(self):
        """Ensure email remains unique when updating"""
        email = self.cleaned_data.get('email')
        user_id = self.instance.id
        
        if email and User.objects.filter(email=email).exclude(id=user_id).exists():
            raise ValidationError('This email is already in use.')
        
        return email


class AdminUserCreationForm(UserCreationForm):
    """
    Form for admins to create users with any role
    Includes role selection and verification options
    """
    
    role = forms.ChoiceField(
        choices=User.ROLE_CHOICES,
        required=True,
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text='Assign user role - determines system permissions'
    )
    
    is_verified = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        label='Mark as verified',
        help_text='Pre-verify this account (recommended for dean/admin accounts)'
    )
    
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'phone_number', 
                  'role', 'is_verified', 'password1', 'password2']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone_number': forms.TextInput(attrs={'class': 'form-control'}),
        }