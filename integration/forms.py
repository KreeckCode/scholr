from django import forms
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from .models import MoodleInstance, Course, CourseCategory


class MoodleConfigurationForm(forms.ModelForm):
    """
    Form for configuring Moodle instance settings
    Used by admins to set up connection
    """
    
    class Meta:
        model = MoodleInstance
        fields = [
            'name', 'base_url', 'ws_token', 'ws_service_name',
            'is_active', 'is_primary'
        ]
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., Local Server'
            }),
            'base_url': forms.URLInput(attrs={
                'class': 'form-control',
                'placeholder': 'http://localhost:8888/moodle501'
            }),
            'ws_token': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter your Moodle web service token'
            }),
            'ws_service_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'judeacas'
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'is_primary': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
        }
        help_texts = {
            'base_url': 'Full URL to your Moodle installation (no trailing slash)',
            'ws_token': 'Token from Moodle > Site administration > Server > Web services > Manage tokens',
            'ws_service_name': 'Name of the external service in Moodle',
            'is_primary': 'This instance will be used first for all operations',
        }
    
    def clean_base_url(self):
        """Ensure URL is valid and remove trailing slash"""
        url = self.cleaned_data.get('base_url')
        if url:
            url = url.rstrip('/')
            validator = URLValidator()
            try:
                validator(url)
            except DjangoValidationError:
                raise forms.ValidationError('Please enter a valid URL')
        return url
    
    def clean_ws_token(self):
        """Ensure token is not empty and has reasonable length"""
        token = self.cleaned_data.get('ws_token')
        if token:
            token = token.strip()
            if len(token) < 10:
                raise forms.ValidationError('Token seems too short. Please check your Moodle token.')
        return token
    
    def clean(self):
        """Validate that only one instance can be primary"""
        cleaned_data = super().clean()
        is_primary = cleaned_data.get('is_primary')
        
        if is_primary:
            # Check if another primary exists
            existing_primary = MoodleInstance.objects.filter(is_primary=True)
            
            # Exclude current instance if editing
            if self.instance.pk:
                existing_primary = existing_primary.exclude(pk=self.instance.pk)
            
            if existing_primary.exists():
                # Warn but allow - will be handled in view
                self.add_error('is_primary', 
                    'Another instance is already set as primary. Saving this will make it the new primary.'
                )
        
        return cleaned_data


class CourseCategoryForm(forms.ModelForm):
    """
    Form for creating/editing course categories
    Maps to the 5 disciplines from Judea Harvest
    """
    
    class Meta:
        model = CourseCategory
        fields = ['name', 'discipline', 'description', 'sort_order', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., Biblical Studies'
            }),
            'discipline': forms.Select(attrs={
                'class': 'form-control'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Brief description of this category'
            }),
            'sort_order': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 0
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
        }
    
    def clean_name(self):
        """Ensure category name is unique"""
        name = self.cleaned_data.get('name')
        
        # Check for duplicates
        existing = CourseCategory.objects.filter(name__iexact=name)
        
        if self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        
        if existing.exists():
            raise forms.ValidationError(
                f'A category with the name "{name}" already exists.'
            )
        
        return name


class CourseForm(forms.ModelForm):
    """
    Form for creating/editing courses
    Courses are created on CAS and synced to Moodle
    """
    
    class Meta:
        model = Course
        fields = [
            'title', 'short_name', 'code', 'category', 'summary',
            'start_date', 'end_date', 'visible', 'auto_enroll_enabled',
            'enrollment_capacity'
        ]
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., Introduction to Biblical Hermeneutics'
            }),
            'short_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., BIB101'
            }),
            'code': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Optional course code'
            }),
            'category': forms.Select(attrs={
                'class': 'form-control'
            }),
            'summary': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4,
                'placeholder': 'Course description and objectives'
            }),
            'start_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'end_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'visible': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'auto_enroll_enabled': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'enrollment_capacity': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 1,
                'placeholder': 'Leave empty for unlimited'
            }),
        }
        help_texts = {
            'short_name': 'Unique identifier (no spaces, e.g., BIB101)',
            'visible': 'Students can see and access this course',
            'auto_enroll_enabled': 'Automatically enroll verified students when they register',
            'enrollment_capacity': 'Maximum number of students (leave empty for unlimited)',
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only show active categories
        self.fields['category'].queryset = CourseCategory.objects.filter(is_active=True)
    
    def clean_short_name(self):
        """
        Validate short name is unique and alphanumeric
        No spaces allowed
        """
        short_name = self.cleaned_data.get('short_name')
        
        if short_name:
            # Remove spaces and convert to uppercase
            short_name = short_name.replace(' ', '').upper()
            
            # Check for valid characters
            if not short_name.replace('_', '').replace('-', '').isalnum():
                raise forms.ValidationError(
                    'Short name can only contain letters, numbers, hyphens, and underscores.'
                )
            
            # Check for duplicates
            existing = Course.objects.filter(short_name__iexact=short_name)
            
            if self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            
            if existing.exists():
                raise forms.ValidationError(
                    f'A course with short name "{short_name}" already exists.'
                )
        
        return short_name
    
    def clean(self):
        """Validate date range"""
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        
        if start_date and end_date:
            if end_date <= start_date:
                raise forms.ValidationError(
                    'End date must be after start date.'
                )
        
        return cleaned_data
    
    def save(self, commit=True):
        """Mark course as modified when saved"""
        instance = super().save(commit=False)
        
        # Mark as modified if already synced
        if instance.pk and instance.sync_status == 'synced':
            instance.sync_status = 'modified'
        
        if commit:
            instance.save()
        
        return instance


class BulkEnrollmentForm(forms.Form):
    """
    Form for bulk enrolling students in courses
    Allows filtering by region, education level, etc.
    """
    
    course = forms.ModelChoiceField(
        queryset=Course.objects.filter(visible=True),
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text='Course to enroll students in'
    )
    
    FILTER_CHOICES = [
        ('all', 'All Verified Students'),
        ('region', 'By Region'),
        ('education', 'By Education Level'),
        ('church_size', 'By Church Size'),
    ]
    
    filter_type = forms.ChoiceField(
        choices=FILTER_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'}),
        label='Filter Students By'
    )
    
    region = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g., Limpopo'
        }),
        help_text='Only used if filtering by region'
    )
    
    education_level = forms.ChoiceField(
        required=False,
        choices=[('', '-- Select --')] + list(
            __import__('accounts.models', fromlist=['UserProfile']).UserProfile.EDUCATION_CHOICES
        ),
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text='Only used if filtering by education'
    )
    
    church_size = forms.ChoiceField(
        required=False,
        choices=[('', '-- Select --')] + list(
            __import__('accounts.models', fromlist=['UserProfile']).UserProfile.CHURCH_SIZE_CHOICES
        ),
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text='Only used if filtering by church size'
    )
    
    def clean(self):
        """Validate that required filter fields are provided"""
        cleaned_data = super().clean()
        filter_type = cleaned_data.get('filter_type')
        
        if filter_type == 'region' and not cleaned_data.get('region'):
            self.add_error('region', 'Region is required when filtering by region')
        
        if filter_type == 'education' and not cleaned_data.get('education_level'):
            self.add_error('education_level', 'Education level is required when filtering by education')
        
        if filter_type == 'church_size' and not cleaned_data.get('church_size'):
            self.add_error('church_size', 'Church size is required when filtering by church size')
        
        return cleaned_data


class SyncFilterForm(forms.Form):
    """
    Form for filtering what to sync
    Allows selective sync operations
    """
    
    SYNC_TYPE_CHOICES = [
        ('full', 'Full Sync (Everything)'),
        ('categories', 'Categories Only'),
        ('courses', 'Courses Only'),
        ('users', 'Users Only'),
        ('enrollments', 'Enrollments Only'),
    ]
    
    sync_type = forms.ChoiceField(
        choices=SYNC_TYPE_CHOICES,
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
        initial='full',
        label='What to Sync'
    )
    
    # Course filters
    course_categories = forms.ModelMultipleChoiceField(
        queryset=CourseCategory.objects.filter(is_active=True),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
        help_text='Leave empty to sync all categories'
    )
    
    specific_courses = forms.ModelMultipleChoiceField(
        queryset=Course.objects.filter(sync_status__in=['pending', 'modified', 'failed']),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-control'}),
        help_text='Leave empty to sync all pending courses'
    )
    
    # User filters
    user_region = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g., Limpopo'
        }),
        help_text='Only sync users from this region (leave empty for all)'
    )
    
    unverified_only = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        label='Only sync unverified users',
        initial=False
    )
    
    # Dry run option
    dry_run = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        label='Dry Run (Preview Only)',
        help_text='Show what will be synced without actually syncing',
        initial=False
    )


class TestConnectionForm(forms.Form):
    """
    Simple form for testing Moodle connection
    No fields needed - just triggers connection test
    """
    pass