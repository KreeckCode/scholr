"""Programme staff screens: pipeline, organisations, contacts, licences, locations and the network view."""

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Prefetch, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from accounts.models import User
from partners.exceptions import PartnersError
from partners.forms import (
    ContactForm,
    LicenceForm,
    LicenceReasonForm,
    LocationForm,
    OrganisationForm,
    PartnershipForm,
    PartnershipMoveForm,
    PartnershipNoteForm,
)
from partners.mixins import (
    FIELD_NOTES, HUB_EDITS, NETWORK_READS, RESULTS_READS, NetworkStaffMixin, ServiceErrorMixin,
)
from partners.models import (
    FORWARD_STAGES,
    Licence,
    LocationMembership,
    PartnerContact,
    PartnerOrganisation,
    Stage,
    StaffAssignment,
    TrainingLocation,
    country_name,
)
from partners.services import exports, licences, network, organisations, pipeline, reach, results, scope
from partners.utils import workbook_response
from partners.utils import write_csv


def organisation_url(organisation):
    return reverse('partners:organisation_detail', kwargs={'org_id': organisation.public_id})


def licence_url(licence):
    return reverse('partners:licence_detail', kwargs={'licence_id': licence.public_id})


def limit_countries(form, user):
    """Hub administrators only see the countries they cover in a partner's country list."""
    if scope.network_role(user) == User.HUB_ADMIN:
        covered = set(scope.assigned_countries(user))
        form.fields['country'].choices = [
            (code, name) for code, name in form.fields['country'].choices if code in covered
        ]
    return form


# ==================== PIPELINE ====================

class PipelineBoardView(NetworkStaffMixin, TemplateView):
    network_roles = NETWORK_READS
    template_name = 'partners/staff/pipeline.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        queryset = (
            scope.partnerships_in_reach(self.request.user).filter(organisation__is_archived=False)
            .select_related('organisation', 'owner')
            .order_by('next_action_due', '-stage_changed_at')
        )
        owner = self.request.GET.get('owner')
        country = (self.request.GET.get('country') or '').upper()
        if owner == 'me':
            queryset = queryset.filter(owner=self.request.user)
        if country:
            queryset = queryset.filter(organisation__country=country)

        by_stage = {stage: [] for stage in FORWARD_STAGES}
        side = {Stage.PAUSED: [], Stage.LOST: []}
        for partnership in queryset:
            partnership.overdue = partnership.is_overdue(today)
            bucket = by_stage.get(partnership.stage, side.get(partnership.stage))
            if bucket is not None:
                bucket.append(partnership)

        labels = dict(Stage.choices)
        context.update({
            'columns': [{'stage': stage, 'label': labels[stage], 'items': by_stage[stage]}
                        for stage in FORWARD_STAGES],
            'paused': side[Stage.PAUSED],
            'lost': side[Stage.LOST],
            'filter_owner': owner or '',
            'filter_country': country,
            'countries': (
                scope.organisations_in_reach(self.request.user).filter(is_archived=False)
                .order_by('country').values_list('country', flat=True).distinct()
            ),
            'overdue_count': sum(1 for column in by_stage.values() for item in column if item.overdue),
        })
        return context


# ==================== ORGANISATIONS ====================

class OrganisationListView(NetworkStaffMixin, ListView):
    network_roles = NETWORK_READS
    template_name = 'partners/staff/organisation_list.html'
    context_object_name = 'organisations'
    paginate_by = 25

    def get_queryset(self):
        queryset = (
            scope.organisations_in_reach(self.request.user).select_related('partnership')
            .annotate(location_count=Count('locations', distinct=True))
            .order_by('name')
        )
        query = (self.request.GET.get('q') or '').strip()
        if query:
            queryset = queryset.filter(Q(name__icontains=query) | Q(city__icontains=query))
        if self.request.GET.get('archived') != '1':
            queryset = queryset.filter(is_archived=False)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['query'] = self.request.GET.get('q', '')
        context['show_archived'] = self.request.GET.get('archived') == '1'
        return context


class OrganisationCreateView(NetworkStaffMixin, CreateView):
    network_roles = FIELD_NOTES
    template_name = 'partners/staff/organisation_form.html'
    form_class = OrganisationForm

    def get_form(self, form_class=None):
        return limit_countries(super().get_form(form_class), self.request.user)

    def form_valid(self, form):
        if not scope.can_place_partner_in(self.request.user, form.cleaned_data['country']) \
                and scope.network_role(self.request.user) != User.AMBASSADOR:
            form.add_error('country', 'You can only add partners in the countries you cover.')
            return self.form_invalid(form)
        self.object = organisations.create_organisation(form.save(commit=False), self.request.user)
        if scope.network_role(self.request.user) == User.AMBASSADOR:
            # An ambassador who brings in a partner looks after it
            StaffAssignment.objects.get_or_create(
                user=self.request.user, organisation=self.object, defaults={'added_by': self.request.user},
            )
        messages.success(self.request, f'{self.object.name} added to the pipeline.')
        return redirect('partners:organisation_detail', org_id=self.object.public_id)


class OrganisationObjectMixin(NetworkStaffMixin):
    def get_organisation(self):
        if not hasattr(self, '_organisation'):
            # Outside someone's reach a partner does not exist for them
            self._organisation = get_object_or_404(
                scope.organisations_in_reach(self.request.user), public_id=self.kwargs['org_id'],
            )
        return self._organisation


class OrganisationDetailView(OrganisationObjectMixin, DetailView):
    network_roles = NETWORK_READS
    template_name = 'partners/staff/organisation_detail.html'
    context_object_name = 'organisation'

    def get_object(self, queryset=None):
        return get_object_or_404(
            scope.organisations_in_reach(self.request.user).prefetch_related(
                'contacts',
                Prefetch('licences', queryset=Licence.objects.order_by('-starts_on')),
                Prefetch('locations', queryset=TrainingLocation.objects.select_related('licence')),
            ),
            public_id=self.kwargs['org_id'],
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organisation = self.object
        partnership = pipeline.ensure_partnership(organisation, self.request.user)
        context.update({
            'partnership': partnership,
            'events': partnership.events.select_related('actor')[:50],
            'move_form': PartnershipMoveForm(partnership=partnership, actor=self.request.user),
            'note_form': PartnershipNoteForm(),
            'partnership_form': PartnershipForm(instance=partnership),
            'unmet_next': pipeline.unmet_gates(partnership, self._next_stage(partnership)),
            'forward_stages': [(stage, dict(Stage.choices)[stage]) for stage in FORWARD_STAGES],
            'coordinators': LocationMembership.objects.filter(
                location__organisation=organisation, role=LocationMembership.Role.COORDINATOR,
            ).select_related('user', 'location'),
            'coverage': reach.coverage_for(organisation),
        })
        if self.request.user.role == User.ADMIN:
            covering = {person.pk for person in context['coverage']['assigned']}
            context['coverage_candidates'] = (
                User.objects.filter(role__in=User.NETWORK_SCOPED_ROLES, is_active=True)
                .exclude(pk__in=covering).order_by('first_name', 'last_name')
            )
        return context

    @staticmethod
    def _next_stage(partnership):
        index = partnership.stage_index
        if index is None or index + 1 >= len(FORWARD_STAGES):
            return None
        return FORWARD_STAGES[index + 1]


class OrganisationUpdateView(OrganisationObjectMixin, UpdateView):
    network_roles = HUB_EDITS
    template_name = 'partners/staff/organisation_form.html'
    form_class = OrganisationForm
    context_object_name = 'organisation'

    def get_object(self, queryset=None):
        return self.get_organisation()

    def get_form(self, form_class=None):
        return limit_countries(super().get_form(form_class), self.request.user)

    def form_valid(self, form):
        if not scope.can_place_partner_in(self.request.user, form.cleaned_data['country']):
            form.add_error('country', 'You can only move a partner to a country you cover.')
            return self.form_invalid(form)
        form.save()
        messages.success(self.request, 'Partner details saved.')
        return redirect('partners:organisation_detail', org_id=self.object.public_id)


class OrganisationArchiveView(OrganisationObjectMixin, ServiceErrorMixin, View):
    network_roles = HUB_EDITS
    http_method_names = ['post']

    def post(self, request, org_id):
        organisation = self.get_organisation()
        archive = request.POST.get('archive') == '1'
        try:
            organisations.set_archived(organisation, request.user, archive)
        except PartnersError as exc:
            return self.service_error(exc, organisation_url(organisation))
        messages.success(request, f"{organisation.name} {'archived' if archive else 'restored'}.")
        return redirect('partners:organisation_detail', org_id=organisation.public_id)


class PartnershipMoveView(OrganisationObjectMixin, ServiceErrorMixin, View):
    network_roles = HUB_EDITS
    http_method_names = ['post']

    def post(self, request, org_id):
        organisation = self.get_organisation()
        partnership = pipeline.ensure_partnership(organisation, request.user)
        form = PartnershipMoveForm(request.POST, partnership=partnership, actor=request.user)
        if not form.is_valid():
            messages.error(request, 'Choose a stage to move to.')
            return redirect(organisation_url(organisation))
        try:
            pipeline.move(partnership, form.cleaned_data['to_stage'], request.user, form.cleaned_data['note'])
        except PartnersError as exc:
            return self.service_error(exc, organisation_url(organisation))
        messages.success(request, 'Stage updated.')
        return redirect(organisation_url(organisation))


class PartnershipNoteView(OrganisationObjectMixin, ServiceErrorMixin, View):
    network_roles = FIELD_NOTES
    http_method_names = ['post']

    def post(self, request, org_id):
        organisation = self.get_organisation()
        partnership = pipeline.ensure_partnership(organisation, request.user)
        form = PartnershipNoteForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Write something in the note before saving.')
            return redirect(organisation_url(organisation))
        try:
            pipeline.add_event(partnership, request.user, form.cleaned_data['kind'], form.cleaned_data['body'])
        except PartnersError as exc:
            return self.service_error(exc, organisation_url(organisation))
        messages.success(request, 'Note added.')
        return redirect(organisation_url(organisation))


class PartnershipUpdateView(OrganisationObjectMixin, View):
    network_roles = FIELD_NOTES
    http_method_names = ['post']

    def post(self, request, org_id):
        organisation = self.get_organisation()
        partnership = pipeline.ensure_partnership(organisation, request.user)
        form = PartnershipForm(request.POST, instance=partnership)
        if form.is_valid():
            form.save()
            messages.success(request, 'Next step saved.')
        else:
            messages.error(request, 'Check the next step details: ' + ' '.join(
                error for errors in form.errors.values() for error in errors
            ))
        return redirect(organisation_url(organisation))


# ==================== CONTACTS ====================

class ContactCreateView(OrganisationObjectMixin, CreateView):
    network_roles = HUB_EDITS
    template_name = 'partners/staff/contact_form.html'
    form_class = ContactForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['organisation'] = self.get_organisation()
        return context

    def form_valid(self, form):
        contact = form.save(commit=False)
        contact.organisation = self.get_organisation()
        organisations.save_contact(contact, self.request.user)
        messages.success(self.request, 'Contact added.')
        return redirect(organisation_url(contact.organisation))


class ContactUpdateView(OrganisationObjectMixin, UpdateView):
    network_roles = HUB_EDITS
    template_name = 'partners/staff/contact_form.html'
    form_class = ContactForm
    context_object_name = 'contact'

    def get_object(self, queryset=None):
        return get_object_or_404(PartnerContact, pk=self.kwargs['pk'], organisation=self.get_organisation())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['organisation'] = self.get_organisation()
        return context

    def form_valid(self, form):
        organisations.save_contact(form.save(commit=False), self.request.user)
        messages.success(self.request, 'Contact saved.')
        return redirect(organisation_url(self.get_organisation()))


class ContactDeleteView(OrganisationObjectMixin, View):
    network_roles = HUB_EDITS
    http_method_names = ['post']

    def post(self, request, org_id, pk):
        contact = get_object_or_404(PartnerContact, pk=pk, organisation=self.get_organisation())
        contact.delete()
        messages.success(request, 'Contact removed.')
        return redirect(organisation_url(self.get_organisation()))


# ==================== LICENCES ====================

class LicenceListView(NetworkStaffMixin, ListView):
    network_roles = NETWORK_READS
    template_name = 'partners/staff/licence_list.html'
    context_object_name = 'licences'
    paginate_by = 30

    def get_queryset(self):
        queryset = scope.licences_in_reach(self.request.user).select_related('organisation').order_by('ends_on')
        status = self.request.GET.get('status')
        if status in Licence.Status.values:
            queryset = queryset.filter(status=status)
        if self.request.GET.get('expiring') == '1':
            today = timezone.localdate()
            queryset = queryset.filter(
                status=Licence.Status.ACTIVE, ends_on__lte=today + timedelta(days=90),
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'statuses': Licence.Status.choices,
            'filter_status': self.request.GET.get('status', ''),
            'filter_expiring': self.request.GET.get('expiring') == '1',
            'today': timezone.localdate(),
        })
        return context


class LicenceCreateView(OrganisationObjectMixin, CreateView):
    network_roles = HUB_EDITS
    template_name = 'partners/staff/licence_form.html'
    form_class = LicenceForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['organisation'] = self.get_organisation()
        return context

    def form_valid(self, form):
        licence = form.save(commit=False)
        licence.organisation = self.get_organisation()
        try:
            self.object = licences.create_licence(licence, self.request.user)
        except PartnersError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)
        messages.success(self.request, f'Licence {self.object.number} drafted.')
        return redirect('partners:licence_detail', licence_id=self.object.public_id)


class LicenceObjectMixin(NetworkStaffMixin):
    def get_licence(self):
        if not hasattr(self, '_licence'):
            self._licence = get_object_or_404(
                scope.licences_in_reach(self.request.user).select_related('organisation'),
                public_id=self.kwargs['licence_id'],
            )
        return self._licence


class LicenceDetailView(LicenceObjectMixin, DetailView):
    network_roles = NETWORK_READS
    template_name = 'partners/staff/licence_detail.html'
    context_object_name = 'licence'

    def get_object(self, queryset=None):
        return self.get_licence()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'locations': self.object.locations.all(),
            'reason_form': LicenceReasonForm(),
            'today': timezone.localdate(),
        })
        return context


class LicenceUpdateView(LicenceObjectMixin, UpdateView):
    network_roles = HUB_EDITS
    template_name = 'partners/staff/licence_form.html'
    form_class = LicenceForm
    context_object_name = 'licence'

    def get_object(self, queryset=None):
        licence = self.get_licence()
        if licence.status == Licence.Status.TERMINATED:
            raise PermissionDenied('A terminated licence cannot be edited.')
        return licence

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['organisation'] = self.object.organisation
        return context

    def form_valid(self, form):
        form.save()
        messages.success(self.request, 'Licence saved.')
        return redirect('partners:licence_detail', licence_id=self.object.public_id)


class LicenceDeleteView(LicenceObjectMixin, View):
    http_method_names = ['post']

    def post(self, request, licence_id):
        licence = self.get_licence()
        if licence.status != Licence.Status.DRAFT or licence.locations.exists():
            messages.error(request, 'Only a draft licence with no locations can be deleted.')
            return redirect('partners:licence_detail', licence_id=licence.public_id)
        organisation = licence.organisation
        if licence.agreement_file:
            licence.agreement_file.delete(save=False)
        licence.delete()
        messages.success(request, 'Draft licence deleted.')
        return redirect(organisation_url(organisation))


class LicenceActionView(LicenceObjectMixin, ServiceErrorMixin, View):
    http_method_names = ['post']
    ACTIONS = {'activate', 'suspend', 'terminate'}

    def post(self, request, licence_id, action):
        if action not in self.ACTIONS:
            raise Http404
        licence = self.get_licence()
        form = LicenceReasonForm(request.POST)
        reason = form.cleaned_data['reason'] if form.is_valid() else ''
        try:
            if action == 'activate':
                licences.activate(licence, request.user)
            elif action == 'suspend':
                licences.suspend(licence, request.user, reason)
            else:
                licences.terminate(licence, request.user, reason)
        except PartnersError as exc:
            return self.service_error(exc, licence_url(licence))
        messages.success(request, f'Licence {licence.number} updated.')
        return redirect('partners:licence_detail', licence_id=licence.public_id)


class LicenceAgreementView(LicenceObjectMixin, View):
    """Streams the signed agreement from private storage to programme staff and leaders."""
    network_roles = NETWORK_READS

    def get(self, request, licence_id):
        licence = self.get_licence()
        if not licence.agreement_file:
            raise Http404
        try:
            handle = licence.agreement_file.open('rb')
        except FileNotFoundError:
            raise Http404
        return FileResponse(
            handle, as_attachment=True, filename=f'{licence.number}-agreement.pdf',
            content_type='application/pdf',
        )


# ==================== LOCATIONS ====================

class LocationCreateView(OrganisationObjectMixin, ServiceErrorMixin, CreateView):
    network_roles = HUB_EDITS
    template_name = 'partners/staff/location_form.html'
    form_class = LocationForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organisation'] = self.get_organisation()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['organisation'] = self.get_organisation()
        return context

    def form_valid(self, form):
        location = form.save(commit=False)
        location.organisation = self.get_organisation()
        try:
            organisations.save_location(location, self.request.user)
        except PartnersError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)
        messages.success(self.request, f'{location.name} created. Invite its coordinator next.')
        return redirect('partners:team', location_id=location.public_id)


class LocationUpdateView(NetworkStaffMixin, UpdateView):
    network_roles = HUB_EDITS
    template_name = 'partners/staff/location_form.html'
    form_class = LocationForm
    context_object_name = 'location'

    def get_object(self, queryset=None):
        return get_object_or_404(
            scope.locations_in_reach(self.request.user).select_related('organisation'),
            public_id=self.kwargs['location_id'],
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organisation'] = self.object.organisation
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['organisation'] = self.object.organisation
        return context

    def form_valid(self, form):
        try:
            organisations.save_location(form.save(commit=False), self.request.user)
        except PartnersError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)
        messages.success(self.request, 'Location saved.')
        return redirect('partners:location_dashboard', location_id=self.object.public_id)


# ==================== NETWORK ====================

class NetworkView(NetworkStaffMixin, TemplateView):
    network_roles = NETWORK_READS
    template_name = 'partners/staff/network.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        rows = network.location_rows(scope.locations_in_reach(self.request.user))
        context.update(network.summarise(rows, scope.organisations_in_reach(self.request.user)))
        context['rows'] = rows
        return context


class NetworkExportView(NetworkStaffMixin, View):
    """CSV of every location's counts. Names of sensitive locations are withheld."""

    def get(self, request):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = (
            f'attachment; filename="metp-network-{timezone.localdate():%Y-%m-%d}.csv"'
        )
        header = [
            'Country', 'Region', 'Partner', 'Location', 'Licence', 'Status', 'Enrolled', 'Completed',
            'Withdrawn', 'Online', 'Running cohorts', 'Sessions held', 'Attendance %',
        ]
        rows = []
        for row in network.location_rows():
            location = row['location']
            hidden = location.hides_identities
            rows.append([
                row['country_name'], row['region'],
                'Withheld' if hidden else location.organisation.name,
                'Withheld' if hidden else location.name,
                location.licence.number, location.get_status_display(),
                row['enrolled'], row['completed'], row['withdrawn'], row['online'],
                row['cohorts_running'], row['sessions_held'],
                '' if row['attendance_rate'] is None else row['attendance_rate'],
            ])
        return write_csv(response, header, rows)


class ResultsOverviewView(NetworkStaffMixin, TemplateView):
    """Results across the network, filtered down to a country, partner, location or cohort."""
    network_roles = RESULTS_READS

    template_name = 'partners/staff/results_overview.html'
    page_size = 50

    def dispatch(self, request, *args, **kwargs):
        from scholr.features import online_results_enabled
        if not online_results_enabled():
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator

        context = super().get_context_data(**kwargs)
        params = self.request.GET
        view = params.get('view') if params.get('view') in results.VIEWS else results.PROGRAMME
        filters = {key: params.get(key, '') for key in ('country', 'organisation', 'location', 'cohort')}

        cohorts = scope.result_cohorts(self.request.user).select_related('location__organisation')
        organisations = PartnerOrganisation.objects.filter(locations__cohorts__in=cohorts).distinct().order_by('name')
        countries = sorted(set(organisations.values_list('country', flat=True)))
        if filters['country']:
            organisations = organisations.filter(country=filters['country'])
            cohorts = cohorts.filter(location__organisation__country=filters['country'])
        if filters['organisation']:
            cohorts = cohorts.filter(location__organisation__public_id=filters['organisation'])
        locations = TrainingLocation.objects.filter(cohorts__in=cohorts).distinct().order_by('name')
        if filters['location']:
            cohorts = cohorts.filter(location__public_id=filters['location'])

        courses = results.active_courses()
        page = Paginator(results.visible_memberships(self.request.user, **filters), self.page_size).get_page(
            params.get('page')
        )
        rows = results.grid(page.object_list, courses)
        query = params.copy()
        query.pop('page', None)
        query.pop('view', None)

        context.update({
            'view': view,
            'filters': filters,
            'countries': [(code, country_name(code)) for code in countries],
            'organisations': organisations,
            'locations': locations,
            'cohorts': cohorts.order_by('location__name', 'name'),
            'courses': courses,
            'rows': rows,
            'page': page,
            'query': query.urlencode(),
            'export_url': reverse('partners:results_export') + '?' + query.urlencode(),
            'export_splits': True,
            'summaries': results.course_summaries(rows, courses, view),
            'pass_mark': settings.PARTNERS_PASS_MARK,
            'online_synced_at': results.last_synced(),
            'show_cohort': True,
        })
        return context


class ResultsExportView(NetworkStaffMixin, View):
    """The results overview as a spreadsheet: same filters, same reach, optionally a sheet per partner or cohort."""
    network_roles = RESULTS_READS

    def dispatch(self, request, *args, **kwargs):
        from scholr.features import online_results_enabled
        if not online_results_enabled():
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        params = request.GET
        view = params.get('view') if params.get('view') in results.VIEWS else results.PROGRAMME
        filters = {key: params.get(key, '') for key in ('country', 'organisation', 'location', 'cohort')}
        workbook = exports.build_workbook(request.user, view, filters, split=params.get('split', exports.ONE_SHEET))
        return workbook_response(workbook, exports.filename(view))
