from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    # ==================== AUTHENTICATION ====================
    path('register/', views.RegisterView.as_view(), name='register'),
    path('login/', views.LoginView.as_view(), name='login'),
    path('logout/', views.logout_view, name='logout'),
    
    # ==================== DASHBOARD (UNIFIED) ====================
    # Main dashboard - auto-routes based on role
    path('', views.dashboard_view, name='dashboard'),
    
    # Role-specific dashboards (for direct access if needed)
    path('student/', views.student_dashboard_view, name='student_dashboard'),
    path('dean/', views.dean_dashboard_view, name='dean_dashboard'),
    path('admin/', views.admin_dashboard_view, name='admin_dashboard'),
    
    # ==================== PROFILE MANAGEMENT ====================
    path('profile/', views.ProfileDetailView.as_view(), name='profile'),
    path('profile/edit/', views.ProfileUpdateView.as_view(), name='profile_edit'),
    path('profile/complete/', views.profile_complete_view, name='profile_complete'),
    
    # ==================== STUDENT MANAGEMENT (DEANS/ADMINS) ====================
    path('students/', views.StudentListView.as_view(), name='student_list'),
    path('students/<int:user_id>/', views.student_detail_view, name='student_detail'),
    path('students/<int:user_id>/verify/', views.verify_student_view, name='verify_student'),
    path('students/<int:user_id>/unverify/', views.unverify_student_view, name='unverify_student'),
    
    # ==================== ANALYTICS (ADMINS) ====================
    path('analytics/', views.analytics_view, name='analytics'),
]