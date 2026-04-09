"""
Shop forms with comprehensive validation and security

Security features:
- CSRF protection (Django default)
- Input sanitization
- Server-side validation
- Rate limiting considerations
"""

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from accounts.models import DiscountCode, User, UserProfile


class CheckoutForm(forms.Form):
    """
    Checkout form for guest/new users
    
    Security:
    - Validates phone number format
    - Sanitizes inputs
    - Email validation
    
    UX:
    - Tailwind CSS classes for styling
    - Placeholder text
    - Help text
    """
    
    first_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            'placeholder': 'First name'
        }),
        error_messages={
            'required': 'Please enter your first name',
            'max_length': 'First name is too long'
        }
    )
    
    last_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            'placeholder': 'Last name'
        }),
        error_messages={
            'required': 'Please enter your last name',
            'max_length': 'Last name is too long'
        }
    )
    
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            'placeholder': 'email@example.com (optional)'
        }),
        help_text='Email is optional but recommended for order confirmation'
    )
    
    phone_number = forms.CharField(
        max_length=17,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            'placeholder': '+27 12 345 6789',
            'pattern': r'^\+?1?\d{9,15}$'
        }),
        help_text='International format preferred (e.g., +27123456789)'
    )
    
    country = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            'placeholder': 'South Africa'
        })
    )
    
    church_name = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            'placeholder': 'Your church name (optional)'
        })
    )
    
    customer_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            'rows': 3,
            'placeholder': 'Any special requests or notes...'
        })
    )
    
    # Terms acceptance
    accept_terms = forms.BooleanField(
        required=True,
        widget=forms.CheckboxInput(attrs={
            'class': 'w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500'
        }),
        error_messages={
            'required': 'You must accept the terms and conditions to continue'
        }
    )
    
    def clean_phone_number(self):
        """
        Validate phone number format
        
        Security: Prevents injection attacks via phone field
        """
        phone = self.cleaned_data['phone_number'].strip()
        
        # Remove common separators
        phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
        
        # Validate format
        import re
        if not re.match(r'^\+?1?\d{9,15}$', phone):
            raise ValidationError(
                'Please enter a valid phone number in international format (e.g., +27123456789)'
            )
        
        return phone
    
    def clean_email(self):
        """
        Validate email and check for existing accounts
        
        UX: Suggests login if email exists
        """
        email = self.cleaned_data.get('email', '').strip().lower()
        
        if email:
            # Check if user exists
            if User.objects.filter(email=email).exists():
                # Don't block, but could add warning
                pass
        
        return email
    
    def clean(self):
        """
        Cross-field validation
        
        Business rule: Email OR phone must be provided for contact
        """
        cleaned_data = super().clean()
        email = cleaned_data.get('email')
        phone = cleaned_data.get('phone_number')
        
        if not email and not phone:
            raise ValidationError(
                'Please provide at least an email address or phone number for order confirmation'
            )
        
        return cleaned_data


class DiscountCodeForm(forms.Form):
    """
    Discount code application form
    
    Security:
    - Rate limiting (should be implemented at view level)
    - Case-insensitive matching
    - Validation against database
    """
    
    code = forms.CharField(
        max_length=50,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent uppercase',
            'placeholder': 'Enter discount code',
            'autocomplete': 'off'
        }),
        error_messages={
            'required': 'Please enter a discount code'
        }
    )
    
    def __init__(self, *args, cart_total=None, user=None, **kwargs):
        """
        Initialize with context
        
        Args:
            cart_total: Current cart total for validation
            user: Current user for usage limit checking
        """
        super().__init__(*args, **kwargs)
        self.cart_total = cart_total
        self.user = user
        self.discount = None
    
    def clean_code(self):
        """
        Validate discount code
        
        Checks:
        - Code exists
        - Code is active
        - Code is within valid dates
        - Usage limits not exceeded
        - User hasn't exceeded personal limit
        - Minimum purchase met
        """
        code = self.cleaned_data['code'].upper().strip()
        
        # Fetch discount
        try:
            discount = DiscountCode.objects.get(code=code)
        except DiscountCode.DoesNotExist:
            raise ValidationError('Invalid discount code')
        
        # Validate status
        if not discount.is_active:
            raise ValidationError('This discount code is no longer active')
        
        # Validate dates
        from django.utils import timezone
        now = timezone.now()
        
        if now < discount.valid_from:
            raise ValidationError('This discount code is not yet valid')
        
        if now > discount.valid_until:
            raise ValidationError('This discount code has expired')
        
        # Validate usage limits
        if discount.max_uses and discount.times_used >= discount.max_uses:
            raise ValidationError('This discount code has reached its usage limit')
        
        # Validate per-user limit
        if self.user and self.user.is_authenticated:
            from accounts.models import Order
            user_usage = Order.objects.filter(
                user=self.user,
                # TODO: Track which discount was used
            ).count()
            
            if user_usage >= discount.max_uses_per_user:
                raise ValidationError('You have already used this discount code')
        
        # Validate minimum purchase
        if self.cart_total and self.cart_total < discount.minimum_purchase:
            raise ValidationError(
                f'Minimum purchase of R{discount.minimum_purchase} required for this discount'
            )
        
        # Store discount for later use
        self.discount = discount
        return code
    
    def get_discount(self):
        """Return validated discount object"""
        return self.discount


class ProductSearchForm(forms.Form):
    """
    Product search and filter form
    
    UX:
    - Simple, intuitive filters
    - Mobile-friendly
    """
    
    q = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            'placeholder': 'Search courses...',
            'autocomplete': 'off'
        })
    )
    
    grade = forms.ChoiceField(
        choices=[('', 'All Grades')],
        required=False,
        widget=forms.Select(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
        })
    )
    
    subject = forms.ChoiceField(
        choices=[('', 'All Subjects')],
        required=False,
        widget=forms.Select(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
        })
    )
    
    price = forms.ChoiceField(
        choices=[
            ('', 'All Prices'),
            ('free', 'Free Only'),
            ('paid', 'Paid Only'),
            ('0-500', 'Under R500'),
            ('500-1000', 'R500 - R1000'),
            ('1000+', 'Over R1000'),
        ],
        required=False,
        widget=forms.Select(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
        })
    )
    
    sort = forms.ChoiceField(
        choices=[
            ('featured', 'Featured First'),
            ('price_asc', 'Price: Low to High'),
            ('price_desc', 'Price: High to Low'),
            ('name_asc', 'Name: A to Z'),
            ('newest', 'Newest First'),
        ],
        required=False,
        initial='featured',
        widget=forms.Select(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
        })
    )
    
    def __init__(self, *args, **kwargs):
        """Populate grade and subject choices from database"""
        super().__init__(*args, **kwargs)
        
        from accounts.models import GradeLevel, Subject
        
        # Populate grade choices
        grades = GradeLevel.objects.filter(is_active=True).values_list('id', 'name')
        self.fields['grade'].choices = [('', 'All Grades')] + list(grades)
        
        # Populate subject choices
        subjects = Subject.objects.filter(is_active=True).values_list('id', 'name')
        self.fields['subject'].choices = [('', 'All Subjects')] + list(subjects)


class BulkEnrollmentForm(forms.Form):
    """
    Admin form for bulk enrollment (comp access)
    
    Security:
    - Admin only
    - Validates user exists
    - Validates product exists
    """
    
    users = forms.CharField(
        widget=forms.Textarea(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent font-mono',
            'rows': 10,
            'placeholder': 'Enter usernames or phone numbers (one per line)'
        }),
        help_text='Enter one username or phone number per line'
    )
    
    product = forms.ModelChoiceField(
        queryset=None,  # Set in __init__
        widget=forms.Select(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
        }),
        help_text='Product to enroll users in'
    )
    
    duration_months = forms.IntegerField(
        min_value=1,
        max_value=120,
        initial=12,
        widget=forms.NumberInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
        }),
        help_text='Access duration in months'
    )
    
    reason = forms.CharField(
        widget=forms.Textarea(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            'rows': 3,
            'placeholder': 'Reason for bulk enrollment (e.g., promotional giveaway, partnership)'
        })
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        from accounts.models import Product
        
        # Only active products
        self.fields['product'].queryset = Product.objects.filter(
            is_active=True
        ).select_related('grade_level', 'subject')
    
    def clean_users(self):
        """
        Parse and validate user list
        
        Returns:
            list: User instances
        """
        users_text = self.cleaned_data['users']
        lines = [line.strip() for line in users_text.split('\n') if line.strip()]
        
        if not lines:
            raise ValidationError('Please enter at least one username or phone number')
        
        users = []
        not_found = []
        
        for identifier in lines:
            # Try username first
            user = User.objects.filter(username=identifier).first()
            
            # Try phone number
            if not user:
                user = User.objects.filter(phone_number=identifier).first()
            
            if user:
                users.append(user)
            else:
                not_found.append(identifier)
        
        if not_found:
            raise ValidationError(
                f'Could not find users: {", ".join(not_found)}'
            )
        
        if not users:
            raise ValidationError('No valid users found')
        
        self.cleaned_users = users
        return users_text
    
    def get_users(self):
        """Return list of validated User instances"""
        return getattr(self, 'cleaned_users', [])