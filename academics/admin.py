from django.contrib import admin

from .models import (
    AcademicYear,
    Department,
    Grade,
    GradeStream,
    Guardian,
    Learner,
    LearnerDocument,
    LearnerEnrollment,
    LearnerGuardian,
    LearnerTransfer,
    School,
    SchoolStaff,
    Term,
)


# ==================== SCHOOL PROFILE ====================

@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Identity", {"fields": ("name", "emis_number", "curriculum", "phase")}),
        ("Classification", {"fields": ("quintile", "is_section_21", "is_independent")}),
        ("Contact", {"fields": ("phone", "email", "website", "fax")}),
        ("Address", {"fields": ("physical_address", "postal_address", "province", "city_town", "postal_code")}),
        ("Leadership", {"fields": ("principal",)}),
        ("Branding", {"fields": ("logo", "primary_colour", "secondary_colour"), "classes": ("collapse",)}),
    )

    def has_add_permission(self, request):
        return not School.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ("year", "start_date", "end_date", "is_current", "term_count")
    list_editable = ("is_current",)
    ordering = ("-year",)

    def term_count(self, obj):
        return f"{obj.terms.count()}/4"
    term_count.short_description = "Terms"


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = (
        "get_label", "academic_year", "start_date", "end_date",
        "marks_open", "marks_deadline", "reports_released", "is_current",
    )
    list_editable = ("marks_open", "reports_released", "is_current")
    list_filter = ("academic_year", "is_current")

    def get_label(self, obj):
        return obj.get_label()
    get_label.short_description = "Term"


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "hod", "active_staff_count", "is_active", "sort_order")
    list_editable = ("sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")

    def active_staff_count(self, obj):
        return obj.staff.filter(is_active=True).count()
    active_staff_count.short_description = "Staff"


@admin.register(SchoolStaff)
class SchoolStaffAdmin(admin.ModelAdmin):
    list_display = (
        "get_full_name", "get_role", "department",
        "employment_type", "persal_number", "sace_number", "is_active",
    )
    list_filter = ("department", "employment_type", "is_active", "user__role")
    search_fields = ("user__first_name", "user__last_name", "persal_number", "sace_number")
    raw_id_fields = ("user",)

    def get_full_name(self, obj):
        return obj.user.get_full_name()
    get_full_name.short_description = "Name"

    def get_role(self, obj):
        return obj.user.get_role_display()
    get_role.short_description = "Role"


# ==================== GRADE STRUCTURE ====================

@admin.register(Grade)
class GradeAdmin(admin.ModelAdmin):
    list_display = ("name", "grade_number", "short_phase", "is_active", "max_learners_per_class")
    list_editable = ("is_active", "max_learners_per_class")
    list_filter = ("phase", "is_active")
    ordering = ("grade_number",)

    def short_phase(self, obj):
        return obj.short_phase
    short_phase.short_description = "Phase"

    def has_add_permission(self, request):
        # Grades are seeded via management command, not created in admin
        return False


@admin.register(GradeStream)
class GradeStreamAdmin(admin.ModelAdmin):
    list_display = (
        "__str__", "grade", "academic_year", "homeroom_teacher",
        "room_number", "capacity", "is_active",
    )
    list_filter = ("academic_year", "grade", "is_active")
    search_fields = ("name", "homeroom_teacher__first_name", "homeroom_teacher__last_name")
    raw_id_fields = ("homeroom_teacher",)


# ==================== LEARNERS ====================

class LearnerGuardianInline(admin.TabularInline):
    model = LearnerGuardian
    extra = 0
    fields = ("guardian", "relationship", "is_primary_contact", "can_collect", "receives_reports")


class LearnerDocumentInline(admin.TabularInline):
    model = LearnerDocument
    extra = 0
    fields = ("document_type", "name", "file", "uploaded_by", "uploaded_at")
    readonly_fields = ("uploaded_at",)


class LearnerEnrollmentInline(admin.TabularInline):
    model = LearnerEnrollment
    extra = 0
    fields = ("academic_year", "grade", "stream", "status", "year_end_outcome", "is_active")


@admin.register(Learner)
class LearnerAdmin(admin.ModelAdmin):
    list_display = (
        "get_full_name", "admission_number", "sa_id_number",
        "current_grade_display", "gender", "is_active",
    )
    list_filter = ("is_active", "gender", "classification", "nationality", "home_language")
    search_fields = (
        "first_name", "last_name", "admission_number",
        "sa_id_number", "emis_learner_number",
    )
    readonly_fields = ("created_at", "updated_at", "created_by", "admission_number")
    inlines = [LearnerEnrollmentInline, LearnerGuardianInline, LearnerDocumentInline]
    fieldsets = (
        ("Identity", {"fields": ("first_name", "last_name", "preferred_name", "admission_number", "user")}),
        ("Identification", {"fields": ("sa_id_number", "birth_cert_number", "emis_learner_number")}),
        ("Demographics", {"fields": ("date_of_birth", "gender", "home_language", "nationality", "classification")}),
        ("Medical", {"fields": ("allergies", "chronic_conditions", "current_medication", "medical_aid_scheme", "medical_aid_number"), "classes": ("collapse",)}),
        ("Transport", {"fields": ("transport_mode", "transport_notes"), "classes": ("collapse",)}),
        ("Address", {"fields": ("street_address", "suburb", "city", "postal_code"), "classes": ("collapse",)}),
        ("Status", {"fields": ("is_active", "photo", "created_by", "created_at", "updated_at")}),
    )

    def get_full_name(self, obj):
        return obj.get_full_name()
    get_full_name.short_description = "Name"

    def current_grade_display(self, obj):
        enrollment = obj.get_current_enrollment()
        return enrollment.grade.name if enrollment else "-"
    current_grade_display.short_description = "Grade"

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Guardian)
class GuardianAdmin(admin.ModelAdmin):
    list_display = ("get_full_name", "phone_primary", "email", "learner_count", "is_active")
    search_fields = ("first_name", "last_name", "phone_primary", "sa_id_number")
    list_filter = ("is_active",)

    def get_full_name(self, obj):
        return obj.get_full_name()
    get_full_name.short_description = "Name"

    def learner_count(self, obj):
        return obj.learner_relationships.count()
    learner_count.short_description = "Learners"