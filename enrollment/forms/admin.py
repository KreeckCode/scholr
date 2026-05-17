"""
Forms for the enrollment admin interface.
Used by enrollment officers to review applications and make decisions.
"""
from django import forms
from django.contrib.auth import get_user_model

from ..models.application import ApplicationDocument

User = get_user_model()


class DocumentReviewForm(forms.Form):
    """
    Approve or reject a single uploaded document.
    Rejection requires a reason that will be emailed to the guardian.
    """
    action = forms.ChoiceField(
        choices=[
            ('approve', 'Approve'),
            ('reject', 'Reject'),
        ],
        widget=forms.RadioSelect,
    )
    rejection_reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 3,
            'placeholder': 'Explain why this document is being rejected (sent to guardian)…',
            'class': 'field',
        }),
        help_text='Required when rejecting a document.',
    )

    def clean(self):
        cleaned = super().clean()
        action = cleaned.get('action')
        reason = cleaned.get('rejection_reason', '').strip()

        if action == 'reject' and not reason:
            self.add_error(
                'rejection_reason',
                'Please explain why you are rejecting this document.',
            )
        
        return cleaned


class IssueOfferForm(forms.Form):
    """
    Issue a conditional offer to an applicant.
    This moves them to STATUS_OFFER_ISSUED and triggers the fee payment step.
    """
    notes = forms.CharField(
        required=False,
        label='Internal notes',
        widget=forms.Textarea(attrs={
            'rows': 3,
            'placeholder': 'Optional notes about this decision (internal only)…',
            'class': 'field',
        }),
    )
    confirm = forms.BooleanField(
        required=True,
        label='I confirm this applicant meets the admission requirements.',
        error_messages={'required': 'Please confirm before issuing an offer.'},
    )


class RejectApplicationForm(forms.Form):
    """
    Reject an application with a reason sent to the guardian.
    """
    rejection_reason = forms.CharField(
        required=True,
        label='Reason for rejection',
        widget=forms.Textarea(attrs={
            'rows': 4,
            'placeholder': 'This will be sent to the guardian. Be clear and professional…',
            'class': 'field',
        }),
        help_text='Explain why the application is being rejected. This will be included in the email to the guardian.',
    )
    internal_notes = forms.CharField(
        required=False,
        label='Internal notes',
        widget=forms.Textarea(attrs={
            'rows': 2,
            'placeholder': 'Optional notes for internal records (not sent to guardian)…',
            'class': 'field',
        }),
    )


class AssignOfficerForm(forms.Form):
    """Assign an application to a specific enrollment officer."""
    
    assigned_to = forms.ModelChoiceField(
        queryset=User.objects.filter(is_staff=True, is_active=True).order_by('first_name', 'last_name'),
        required=False,
        empty_label='— Unassigned —',
        widget=forms.Select(attrs={'class': 'field'}),
    )


class InternalNotesForm(forms.Form):
    """Add or update internal notes on an application."""
    
    internal_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 4,
            'placeholder': 'Notes visible only to enrollment staff…',
            'class': 'field',
        }),
    )