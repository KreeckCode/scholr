from django.contrib import admin

from .models import (
    Cohort,
    CohortMembership,
    CohortSession,
    CourseResult,
    Licence,
    LocationAccessLog,
    LocationEvent,
    LocationMembership,
    Participant,
    PartnerContact,
    PartnerOrganisation,
    Partnership,
    PartnershipEvent,
    ProgrammeCourse,
    SessionAttendance,
    SyncOperation,
    TrainingLocation,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    """Audit records are written by services only."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class PartnerContactInline(admin.TabularInline):
    model = PartnerContact
    extra = 0
    fields = ['name', 'role_title', 'email', 'phone', 'is_primary']


@admin.register(PartnerOrganisation)
class PartnerOrganisationAdmin(admin.ModelAdmin):
    list_display = ['name', 'kind', 'country', 'city', 'is_sensitive', 'is_archived']
    list_filter = ['kind', 'is_sensitive', 'is_archived', 'country']
    search_fields = ['name', 'city', 'contacts__email']
    readonly_fields = ['public_id', 'created_at', 'updated_at', 'created_by']
    inlines = [PartnerContactInline]


@admin.register(Partnership)
class PartnershipAdmin(admin.ModelAdmin):
    list_display = ['organisation', 'stage', 'owner', 'next_action_due', 'stage_changed_at']
    list_filter = ['stage', 'source']
    search_fields = ['organisation__name']
    autocomplete_fields = ['organisation']
    # I'm making stage read-only so moves always go through the pipeline service and its gates
    readonly_fields = ['stage', 'stage_before_pause', 'stage_changed_at', 'lost_reason']


@admin.register(PartnershipEvent)
class PartnershipEventAdmin(ReadOnlyAdmin):
    list_display = ['partnership', 'kind', 'from_stage', 'to_stage', 'actor', 'created_at']
    list_filter = ['kind']
    search_fields = ['partnership__organisation__name', 'body']


@admin.register(Licence)
class LicenceAdmin(admin.ModelAdmin):
    list_display = ['number', 'organisation', 'status', 'starts_on', 'ends_on', 'fee_status']
    list_filter = ['status', 'fee_status']
    search_fields = ['number', 'organisation__name']
    readonly_fields = ['public_id', 'number', 'status', 'status_reason', 'status_changed_at', 'created_by',
                       'created_at', 'updated_at']


class LocationMembershipInline(admin.TabularInline):
    model = LocationMembership
    extra = 0
    fields = ['user', 'role', 'trained_on', 'is_active']
    raw_id_fields = ['user']


@admin.register(TrainingLocation)
class TrainingLocationAdmin(admin.ModelAdmin):
    list_display = ['name', 'organisation', 'licence', 'country', 'region', 'status']
    list_filter = ['status', 'country', 'is_sensitive']
    search_fields = ['name', 'organisation__name', 'region']
    readonly_fields = [
        'public_id', 'created_by', 'created_at', 'updated_at', 'position_source',
        'position_confirmed_at', 'position_confirmed_by',
    ]
    inlines = [LocationMembershipInline]


@admin.register(ProgrammeCourse)
class ProgrammeCourseAdmin(admin.ModelAdmin):
    list_display = ['code', 'title', 'sequence', 'classroom_hours', 'online_course', 'is_active']
    list_editable = ['is_active']
    raw_id_fields = ['online_course']


@admin.register(Cohort)
class CohortAdmin(admin.ModelAdmin):
    list_display = ['name', 'location', 'status', 'delivery', 'starts_on']
    list_filter = ['status', 'delivery']
    search_fields = ['name', 'location__name']
    readonly_fields = ['public_id', 'created_by', 'created_at', 'updated_at']
    filter_horizontal = ['facilitators']


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ['first_name', 'last_name', 'home_location', 'phone', 'user']
    search_fields = ['first_name', 'last_name', 'phone', 'email']
    list_filter = ['home_location__country']
    raw_id_fields = ['user']
    readonly_fields = ['public_id', 'client_uuid', 'created_by', 'created_at', 'updated_at']


@admin.register(CohortMembership)
class CohortMembershipAdmin(admin.ModelAdmin):
    list_display = ['participant', 'cohort', 'status', 'access_order', 'access_withdrawn_at']
    list_filter = ['status']
    search_fields = ['participant__first_name', 'participant__last_name', 'cohort__name']
    readonly_fields = ['public_id', 'access_order', 'access_granted_at', 'access_granted_by', 'access_withdrawn_at']


@admin.register(CohortSession)
class CohortSessionAdmin(admin.ModelAdmin):
    list_display = ['cohort', 'course', 'session_date', 'hours', 'status']
    list_filter = ['status', 'course']
    readonly_fields = ['public_id']


@admin.register(SessionAttendance)
class SessionAttendanceAdmin(ReadOnlyAdmin):
    list_display = ['membership', 'session', 'status', 'recorded_by', 'recorded_at']
    list_filter = ['status']


@admin.register(CourseResult)
class CourseResultAdmin(ReadOnlyAdmin):
    list_display = ['membership', 'course', 'exam_score', 'status', 'signed_off_at', 'version']
    list_filter = ['status', 'course']


@admin.register(SyncOperation)
class SyncOperationAdmin(ReadOnlyAdmin):
    list_display = ['op_id', 'kind', 'result', 'user', 'location', 'received_at', 'resolved_at']
    list_filter = ['result', 'kind']
    search_fields = ['op_id']


@admin.register(LocationEvent)
class LocationEventAdmin(admin.ModelAdmin):
    list_display = ['title', 'kind', 'location', 'starts_on', 'status']
    list_filter = ['kind', 'status']
    search_fields = ['title', 'location__name']
    date_hierarchy = 'starts_on'
    raw_id_fields = ['location', 'cohort', 'created_by']
    filter_horizontal = ['attendees']
    readonly_fields = ['public_id', 'created_at', 'updated_at']


@admin.register(LocationAccessLog)
class LocationAccessLogAdmin(ReadOnlyAdmin):
    list_display = ['location', 'user', 'kind', 'ip_address', 'viewed_at']
    list_filter = ['kind']
    search_fields = ['location__name', 'user__username']
