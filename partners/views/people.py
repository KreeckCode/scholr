"""System administrators managing network staff: accounts, roles and reach."""
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import urlencode
from django.views import View
from django.views.generic import FormView, ListView, TemplateView

from accounts.models import RoleChange, User
from accounts.roles import RoleChangeError, change_role
from partners.exceptions import PartnersError
from partners.forms import RoleChangeForm, StaffMemberForm
from partners.mixins import SystemAdminRequiredMixin
from partners.models import PartnerOrganisation, country_name
from partners.services import accounts, people, reach, scope

from .portal import pop_activation_link, remember_activation_link

ROLE_LABELS = dict(User.ROLE_CHOICES)

# What a system administrator should know before moving someone into each role
ROLE_CONSEQUENCES = {
    User.HUB_ADMIN: 'Sees and manages the partners in the countries you assign, plus any partners you add.',
    User.AMBASSADOR: 'Sees only the partners you assign. Any countries they covered are removed.',
    User.LEADER: 'Sees every partner and changes nothing. Any countries and partners they covered are removed.',
    User.PROGRAMME_STAFF: 'Manages every partner. Any countries and partners they covered are removed.',
    User.ADMIN: 'Manages every partner and all staff, including roles. Any countries and partners are removed.',
    User.DEAN: 'Verifies students. Any countries and partners they covered are removed.',
}


def staff_detail_url(user):
    return reverse('partners:staff_detail', kwargs={'pk': user.pk})


class StaffMixin(SystemAdminRequiredMixin):
    """Resolves self.member, refusing anyone outside the staff roles these screens manage."""

    def dispatch(self, request, *args, **kwargs):
        if 'pk' in kwargs:
            self.member = get_object_or_404(people.manageable_staff(), pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)


class StaffListView(SystemAdminRequiredMixin, ListView):
    template_name = 'partners/staff/people/list.html'
    context_object_name = 'members'
    paginate_by = 25

    def get_queryset(self):
        members = people.manageable_staff().prefetch_related('network_assignments').order_by(
            'first_name', 'last_name', 'username',
        )
        role = self.request.GET.get('role', '')
        if role in User.ASSIGNABLE_STAFF_ROLES:
            members = members.filter(role=role)
        query = self.request.GET.get('q', '').strip()
        if query:
            members = members.filter(
                Q(first_name__icontains=query) | Q(last_name__icontains=query)
                | Q(email__icontains=query) | Q(username__icontains=query)
            )
        return members

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        for member in context['members']:
            member.reach_summary = reach.summary(member)
            member.awaiting_activation = accounts.needs_activation(member)
        context.update({
            'roles': sorted(
                ((role, ROLE_LABELS[role]) for role in User.ASSIGNABLE_STAFF_ROLES), key=lambda pair: pair[1],
            ),
            'role': self.request.GET.get('role', ''),
            'query': self.request.GET.get('q', ''),
        })
        return context


class StaffCreateView(SystemAdminRequiredMixin, FormView):
    template_name = 'partners/staff/people/form.html'
    form_class = StaffMemberForm

    def form_valid(self, form):
        data = form.cleaned_data
        try:
            member = people.add_staff_member(
                self.request.user, data['first_name'], data['last_name'], data['role'],
                email=data['email'], phone=data['phone'],
            )
        except PartnersError as error:
            form.add_error(None, str(error))
            return self.form_invalid(form)
        remember_activation_link(self.request, member, f'Set-password link for {member.get_full_name()}')
        messages.success(self.request, f'{member.get_full_name()} was added as {member.get_role_display()}.')
        return redirect(staff_detail_url(member))


class StaffDetailView(StaffMixin, TemplateView):
    template_name = 'partners/staff/people/detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        member = self.member
        rows = list(member.network_assignments.select_related('organisation').order_by('country'))
        countries_with_partners = dict(_partner_counts_by_country())
        context.update({
            'member': member,
            'is_self': member.pk == self.request.user.pk,
            'uses_countries': member.role == User.HUB_ADMIN,
            'uses_partners': member.role in User.NETWORK_SCOPED_ROLES,
            'countries': [(row.country, country_name(row.country)) for row in rows if row.country],
            'assigned_partners': [row.organisation for row in rows if row.organisation_id],
            'country_choices': _country_choices(countries_with_partners),
            'chosen_countries': {row.country for row in rows if row.country},
            'reach_summary': reach.summary(member),
            'history': RoleChange.objects.filter(user=member).select_related('changed_by')[:20],
            'role_form': RoleChangeForm(user=member),
            'consequences': {role: ROLE_CONSEQUENCES.get(role, '') for role in User.ASSIGNABLE_STAFF_ROLES},
            'awaiting_activation': accounts.needs_activation(member),
            'activation': pop_activation_link(self.request),
        })
        return context


class StaffRoleView(StaffMixin, View):
    def post(self, request, pk):
        form = RoleChangeForm(request.POST, user=self.member)
        if not form.is_valid():
            messages.error(request, 'Choose the new role.')
            return redirect(staff_detail_url(self.member))
        try:
            change = change_role(self.member, form.cleaned_data['new_role'], request.user,
                                 note=form.cleaned_data['note'])
        except RoleChangeError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, f'{self.member.get_full_name()} is now {change.get_to_role_display()}.')
        return redirect(staff_detail_url(self.member))


class StaffCountriesView(StaffMixin, View):
    def post(self, request, pk):
        try:
            added, removed = reach.set_countries(self.member, request.POST.getlist('countries'), request.user)
        except PartnersError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, _change_sentence('country', 'countries', added, removed))
        return redirect(staff_detail_url(self.member))


class StaffPartnersView(StaffMixin, TemplateView):
    """Pick partners for an ambassador or hub administrator, filtered by country, region or name."""

    template_name = 'partners/staff/people/partners.html'

    def filtered(self, params):
        partners = PartnerOrganisation.objects.order_by('country', 'name')
        country = params.get('country', '')
        region = params.get('region', '').strip()
        query = params.get('q', '').strip()
        if country:
            partners = partners.filter(country=country)
        if region:
            partners = partners.filter(region__icontains=region)
        if query:
            partners = partners.filter(name__icontains=query)
        return partners

    def get(self, request, *args, **kwargs):
        if self.member.role not in User.NETWORK_SCOPED_ROLES:
            messages.error(request, 'Only hub administrators and ambassadors are assigned partners.')
            return redirect(staff_detail_url(self.member))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = self.request.GET
        partners = list(self.filtered(params)[:500])
        assigned = set(self.member.network_assignments.filter(organisation__isnull=False)
                       .values_list('organisation_id', flat=True))
        hub_countries = set(scope.assigned_countries(self.member))
        context.update({
            'member': self.member,
            'partners': partners,
            'assigned': assigned,
            'hub_countries': hub_countries,
            'country': params.get('country', ''),
            'region': params.get('region', ''),
            'query': params.get('q', ''),
            'country_choices': [(code, country_name(code)) for code, _count in _partner_counts_by_country()],
            'truncated': len(partners) == 500,
        })
        return context

    def post(self, request, pk):
        shown = PartnerOrganisation.objects.filter(public_id__in=request.POST.getlist('shown'))
        selected = PartnerOrganisation.objects.filter(public_id__in=request.POST.getlist('partners'))
        try:
            added, removed = reach.update_shown_organisations(self.member, shown, selected, request.user)
        except PartnersError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, _change_sentence('partner', 'partners', added, removed))
        # I rebuild the return address from the filters rather than trusting a posted URL
        filters = {key: request.POST.get(key, '') for key in ('country', 'region', 'q') if request.POST.get(key)}
        url = reverse('partners:staff_partners', kwargs={'pk': self.member.pk})
        return redirect(f'{url}?{urlencode(filters)}' if filters else url)


class StaffActivationLinkView(StaffMixin, View):
    """A fresh one-time link for someone who has not set their password yet."""

    def post(self, request, pk):
        if accounts.needs_activation(self.member):
            remember_activation_link(request, self.member, f'Set-password link for {self.member.get_full_name()}')
        else:
            messages.info(request, 'They have already set a password. They can use "Forgot password" if needed.')
        return redirect(staff_detail_url(self.member))


class OrganisationCoverageView(SystemAdminRequiredMixin, View):
    """Add or remove an ambassador or hub administrator from a partner, from the partner's page."""

    def post(self, request, org_id):
        organisation = get_object_or_404(PartnerOrganisation, public_id=org_id)
        member = get_object_or_404(
            people.manageable_staff().filter(role__in=User.NETWORK_SCOPED_ROLES), pk=request.POST.get('user'),
        )
        try:
            if request.POST.get('action') == 'remove':
                reach.remove_organisation(member, organisation, request.user)
                messages.success(request, f'{member.get_full_name()} no longer covers {organisation.name}.')
            else:
                reach.add_organisation(member, organisation, request.user)
                messages.success(request, f'{member.get_full_name()} now covers {organisation.name}.')
        except PartnersError as error:
            messages.error(request, str(error))
        return redirect('partners:organisation_detail', org_id=organisation.public_id)


def _partner_counts_by_country():
    from django.db.models import Count
    return (
        PartnerOrganisation.objects.values_list('country').annotate(total=Count('pk')).order_by('country')
    )


def _country_choices(counts):
    """Countries with partners first, then every other country, for covering a country before partners arrive."""
    from partners.countries import COUNTRIES
    with_partners = [(code, name, counts[code]) for code, name in COUNTRIES if code in counts]
    others = [(code, name, 0) for code, name in COUNTRIES if code not in counts]
    return with_partners + others


def _change_sentence(singular, plural, added, removed):
    if not added and not removed:
        return 'Nothing changed.'
    parts = []
    if added:
        parts.append(f'{len(added)} {singular if len(added) == 1 else plural} added')
    if removed:
        parts.append(f'{len(removed)} {singular if len(removed) == 1 else plural} removed')
    return ', '.join(parts).capitalize() + '.'
