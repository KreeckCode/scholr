from django.urls import path

from .views import api, offline, places, portal, public, staff

app_name = 'partners'

location = 'portal/<uuid:location_id>/'
cohort = f'{location}cohorts/<uuid:cohort_id>/'
session = f'{cohort}sessions/<uuid:session_id>/'

urlpatterns = [
    # ==================== PUBLIC ====================
    path('enquire/', public.EnquiryView.as_view(), name='enquire'),
    path('enquire/thanks/', public.EnquiryThanksView.as_view(), name='enquiry_thanks'),
    path('activate/<uidb64>/<token>/', public.ActivateAccountView.as_view(), name='activate'),
    path('activate/done/', public.ActivateDoneView.as_view(), name='activate_done'),

    # ==================== PROGRAMME STAFF ====================
    path('pipeline/', staff.PipelineBoardView.as_view(), name='pipeline'),
    path('network/', staff.NetworkView.as_view(), name='network'),
    path('network/export.csv', staff.NetworkExportView.as_view(), name='network_export'),
    path('results/', staff.ResultsOverviewView.as_view(), name='results_overview'),
    path('map/', places.MapView.as_view(), name='map'),
    path('map/locations.json', places.MapDataView.as_view(), name='map_data'),
    path('map/locations/<uuid:location_id>/', places.MapPanelView.as_view(), name='map_panel'),
    path('map/trip.pdf', places.TripSheetView.as_view(), name='map_trip'),

    path('organisations/', staff.OrganisationListView.as_view(), name='organisation_list'),
    path('organisations/new/', staff.OrganisationCreateView.as_view(), name='organisation_create'),
    path('organisations/<uuid:org_id>/', staff.OrganisationDetailView.as_view(), name='organisation_detail'),
    path('organisations/<uuid:org_id>/edit/', staff.OrganisationUpdateView.as_view(), name='organisation_edit'),
    path('organisations/<uuid:org_id>/archive/', staff.OrganisationArchiveView.as_view(),
         name='organisation_archive'),
    path('organisations/<uuid:org_id>/move/', staff.PartnershipMoveView.as_view(), name='partnership_move'),
    path('organisations/<uuid:org_id>/note/', staff.PartnershipNoteView.as_view(), name='partnership_note'),
    path('organisations/<uuid:org_id>/next-step/', staff.PartnershipUpdateView.as_view(),
         name='partnership_update'),
    path('organisations/<uuid:org_id>/contacts/new/', staff.ContactCreateView.as_view(), name='contact_create'),
    path('organisations/<uuid:org_id>/contacts/<int:pk>/edit/', staff.ContactUpdateView.as_view(),
         name='contact_edit'),
    path('organisations/<uuid:org_id>/contacts/<int:pk>/delete/', staff.ContactDeleteView.as_view(),
         name='contact_delete'),
    path('organisations/<uuid:org_id>/licences/new/', staff.LicenceCreateView.as_view(), name='licence_create'),
    path('organisations/<uuid:org_id>/locations/new/', staff.LocationCreateView.as_view(),
         name='location_create'),

    path('licences/', staff.LicenceListView.as_view(), name='licence_list'),
    path('licences/<uuid:licence_id>/', staff.LicenceDetailView.as_view(), name='licence_detail'),
    path('licences/<uuid:licence_id>/edit/', staff.LicenceUpdateView.as_view(), name='licence_edit'),
    path('licences/<uuid:licence_id>/delete/', staff.LicenceDeleteView.as_view(), name='licence_delete'),
    path('licences/<uuid:licence_id>/agreement/', staff.LicenceAgreementView.as_view(), name='licence_agreement'),
    path('licences/<uuid:licence_id>/<slug:action>/', staff.LicenceActionView.as_view(), name='licence_action'),

    path('locations/<uuid:location_id>/edit/', staff.LocationUpdateView.as_view(), name='location_edit'),

    # ==================== LOCATION PORTAL ====================
    path('portal/', portal.PortalHomeView.as_view(), name='portal_home'),
    path(location, portal.LocationDashboardView.as_view(), name='location_dashboard'),

    path(f'{location}participants/', portal.ParticipantListView.as_view(), name='participant_list'),
    path(f'{location}participants/new/', portal.ParticipantCreateView.as_view(), name='participant_create'),
    path(f'{location}participants/import/', portal.ParticipantImportView.as_view(), name='participant_import'),
    path(f'{location}participants/<uuid:participant_id>/', portal.ParticipantDetailView.as_view(),
         name='participant_detail'),
    path(f'{location}participants/<uuid:participant_id>/edit/', portal.ParticipantUpdateView.as_view(),
         name='participant_edit'),

    path(f'{location}cohorts/new/', portal.CohortCreateView.as_view(), name='cohort_create'),
    path(cohort, portal.CohortDetailView.as_view(), name='cohort_detail'),
    path(f'{cohort}edit/', portal.CohortUpdateView.as_view(), name='cohort_edit'),
    path(f'{cohort}status/', portal.CohortStatusView.as_view(), name='cohort_status'),
    path(f'{cohort}enrol/', portal.CohortEnrolView.as_view(), name='cohort_enrol'),
    path(f'{cohort}results/', portal.ResultsView.as_view(), name='cohort_results'),
    path(f'{cohort}results/overview/', portal.CohortResultsOverviewView.as_view(), name='cohort_results_overview'),
    path(f'{cohort}members/<uuid:membership_id>/<slug:action>/', portal.MembershipActionView.as_view(),
         name='membership_action'),

    path(f'{cohort}sessions/new/', portal.SessionCreateView.as_view(), name='session_create'),
    path(f'{session}edit/', portal.SessionUpdateView.as_view(), name='session_edit'),
    path(f'{session}delete/', portal.SessionDeleteView.as_view(), name='session_delete'),
    path(f'{session}register/', portal.SessionRegisterView.as_view(), name='session_register'),
    path(f'{session}register.pdf', portal.SessionRegisterPdfView.as_view(), name='session_register_pdf'),

    path(f'{location}team/', portal.TeamView.as_view(), name='team'),
    path(f'{location}team/invite/', portal.MemberInviteView.as_view(), name='member_invite'),
    path(f'{location}team/<int:pk>/<slug:action>/', portal.MemberActionView.as_view(), name='member_action'),

    path(f'{location}place/', places.LocationPlaceView.as_view(), name='location_place'),
    path(f'{location}place/edit/', places.LocationPlaceUpdateView.as_view(), name='location_place_edit'),
    path(f'{location}events/new/', places.EventCreateView.as_view(), name='event_create'),
    path(f'{location}events/<uuid:event_id>/edit/', places.EventUpdateView.as_view(), name='event_edit'),
    path(f'{location}events/<uuid:event_id>/cancel/', places.EventCancelView.as_view(), name='event_cancel'),

    path(f'{location}conflicts/', portal.ConflictListView.as_view(), name='conflicts'),
    path(f'{location}conflicts/<uuid:op_id>/resolve/', portal.ConflictResolveView.as_view(),
         name='conflict_resolve'),

    # ==================== OFFLINE ====================
    path('offline/', offline.OfflineAppView.as_view(), name='offline_app'),
    path('sw.js', offline.ServiceWorkerView.as_view(), name='service_worker'),
    path('manifest.webmanifest', offline.ManifestView.as_view(), name='manifest'),
    path('api/me/', api.MeView.as_view(), name='api_me'),
    path('api/cohorts/<uuid:cohort_id>/pack/', api.CohortPackView.as_view(), name='api_pack'),
    path('api/sync/', api.SyncView.as_view(), name='api_sync'),
]
