"""
Django admin registration for all enrollment models.
Provides a working admin interface immediately in Stage 1
while dedicated views are built in subsequent stages.
"""
from django.contrib import admin

from .models import (
    ApplicationDocument,
    ApplicationDocumentRequirement,
    ApplicationTemplate,
    Application,
    ApplicationStatusHistory,
    BulkPromotionBatch,
    BursaryApplication,
    BursaryDocument,
    ConsentRecord,
    EnrollmentConfig,
    LearnerPromotion,
    Reenrollment,
)


@admin.register(EnrollmentConfig)
class EnrollmentConfigAdmin(admin.ModelAdmin):
    fieldsets = [
        ('Re-enrollment', {
            'fields': ['require_parent_reenrollment_confirmation'],
        }),
        ('Fees', {
            'fields': ['default_registration_fee', 'late_application_fee'],
        }),
        ('Notifications', {
            'fields': ['enrollment_admin_email'],
        }),
        ('Public notice', {
            'fields': ['public_notice'],
        }),
    ]

    def has_add_permission(self, request):
        # Singleton — prevent adding a second record
        return not EnrollmentConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class ApplicationDocumentRequirementInline(admin.TabularInline):
    model = ApplicationDocumentRequirement
    extra = 1
    fields = ['document_type', 'is_required', 'description']


@admin.register(ApplicationTemplate)
class ApplicationTemplateAdmin(admin.ModelAdmin):
    list_display  = ['name', 'entry_grade', 'enrollment_year', 'is_active', 'is_open', 'enrollment_capacity']
    list_filter   = ['enrollment_year', 'is_active', 'entry_grade']
    inlines       = [ApplicationDocumentRequirementInline]
    fieldsets = [
        ('Basics', {
            'fields': ['name', 'entry_grade', 'enrollment_year', 'is_active'],
        }),
        ('Enrollment window', {
            'fields': ['open_date', 'close_date', 'late_applications_allowed'],
        }),
        ('Capacity', {
            'fields': ['enrollment_capacity', 'waitlist_enabled'],
        }),
        ('Registration fee', {
            'fields': [
                'registration_fee_required',
                'registration_fee_amount',
                'registration_fee_is_refundable',
            ],
        }),
        ('Form sections', {
            'fields': [
                'show_previous_school_section',
                'show_medical_section',
                'show_subject_selection',
                'show_essay_section',
                'essay_question',
            ],
        }),
        ('Priority rules', {
            'fields': ['sibling_priority_enabled', 'staff_child_priority_enabled'],
        }),
        ('Consent documents', {
            'fields': ['required_consents'],
        }),
    ]


class ApplicationStatusHistoryInline(admin.TabularInline):
    model  = ApplicationStatusHistory
    extra  = 0
    fields = ['from_status', 'to_status', 'changed_by', 'changed_at', 'notes']
    readonly_fields = ['from_status', 'to_status', 'changed_by', 'changed_at']

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ApplicationDocumentInline(admin.TabularInline):
    model    = ApplicationDocument
    extra    = 0
    fields   = ['document_type', 'status', 'version', 'rejection_reason', 'reviewed_by']
    readonly_fields = ['version']


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display  = [
        'reference_number', 'get_full_name', 'target_grade',
        'enrollment_year', 'status', 'submitted_at',
    ]
    list_filter   = ['status', 'enrollment_year', 'target_grade']
    search_fields = ['reference_number', 'first_name', 'last_name', 'id_number', 'guardian_email']
    readonly_fields = ['reference_number', 'submitted_at', 'converted_at', 'created_at', 'updated_at']
    inlines = [ApplicationDocumentInline, ApplicationStatusHistoryInline]

    fieldsets = [
        ('Reference', {
            'fields': ['reference_number', 'template', 'enrollment_year', 'target_grade', 'status'],
        }),
        ('Applicant', {
            'fields': ['first_name', 'last_name', 'date_of_birth', 'gender', 'id_type', 'id_number', 'nationality', 'home_language'],
        }),
        ('Primary guardian', {
            'fields': [
                'guardian_first_name', 'guardian_last_name', 'guardian_relationship',
                'guardian_email', 'guardian_phone', 'guardian_id_number',
                'guardian_address_line1', 'guardian_city', 'guardian_postal_code',
            ],
        }),
        ('Emergency contact', {
            'fields': ['emergency_contact_name', 'emergency_contact_phone', 'emergency_contact_relationship'],
        }),
        ('Previous school', {
            'fields': [
                'previous_school_name', 'previous_school_type',
                'previous_grade_completed', 'reason_for_leaving',
            ],
            'classes': ['collapse'],
        }),
        ('Medical', {
            'fields': [
                'has_medical_conditions', 'medical_details',
                'has_learning_support_needs', 'learning_support_details',
                'medical_aid_provider', 'medical_aid_number',
            ],
            'classes': ['collapse'],
        }),
        ('Workflow', {
            'fields': [
                'assigned_to', 'internal_notes', 'rejection_reason',
                'waitlist_position', 'priority',
            ],
        }),
        ('Fee', {
            'fields': ['fee_exempted', 'fee_exemption_reason', 'fee_exemption_granted_by', 'fee_order_id'],
            'classes': ['collapse'],
        }),
        ('Conversion', {
            'fields': ['learner', 'converted_at', 'converted_by', 'conversion_error'],
            'classes': ['collapse'],
        }),
        ('Metadata', {
            'fields': ['submitted_at', 'ip_address', 'how_did_you_hear', 'created_at', 'updated_at'],
            'classes': ['collapse'],
        }),
    ]


@admin.register(LearnerPromotion)
class LearnerPromotionAdmin(admin.ModelAdmin):
    list_display  = [
        'learner', 'from_grade', 'to_grade',
        'from_academic_year', 'to_academic_year',
        'status', 'decided_by', 'decided_at',
    ]
    list_filter   = ['status', 'from_academic_year', 'to_academic_year', 'from_grade']
    search_fields = ['learner__first_name', 'learner__last_name', 'learner__admission_number']
    readonly_fields = ['decided_at', 'executed_at', 'created_at']


@admin.register(BulkPromotionBatch)
class BulkPromotionBatchAdmin(admin.ModelAdmin):
    list_display  = ['label', 'from_academic_year', 'to_academic_year', 'total_count', 'success_count', 'executed', 'created_by', 'created_at']
    readonly_fields = ['created_at', 'executed_at', 'total_count', 'success_count', 'failure_count']


@admin.register(Reenrollment)
class ReenrollmentAdmin(admin.ModelAdmin):
    list_display  = ['learner', 'enrollment_year', 'to_grade', 'status', 'created_at']
    list_filter   = ['status', 'enrollment_year']
    search_fields = ['learner__first_name', 'learner__last_name']
    readonly_fields = ['created_at', 'updated_at', 'completed_at']


@admin.register(BursaryApplication)
class BursaryApplicationAdmin(admin.ModelAdmin):
    list_display = [
        'application', 'status', 'household_gross_income',
        'income_frequency', 'bursary_percentage', 'decided_by',
    ]
    list_filter  = ['status']
    readonly_fields = ['submitted_at', 'updated_at']


@admin.register(BursaryDocument)
class BursaryDocumentAdmin(admin.ModelAdmin):
    list_display = ['bursary_application', 'document_type', 'status', 'uploaded_at']
    list_filter  = ['status', 'document_type']