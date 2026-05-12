from django.urls import path
from .views.application import (
    EnrollmentLandingView,
    ApplyView,
    ApplicationSuccessView,
    DocumentUploadView,
    ApplicationStatusView,
)

app_name = 'enrollment'

urlpatterns = [
    # Public facing
    path('',                                      EnrollmentLandingView.as_view(),  name='landing'),
    path('apply/<int:template_pk>/',              ApplyView.as_view(),              name='apply'),
    path('success/<str:reference>/',              ApplicationSuccessView.as_view(), name='success'),
    path('documents/<str:reference>/',            DocumentUploadView.as_view(),     name='documents'),
    path('status/',                               ApplicationStatusView.as_view(),  name='status'),

    # Admin review views — Stage 3
]