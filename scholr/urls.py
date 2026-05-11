from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView, TemplateView
from django.conf import settings
from django.conf.urls.static import static
import debug_toolbar

urlpatterns = [
    # Admin
    path('admin/', admin.site.urls),
    # Landing page (public homepage)
    path('', include('main.urls')),
    
    # Main entry point - redirects to dashboard
    path('', RedirectView.as_view(url='/dashboard/', permanent=False), name='home'),
    
    # Core apps
    path('dashboard/', include('accounts.urls')),  # User dashboards & auth
    path('courses/', include('shop.urls')),        # Student-facing shop/courses
    path('integration/', include('integration.urls')),  # Moodle sync & LMS access
    path('academics/', include('academics.urls', namespace='academics')),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)


if settings.DEBUG:
    urlpatterns = [path('__debug__/', include(debug_toolbar.urls))] + urlpatterns
    # Error handlers (module-level, outside urlpatterns)

else:
    handler404 = 'main.views.handler404'
    handler500 = 'main.views.handler500'