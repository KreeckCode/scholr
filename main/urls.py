from django.urls import path
from . import views

app_name = 'main'

urlpatterns = [
    path('',              views.HomeView.as_view(),         name='home'),
    path('about/',        views.AboutView.as_view(),        name='about'),
    path('features/',     views.FeaturesView.as_view(),     name='features'),
    path('institutions/', views.InstitutionsView.as_view(), name='institutions'),
    path('pricing/',      views.PricingView.as_view(),      name='pricing'),
    path('demo/',         views.BookDemoView.as_view(),     name='demo'),
    path('contact/',      views.ContactView.as_view(),      name='contact'),
    path('faq/',          views.FaqView.as_view(),          name='faq'),
    path('privacy/',      views.PrivacyView.as_view(),      name='privacy'),
    path('terms/',        views.TermsView.as_view(),        name='terms'),
]