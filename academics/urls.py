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
from .views.structure import (
    GradeListView,
    GradeStreamCreateView,
    GradeStreamUpdateView,
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

    # Add inside urlpatterns:
    path("intervention/at-risk/", AtRiskDashboardView.as_view(), name="at_risk"),
    path("intervention/analyse/", RunAnalysisView.as_view(), name="run_analysis"),
    path("intervention/<int:learner_pk>/analyse/", RunAnalysisView.as_view(), name="run_analysis_learner"),
    path("intervention/<int:learner_pk>/", LearnerAnalysisView.as_view(), name="learner_analysis"),
    path("intervention/<int:learner_pk>/interventions/add/", InterventionCreateView.as_view(), name="intervention_create"),
    path("intervention/interventions/<int:pk>/edit/", InterventionUpdateView.as_view(), name="intervention_update"),
    path("intervention/<int:learner_pk>/notes/add/", ProgressNoteCreateView.as_view(), name="progress_note_create"),


]