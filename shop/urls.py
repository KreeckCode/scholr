"""
Shop URL Configuration

Simplified structure:
- /courses/ → Catalog (browse)
- /courses/cart/ → Shopping cart
- /courses/checkout/ → Purchase
- /courses/my-learning/ → Enrolled courses
- /courses/orders/ → Order history
"""

from django.urls import path
from . import views

app_name = 'shop'

urlpatterns = [
    # ==================== CATALOG ====================
    path('', views.ProductCatalogView.as_view(), name='catalog'),
    path('product/<slug:slug>/', views.ProductDetailView.as_view(), name='product_detail'),
    
    # ==================== CART ====================
    path('cart/', views.cart_detail, name='cart_detail'),
    path('cart/add/<int:product_id>/', views.cart_add, name='cart_add'),
    path('cart/remove/<int:product_id>/', views.cart_remove, name='cart_remove'),
    path('cart/update/<int:product_id>/', views.cart_update, name='cart_update'),
    path('cart/discount/remove/', views.remove_discount, name='remove_discount'),
    path('my-courses/refresh-progress/', views.refresh_progress, name='refresh_progress'),
    
    # ==================== CHECKOUT ====================
    path('checkout/', views.checkout, name='checkout'),
    
    # ==================== PAYMENT ====================
    path('payment/<str:order_number>/', views.payment, name='payment'),
    path('payment/webhook/', views.payment_webhook, name='payment_webhook'),
    path('payment/callback/', views.payment_callback, name='payment_callback'),
    
    # ==================== ORDERS ====================
    path('orders/', views.OrderListView.as_view(), name='order_list'),
    path('orders/<str:order_number>/', views.OrderDetailView.as_view(), name='order_detail'),
    path('orders/<str:order_number>/confirmation/', views.order_confirmation, name='order_confirmation'),
    
    # ==================== MY LEARNING ====================
    path('my-courses/', views.MyCoursesView.as_view(), name='my_courses'),
    
    # ==================== ADMIN TOOLS ====================
    path('admin/bulk-enrollment/', views.BulkEnrollmentView.as_view(), name='bulk_enrollment'),

    # Bundle Management URLs
    path('bundles/', views.BundleListView.as_view(), name='bundle_list'),
    path('bundles/create/', views.BundleCreateView.as_view(), name='bundle_create'),
    path('bundles/<int:bundle_id>/', views.BundleDetailView.as_view(), name='bundle_detail'),
    path('bundles/<int:bundle_id>/edit/', views.BundleEditView.as_view(), name='bundle_edit'),
    path('bundles/<int:bundle_id>/delete/', views.bundle_delete, name='bundle_delete'),
    path('debug/moodle-progress/', views.debug_moodle_progress, name='debug_moodle_progress'),

]