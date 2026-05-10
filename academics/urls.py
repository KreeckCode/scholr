from django.urls import path

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
    path(
        "learners/<int:pk>/deactivate/",
        LearnerDeactivateView.as_view(),
        name="learner_deactivate",
    ),
    path("learners/<int:pk>/enroll/", LearnerEnrollView.as_view(), name="learner_enroll"),
    # Documents - served through Django, not Nginx, for access control
    path(
        "learners/<int:pk>/documents/<int:doc_pk>/",
        DocumentServeView.as_view(),
        name="document_serve",
    ),
    path(
        "learners/<int:pk>/documents/upload/",
        DocumentUploadView.as_view(),
        name="document_upload",
    ),
    # Grade and class structure
    path("grades/", GradeListView.as_view(), name="grade_list"),
    path("grades/streams/add/", GradeStreamCreateView.as_view(), name="stream_create"),
    path(
        "grades/streams/<int:pk>/edit/",
        GradeStreamUpdateView.as_view(),
        name="stream_update",
    ),
]