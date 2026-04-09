"""
Shop URL Configuration

URL Structure:
- /shop/ → Catalog
- /shop/product/<slug>/ → Product detail
- /shop/cart/ → Cart management
- /shop/checkout/ → Checkout process
- /shop/payment/ → Payment processing
- /shop/orders/ → Order management
- /shop/courses/ → Student courses
- /shop/admin/ → Admin tools

Security:
- CSRF protection on POST endpoints (except webhooks)
- Login required where appropriate
- Admin-only endpoints protected
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
    
    # ==================== STUDENT DASHBOARD ====================
    path('my-courses/', views.MyCoursesView.as_view(), name='my_courses'),
    
    # ==================== ADMIN TOOLS ====================
    path('admin/bulk-enrollment/', views.BulkEnrollmentView.as_view(), name='bulk_enrollment'),
]