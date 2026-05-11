from django.urls import path
from .views.marks import MarksDashboardView, MarksCaptureView, TermMarksSummaryView
from .views.curriculum import (
    AssessmentTaskCreateView,
    AssessmentTaskDeactivateView,
    AssessmentTaskListView,
    AssessmentTaskUpdateView,
    GradeSubjectsView,
    OfferingMatrixView,
    SubjectCreateView,
    SubjectListView,
    SubjectOfferingCreateView,
    SubjectOfferingUpdateView,
    SubjectUpdateView,
)
from .views.lms_bulk import BulkCreateAccountsView, BulkEnrollView
from .views.lms_link import (
    LMSAccountsView, CreateLMSAccountView, LMSAccountCredentialsView,
    ResetLMSPasswordView, DeactivateLMSAccountView, ReactivateLMSAccountView,
    RetryMoodleSyncView, LearnerLMSDashboardView,OnlineLearningReportView,
    ClassOnlineStatusView,
)
from .views.analytics import (
    AnalyticsDashboardView, AcademicReportView,
    EnrollmentReportView, ExportAnalyticsExcelView,
)
from .views.portal import (
    PortalDashboardView, PortalChildOverviewView,
    PortalChildMarksView, PortalChildAttendanceView,
    PortalChildReportsView, PortalReportDownloadView,
    ParentAccountsView, CreateParentAccountView, RevokeParentAccountView,
)
from .views.lurits import (
    LURITSDashboardView, RunValidationView,
    ValidationResultsView, ExportDataView, ExportDownloadView,
)
from .views.intervention import (
    AtRiskDashboardView, RunAnalysisView, LearnerAnalysisView,
    InterventionCreateView, InterventionUpdateView, ProgressNoteCreateView,
)
from .views.learner import (
    DocumentServeView,
    DocumentUploadView,
    LearnerCreateView,
    LearnerDeactivateView,
    LearnerDetailView,
    LearnerEnrollView,
    LearnerListView,
    LearnerUpdateView,
)
from .views.lms_link import (
    # Stage 12A (already there)
    LMSAccountsView, CreateLMSAccountView, LMSAccountCredentialsView,
    ResetLMSPasswordView, DeactivateLMSAccountView, ReactivateLMSAccountView,
    RetryMoodleSyncView, LearnerLMSDashboardView,
    # Stage 12B — new
    OnlineLearningDashboardView, LearnerOnlineCoursesView,
    AvailableCoursesView, EnrollConfirmView,
    WithdrawEnrollmentView, SyncProgressView,
)
from .views.structure import (
    GradeListView,
    GradeStreamCreateView,
    GradeStreamUpdateView,
)
from .views.reports import (
    ReportsDashboardView, CommentsCaptureView, ConductCaptureView,
    GenerateReportCardsView, ReportCardListView,
    ReportCardPreviewView, ReportCardDownloadView,
)
from .views.attendance import (
    AttendanceDashboardView, TakeRegisterView,
    AttendanceSummaryView, LearnerAttendanceHistoryView, AttendanceAlertView,
)

app_name = "academics"

urlpatterns = [
    # Learners
    path("learners/", LearnerListView.as_view(), name="learner_list"),
    path("learners/add/", LearnerCreateView.as_view(), name="learner_create"),
    path("learners/<int:pk>/", LearnerDetailView.as_view(), name="learner_detail"),
    path("learners/<int:pk>/edit/", LearnerUpdateView.as_view(), name="learner_update"),
    path("learners/<int:pk>/deactivate/", LearnerDeactivateView.as_view(), name="learner_deactivate"),
    path("learners/<int:pk>/enroll/", LearnerEnrollView.as_view(), name="learner_enroll"),
    path("learners/<int:pk>/documents/<int:doc_pk>/", DocumentServeView.as_view(), name="document_serve"),
    path("learners/<int:pk>/documents/upload/", DocumentUploadView.as_view(), name="document_upload"),

    # Grade and class structure
    path("grades/", GradeListView.as_view(), name="grade_list"),
    path("grades/streams/add/", GradeStreamCreateView.as_view(), name="stream_create"),
    path("grades/streams/<int:pk>/edit/", GradeStreamUpdateView.as_view(), name="stream_update"),

    # Curriculum - subjects
    path("curriculum/subjects/", SubjectListView.as_view(), name="subject_list"),
    path("curriculum/subjects/add/", SubjectCreateView.as_view(), name="subject_create"),
    path("curriculum/subjects/<int:pk>/edit/", SubjectUpdateView.as_view(), name="subject_update"),

    # Curriculum - offering matrix
    path("curriculum/matrix/", OfferingMatrixView.as_view(), name="offering_matrix"),

    # Curriculum - grade-specific subject management
    path("curriculum/grades/<int:pk>/subjects/", GradeSubjectsView.as_view(), name="grade_subjects"),
    path("curriculum/grades/<int:grade_pk>/subjects/add/", SubjectOfferingCreateView.as_view(), name="offering_create"),
    path("curriculum/offerings/<int:pk>/edit/", SubjectOfferingUpdateView.as_view(), name="offering_update"),

    # Curriculum - assessment tasks
    path("curriculum/offerings/<int:pk>/tasks/", AssessmentTaskListView.as_view(), name="task_list"),
    path("curriculum/offerings/<int:offering_pk>/tasks/add/", AssessmentTaskCreateView.as_view(), name="task_create"),
    path("curriculum/tasks/<int:pk>/edit/", AssessmentTaskUpdateView.as_view(), name="task_update"),
    path("curriculum/tasks/<int:pk>/deactivate/", AssessmentTaskDeactivateView.as_view(), name="task_deactivate"),
    path("marks/", MarksDashboardView.as_view(), name="marks_dashboard"),
    path(
        "marks/<int:offering_pk>/<int:stream_pk>/",
        MarksCaptureView.as_view(),
        name="marks_capture",
    ),
    path(
        "marks/<int:offering_pk>/<int:stream_pk>/summary/",
        TermMarksSummaryView.as_view(),
        name="term_summary",
    ),

    # Interventions and performance analysis
    path("intervention/at-risk/", AtRiskDashboardView.as_view(), name="at_risk"),
    path("intervention/analyse/", RunAnalysisView.as_view(), name="run_analysis"),
    path("intervention/<int:learner_pk>/analyse/", RunAnalysisView.as_view(), name="run_analysis_learner"),
    path("intervention/<int:learner_pk>/", LearnerAnalysisView.as_view(), name="learner_analysis"),
    path("intervention/<int:learner_pk>/interventions/add/", InterventionCreateView.as_view(), name="intervention_create"),
    path("intervention/interventions/<int:pk>/edit/", InterventionUpdateView.as_view(), name="intervention_update"),
    path("intervention/<int:learner_pk>/notes/add/", ProgressNoteCreateView.as_view(), name="progress_note_create"),

    # Reports
    path("reports/", ReportsDashboardView.as_view(), name="reports_dashboard"),
    path("reports/<int:offering_pk>/<int:stream_pk>/comments/", CommentsCaptureView.as_view(), name="comments_capture"),
    path("reports/<int:stream_pk>/conduct/", ConductCaptureView.as_view(), name="conduct_capture"),
    path("reports/<int:stream_pk>/generate/", GenerateReportCardsView.as_view(), name="generate_reports"),
    path("reports/<int:stream_pk>/term/<int:term_number>/", ReportCardListView.as_view(), name="report_card_list"),
    path("reports/preview/<int:learner_pk>/<int:term_number>/", ReportCardPreviewView.as_view(), name="report_card_preview"),
    path("reports/download/<int:pk>/", ReportCardDownloadView.as_view(), name="report_card_download"),

    #attendance
    path("attendance/", AttendanceDashboardView.as_view(), name="attendance_dashboard"),
    path("attendance/<int:stream_pk>/register/", TakeRegisterView.as_view(), name="take_register"),
    path("attendance/<int:stream_pk>/summary/", AttendanceSummaryView.as_view(), name="attendance_summary"),
    path("attendance/alerts/", AttendanceAlertView.as_view(), name="attendance_alerts"),
    path("attendance/learner/<int:learner_pk>/", LearnerAttendanceHistoryView.as_view(), name="learner_attendance"),

    #lurits
    path("lurits/", LURITSDashboardView.as_view(), name="lurits_dashboard"),
    path("lurits/validate/", RunValidationView.as_view(), name="lurits_run_validation"),
    path("lurits/validation/", ValidationResultsView.as_view(), name="lurits_validation"),
    path("lurits/export/", ExportDataView.as_view(), name="lurits_export"),
    path("lurits/download/<int:pk>/", ExportDownloadView.as_view(), name="lurits_download"),

    # Parent portal
    path("portal/", PortalDashboardView.as_view(), name="portal_dashboard"),
    path("portal/<int:learner_pk>/", PortalChildOverviewView.as_view(), name="portal_child"),
    path("portal/<int:learner_pk>/marks/", PortalChildMarksView.as_view(), name="portal_marks"),
    path("portal/<int:learner_pk>/attendance/", PortalChildAttendanceView.as_view(), name="portal_attendance"),
    path("portal/<int:learner_pk>/reports/", PortalChildReportsView.as_view(), name="portal_reports"),
    path("portal/<int:learner_pk>/reports/<int:report_pk>/download/", PortalReportDownloadView.as_view(), name="portal_report_download"),

    # Parent account administration
    path("parent-accounts/", ParentAccountsView.as_view(), name="parent_accounts"),
    path("parent-accounts/<int:guardian_pk>/create/", CreateParentAccountView.as_view(), name="parent_account_create"),
    path("parent-accounts/<int:guardian_pk>/revoke/", RevokeParentAccountView.as_view(), name="parent_account_revoke"),

    # Analytics
    path("analytics/", AnalyticsDashboardView.as_view(), name="analytics_dashboard"),
    path("analytics/academic/", AcademicReportView.as_view(), name="analytics_academic"),
    path("analytics/enrollment/", EnrollmentReportView.as_view(), name="analytics_enrollment"),
    path("analytics/export/", ExportAnalyticsExcelView.as_view(), name="analytics_export"),
    


    # Learner-facing (no staff sidebar)
    path('my-online-learning/', LearnerLMSDashboardView.as_view(), name='learner_lms_dashboard'),

    # Staff: Online Learning account management
    path('online/accounts/', LMSAccountsView.as_view(), name='lms_accounts'),
    path('online/accounts/<int:learner_pk>/create/', CreateLMSAccountView.as_view(), name='lms_account_create'),
    path('online/accounts/<int:pk>/credentials/', LMSAccountCredentialsView.as_view(), name='lms_account_credentials'),
    path('online/accounts/<int:pk>/reset-password/', ResetLMSPasswordView.as_view(), name='lms_account_reset_password'),
    path('online/accounts/<int:pk>/deactivate/', DeactivateLMSAccountView.as_view(), name='lms_account_deactivate'),
    path('online/accounts/<int:pk>/reactivate/', ReactivateLMSAccountView.as_view(), name='lms_account_reactivate'),
    path('online/accounts/<int:pk>/retry-sync/', RetryMoodleSyncView.as_view(), name='lms_account_retry_sync'),

    # Stage 12A - bulk account creation and enrollment
    path(
        'online/bulk/create-accounts/',
        BulkCreateAccountsView.as_view(),
        name='bulk_create_accounts',
    ),
    path(
        'online/bulk/enroll/',
        BulkEnrollView.as_view(),
        name='bulk_enroll',
    ),

    # Stage 12B - new online learning management views
    path('online/', OnlineLearningDashboardView.as_view(), name='online_learning_dashboard'),
    path('online/learner/<int:learner_pk>/courses/', LearnerOnlineCoursesView.as_view(), name='learner_online_courses'),
    path('online/learner/<int:learner_pk>/available/', AvailableCoursesView.as_view(), name='available_courses'),
    path('online/learner/<int:learner_pk>/enroll/<int:course_pk>/', EnrollConfirmView.as_view(), name='enroll_confirm'),
    path('online/enrollment/<int:enrollment_pk>/withdraw/', WithdrawEnrollmentView.as_view(), name='withdraw_online_enrollment'),
    path('online/learner/<int:learner_pk>/sync-progress/', SyncProgressView.as_view(), name='sync_online_progress'),

    path(
        'online/report/',
        OnlineLearningReportView.as_view(),
        name='online_learning_report',
    ),
    path(
        'online/class-status/',
        ClassOnlineStatusView.as_view(),
        name='class_online_status',
    ),
]