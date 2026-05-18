from django.urls import path

from .views.application import (
    EnrollmentLandingView,
    ApplyView,
    ApplicationSuccessView,
    DocumentUploadView,
    ApplicationStatusView,
)
from .views.admin_views import (
    AdminQueueView,
    ApplicationDetailView,
    DocumentReviewView,
    IssueOfferView,
    ApproveApplicationView,
    RejectApplicationView,
    ClearDuplicateView,
    WaitlistView,
    PromoteWaitlistView,
)
from .views.promotion import (
    PromotionDashboardView,
    CreateBulkPromotionView,
    ConfigureBulkPromotionView,
    BatchDetailView,
    ExecuteBatchView,
)
from .views.reenrollment import (
    ReenrollmentLandingView,
    ConfirmDetailsView,
    SubjectSelectionView,
    FeeAgreementView,
    PaymentView,
    ReenrollmentCompleteView,
)

app_name = 'enrollment'

urlpatterns = [
    # ==================== PUBLIC ====================
    path('',                                EnrollmentLandingView.as_view(),  name='landing'),
    path('apply/<int:template_pk>/',        ApplyView.as_view(),              name='apply'),
    path('success/<str:reference>/',        ApplicationSuccessView.as_view(), name='success'),
    path('documents/<str:reference>/',      DocumentUploadView.as_view(),     name='documents'),
    path('status/',                         ApplicationStatusView.as_view(),  name='status'),

    # ==================== ADMIN - APPLICATIONS ====================
    path('admin/queue/',                           AdminQueueView.as_view(),        name='admin_queue'),
    path('admin/application/<int:pk>/',            ApplicationDetailView.as_view(), name='admin_application_detail'),
    path('admin/document/<int:document_pk>/review/', DocumentReviewView.as_view(),  name='admin_document_review'),
    path('admin/application/<int:pk>/issue-offer/',  IssueOfferView.as_view(),      name='admin_issue_offer'),
    path('admin/application/<int:pk>/approve/',      ApproveApplicationView.as_view(), name='admin_approve'),
    path('admin/application/<int:pk>/reject/',       RejectApplicationView.as_view(),  name='admin_reject'),
    path('admin/application/<int:pk>/clear-duplicate/', ClearDuplicateView.as_view(), name='admin_clear_duplicate'),
    path('admin/waitlist/',                        WaitlistView.as_view(),          name='admin_waitlist'),
    path('admin/waitlist/<int:pk>/promote/',       PromoteWaitlistView.as_view(),   name='admin_promote_waitlist'),

    # ==================== ADMIN - PROMOTION ====================
    path('admin/promotion/',                       PromotionDashboardView.as_view(),     name='promotion_dashboard'),
    path('admin/promotion/create/',                CreateBulkPromotionView.as_view(),    name='promotion_create_batch'),
    path('admin/promotion/configure/',             ConfigureBulkPromotionView.as_view(), name='promotion_configure_batch'),
    path('admin/promotion/batch/<int:pk>/',        BatchDetailView.as_view(),            name='promotion_batch_detail'),
    path('admin/promotion/batch/<int:pk>/execute/', ExecuteBatchView.as_view(),          name='promotion_execute_batch'),

    # ==================== PARENT REENROLLMENT PORTAL ====================
    path('reenroll/',            ReenrollmentLandingView.as_view(),  name='reenroll_landing'),
    path('reenroll/details/',    ConfirmDetailsView.as_view(),       name='reenroll_details'),
    path('reenroll/subjects/',   SubjectSelectionView.as_view(),     name='reenroll_subjects'),
    path('reenroll/agreement/',  FeeAgreementView.as_view(),         name='reenroll_agreement'),
    path('reenroll/payment/',    PaymentView.as_view(),              name='reenroll_payment'),
    path('reenroll/complete/',   ReenrollmentCompleteView.as_view(), name='reenroll_complete'),
]