from django.urls import path
from . import views

app_name = 'integration'

urlpatterns = [
    # Main dashboard
    path('', views.integration_dashboard, name='dashboard'),
    
    # Moodle configuration
    path('config/', views.moodle_config, name='moodle_config'),
    path('test-connection/', views.test_connection, name='test_connection'),
    
    # Sync operations
    path('sync/', views.sync_dashboard, name='sync_dashboard'),
    path('sync/trigger/', views.trigger_sync, name='trigger_sync'),
    path('sync/logs/', views.sync_log_list, name='sync_log_list'),
    path('sync/logs/<int:log_id>/', views.sync_log_detail, name='sync_log_detail'),
    
    # Course management
    path('courses/', views.course_list, name='course_list'),
    path('courses/create/', views.course_create, name='course_create'),
    path('courses/<int:course_id>/', views.course_detail, name='course_detail'),
    path('courses/<int:course_id>/edit/', views.course_edit, name='course_edit'),
    path('courses/<int:course_id>/delete/', views.course_delete, name='course_delete'),
    path('courses/<int:course_id>/toggle-auto-enroll/', views.toggle_auto_enroll, name='toggle_auto_enroll'),
    
    
    path('enrollment/preview/', views.preview_bulk_enrollment, name='preview_bulk_enrollment'),
    # Enrollment
    path('enrollment/bulk/', views.bulk_enroll, name='bulk_enroll'),
    
    # Student LMS access
    path('access-lms/', views.access_lms, name='access_lms'),
    
    # AJAX endpoints
    path('api/sync-status/', views.get_sync_status, name='get_sync_status'),
    path('api/check-course-name/', views.check_course_name, name='check_course_name'),
]