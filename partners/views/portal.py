"""
Training location portal: coordinators run their location, facilitators take registers
and record results, programme staff look in read-only.
"""

import logging
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, ListView, TemplateView, UpdateView

from partners.exceptions import PartnersError, SyncConflict
from partners.forms import (
    CohortForm,
    MemberInviteForm,
    ParticipantForm,
    ParticipantImportForm,
    ResultEntryForm,
    SessionForm,
)
from partners.forms.portal import validate_import_row
from partners.mixins import CohortMixin, LocationMixin, ServiceErrorMixin
from partners.models import (
    Cohort,
    CohortMembership,
    CohortSession,
    CourseResult,
    LocationMembership,
    Participant,
    ProgrammeCourse,
    SessionAttendance,
    SyncOperation,
)
from partners.services import access, accounts, licences, results, roster, scope, sync
from partners.utils import enqueue
from scholr.features import online_results_enabled

logger = logging.getLogger(__name__)

ACTIVATION_SESSION_KEY = 'partners_activation_link'
IMPORT_SESSION_KEY = 'partners_import_rows'


def dashboard_url(location):
    return reverse('partners:location_dashboard', kwargs={'location_id': location.public_id})


def cohort_url(cohort):
    return reverse('partners:cohort_detail', kwargs={
        'location_id': cohort.location.public_id, 'cohort_id': cohort.public_id,
    })


def participant_url(participant):
    return reverse('partners:participant_detail', kwargs={
        'location_id': participant.home_location.public_id, 'participant_id': participant.public_id,
    })


def remember_activation_link(request, user, label):
    """Keep a freshly generated set-password link for one page view, then forget it."""
    request.session[ACTIVATION_SESSION_KEY] = {
        'url': request.build_absolute_uri(accounts.activation_path(user)),
        'label': label,
    }


def pop_activation_link(request):
    return request.session.pop(ACTIVATION_SESSION_KEY, None)


# ==================== HOME AND DASHBOARD ====================

class PortalHomeView(LoginRequiredMixin, TemplateView):
    template_name = 'partners/portal/home.html'

    def get(self, request, *args, **kwargs):
        if scope.is_programme_staff(request.user):
            return redirect('partners:network')
        locations = list(scope.locations_for(request.user).select_related('organisation'))
        if len(locations) == 1:
            return redirect(dashboard_url(locations[0]))
        return self.render_to_response(self.get_context_data(locations=locations))


class LocationDashboardView(LocationMixin, TemplateView):
    template_name = 'partners/portal/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        location = self.location
        today = timezone.localdate()
        cohorts = scope.cohorts_for(self.request.user).filter(location=location).annotate(
            enrolled=Count('memberships', filter=Q(memberships__status=CohortMembership.Status.ENROLLED)),
            completed=Count('memberships', filter=Q(memberships__status=CohortMembership.Status.COMPLETED)),
        ).order_by('status', '-starts_on')
        upcoming = (
            CohortSession.objects.filter(
                cohort__in=scope.cohorts_for(self.request.user).filter(location=location),
                session_date__gte=today,
            )
            .exclude(status=CohortSession.Status.CANCELLED)
            .select_related('cohort', 'course', 'facilitator')
            .order_by('session_date')[:8]
        )
        licence = location.licence
        context.update({
            'cohorts': cohorts,
            'upcoming_sessions': upcoming,
            'participant_count': location.participants.count(),
            'team_count': location.memberships.filter(is_active=True).count(),
            'licence': licence,
            'licence_days': licence.days_remaining(today),
            'licence_live': licences.allows_new_cohorts(licence, today),
            'open_conflicts': sync.open_conflicts(location).count() if self.is_coordinator else 0,
        })
        return context


# ==================== PARTICIPANTS ====================

class ParticipantListView(LocationMixin, ListView):
    template_name = 'partners/portal/participant_list.html'
    context_object_name = 'participants'
    paginate_by = 50

    def get_queryset(self):
        queryset = self.location.participants.prefetch_related(
            Prefetch('cohort_memberships', queryset=CohortMembership.objects.select_related('cohort')),
        )
        query = (self.request.GET.get('q') or '').strip()
        if query:
            queryset = queryset.filter(
                Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(church_name__icontains=query)
            )
        return queryset.order_by('last_name', 'first_name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['query'] = self.request.GET.get('q', '')
        context['can_add'] = self.portal_role in (scope.COORDINATOR, scope.FACILITATOR)
        return context


class ParticipantCreateView(LocationMixin, CreateView):
    template_name = 'partners/portal/participant_form.html'
    form_class = ParticipantForm

    member_required = True

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['location'] = self.location
        return kwargs

    def target_cohort(self):
        cohort_id = self.request.GET.get('cohort') or self.request.POST.get('cohort')
        if not cohort_id:
            return None
        return scope.cohorts_for(self.request.user).filter(location=self.location, public_id=cohort_id).first()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['target_cohort'] = self.target_cohort()
        return context

    def form_valid(self, form):
        participant = form.save(commit=False)
        if not self.request.POST.get('confirm_duplicate'):
            duplicates = list(roster.possible_duplicates(
                self.location, participant.first_name, participant.last_name, participant.phone,
            ))
            if duplicates:
                return self.render_to_response(self.get_context_data(form=form, duplicates=duplicates))
        cohort = self.target_cohort()
        try:
            roster.register_participant(participant, self.request.user)
            if cohort is not None:
                roster.enrol(cohort, participant, self.request.user)
        except PartnersError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)
        messages.success(self.request, f'{participant.full_name} added.')
        return redirect(cohort_url(cohort) if cohort else participant_url(participant))


class ParticipantObjectMixin(LocationMixin):
    def get_participant(self):
        if not hasattr(self, '_participant'):
            self._participant = get_object_or_404(
                Participant.objects.select_related('home_location', 'user'),
                public_id=self.kwargs['participant_id'], home_location=self.location,
            )
        return self._participant


class ParticipantDetailView(ParticipantObjectMixin, DetailView):
    template_name = 'partners/portal/participant_detail.html'
    context_object_name = 'participant'

    def get_object(self, queryset=None):
        return self.get_participant()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        visible_cohorts = scope.cohorts_for(self.request.user)
        memberships = (
            self.object.cohort_memberships.filter(cohort__in=visible_cohorts)
            .select_related('cohort', 'access_order')
            .prefetch_related(Prefetch('results', queryset=CourseResult.objects.select_related('course')))
        )
        memberships = list(memberships)
        statuses = access.access_statuses(memberships)
        rows = []
        for membership in memberships:
            marks = membership.attendance.exclude(session__status=CohortSession.Status.CANCELLED)
            total = marks.count()
            attended = marks.filter(status__in=SessionAttendance.ATTENDED).count()
            status = statuses[membership.pk]
            rows.append({
                'membership': membership,
                'results': list(membership.results.all()),
                'attendance_rate': round(100 * attended / total) if total else None,
                'access_status': status,
                'access_label': access.ACCESS_LABELS[status],
            })
        context['membership_rows'] = rows
        return context


class ParticipantUpdateView(ParticipantObjectMixin, UpdateView):
    template_name = 'partners/portal/participant_form.html'
    form_class = ParticipantForm
    manager_required = True
    context_object_name = 'participant'

    def get_object(self, queryset=None):
        return self.get_participant()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['location'] = self.location
        return kwargs

    def form_valid(self, form):
        roster.update_participant(form.save(commit=False), self.request.user)
        messages.success(self.request, 'Participant saved.')
        return redirect(participant_url(self.object))


class ParticipantImportView(LocationMixin, FormView):
    """Two steps: upload and preview, then confirm. Parsed rows wait in the session between them."""

    template_name = 'partners/portal/participant_import.html'
    form_class = ParticipantImportForm
    manager_required = True

    def session_key(self):
        return f'{IMPORT_SESSION_KEY}:{self.location.public_id}'

    def cohorts(self):
        return self.location.cohorts.filter(status__in=[Cohort.Status.PLANNED, Cohort.Status.RUNNING])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cohorts'] = self.cohorts()
        return context

    def form_valid(self, form):
        rows = form.cleaned_data['file']
        for row in rows:
            row['errors'] = validate_import_row(row)
            row['duplicate'] = bool(
                not row['errors'] and roster.possible_duplicates(
                    self.location, row['first_name'], row['last_name'], row['phone'],
                ).exists()
            )
        self.request.session[self.session_key()] = rows
        return self.render_to_response(self.get_context_data(form=form, preview=rows, valid_count=sum(
            1 for row in rows if not row['errors']
        )))

    def post(self, request, *args, **kwargs):
        if request.POST.get('confirm') != '1':
            return super().post(request, *args, **kwargs)
        rows = request.session.pop(self.session_key(), None)
        if not rows:
            messages.error(request, 'The preview has expired. Upload the file again.')
            return redirect('partners:participant_import', location_id=self.location.public_id)
        cohort_id = request.POST.get('cohort')
        cohort = self.cohorts().filter(public_id=cohort_id).first() if _is_uuid(cohort_id) else None
        skip_duplicates = request.POST.get('skip_duplicates') == '1'
        created = skipped = 0
        try:
            for row in rows:
                if row['errors'] or (skip_duplicates and row['duplicate']):
                    skipped += 1
                    continue
                participant = Participant(
                    home_location=self.location,
                    first_name=row['first_name'][:100], last_name=row['last_name'][:100],
                    phone=row['phone'], email=row['email'], church_name=row['church_name'][:200],
                    position_title=row['position_title'][:100],
                )
                roster.register_participant(participant, request.user)
                if cohort is not None:
                    roster.enrol(cohort, participant, request.user)
                created += 1
        except PartnersError as exc:
            messages.error(request, f'Import stopped after {created} participant(s): {exc.message}')
            return redirect('partners:participant_list', location_id=self.location.public_id)
        messages.success(request, f'{created} participant(s) imported, {skipped} skipped.')
        return redirect('partners:participant_list', location_id=self.location.public_id)


# ==================== COHORTS ====================

class CohortCreateView(LocationMixin, CreateView):
    template_name = 'partners/portal/cohort_form.html'
    form_class = CohortForm
    manager_required = True

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['location'] = self.location
        return kwargs

    def form_valid(self, form):
        cohort = form.save(commit=False)
        try:
            roster.create_cohort(cohort, self.request.user, form.cleaned_data['facilitators'])
        except PartnersError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)
        messages.success(self.request, f'{cohort.name} created. Add participants and sessions next.')
        return redirect(cohort_url(cohort))


class CohortDetailView(CohortMixin, TemplateView):
    template_name = 'partners/portal/cohort_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cohort = self.cohort
        memberships = list(
            cohort.memberships.select_related('participant', 'access_order')
            .annotate(
                passed=Count('results', filter=Q(results__status=CourseResult.Status.PASSED), distinct=True),
            )
            .order_by('participant__last_name', 'participant__first_name')
        )
        statuses = access.access_statuses(memberships) if cohort.delivery == Cohort.Delivery.BLENDED else {}
        for membership in memberships:
            status = statuses.get(membership.pk)
            membership.access_state = status
            membership.access_label = access.ACCESS_LABELS.get(status, '')
            membership.display_name = membership.participant.full_name
        enrolled_ids = [membership.participant_id for membership in memberships]
        context.update({
            'memberships': memberships,
            'sessions': cohort.sessions.select_related('course', 'facilitator').annotate(
                marks=Count('attendance'),
            ),
            'courses': ProgrammeCourse.objects.filter(is_active=True),
            'available_participants': (
                self.location.participants.exclude(pk__in=enrolled_ids).order_by('last_name', 'first_name')
                if self.can_record and cohort.is_open else Participant.objects.none()
            ),
            'facilitators': cohort.facilitators.all(),
            'activation': pop_activation_link(self.request),
            'statuses': Cohort.Status,
            'course_count': ProgrammeCourse.objects.filter(is_active=True).count(),
            'remember_me_session': not self.request.session.get_expire_at_browser_close(),
        })
        return context


class CohortUpdateView(CohortMixin, UpdateView):
    template_name = 'partners/portal/cohort_form.html'
    form_class = CohortForm
    manager_required = True

    def get_object(self, queryset=None):
        return self.cohort

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['location'] = self.location
        return kwargs

    def form_valid(self, form):
        try:
            roster.update_cohort(form.save(commit=False), self.request.user, form.cleaned_data['facilitators'])
        except PartnersError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)
        messages.success(self.request, 'Cohort saved.')
        return redirect(cohort_url(self.cohort))


class CohortStatusView(CohortMixin, ServiceErrorMixin, View):
    http_method_names = ['post']
    manager_required = True

    def post(self, request, location_id, cohort_id):
        to_status = request.POST.get('status')
        try:
            roster.change_cohort_status(self.cohort, to_status, request.user)
        except PartnersError as exc:
            return self.service_error(exc, cohort_url(self.cohort))
        messages.success(request, 'Cohort status updated.')
        return redirect(cohort_url(self.cohort))


class CohortEnrolView(CohortMixin, ServiceErrorMixin, View):
    http_method_names = ['post']

    def post(self, request, location_id, cohort_id):
        ids = request.POST.getlist('participants')
        participants = self.location.participants.filter(public_id__in=[
            value for value in ids if _is_uuid(value)
        ])
        added = 0
        try:
            for participant in participants:
                _membership, created = roster.enrol(self.cohort, participant, request.user)
                added += int(created)
        except PartnersError as exc:
            return self.service_error(exc, cohort_url(self.cohort))
        messages.success(request, f'{added} participant(s) added to {self.cohort.name}.')
        return redirect(cohort_url(self.cohort))


def _is_uuid(value):
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


class MembershipActionView(CohortMixin, ServiceErrorMixin, View):
    """Withdraw a participant, or give and withdraw online access."""

    http_method_names = ['post']
    ACTIONS = {'withdraw', 'grant_access', 'withdraw_access'}

    def post(self, request, location_id, cohort_id, membership_id, action):
        if action not in self.ACTIONS:
            raise Http404
        membership = get_object_or_404(
            CohortMembership.objects.select_related('participant'), public_id=membership_id, cohort=self.cohort,
        )
        name = membership.participant.full_name
        try:
            if action == 'withdraw':
                roster.withdraw(membership, request.user)
                messages.success(request, f'{name} withdrawn from the cohort.')
            elif action == 'grant_access':
                _order, new_user = access.grant_online_access(membership, request.user)
                if new_user is not None:
                    remember_activation_link(request, new_user, f'Set-password link for {name}')
                messages.success(request, f'Online access for {name} is being set up in Moodle.')
            else:
                access.withdraw_online_access(membership, request.user)
                messages.success(request, f'Online access withdrawn for {name}.')
        except PartnersError as exc:
            return self.service_error(exc, cohort_url(self.cohort))
        return redirect(cohort_url(self.cohort))


# ==================== SESSIONS ====================

class SessionCreateView(CohortMixin, CreateView):
    template_name = 'partners/portal/session_form.html'
    form_class = SessionForm
    manager_required = True

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['cohort'] = self.cohort
        return kwargs

    def form_valid(self, form):
        try:
            roster.create_session(form.save(commit=False), self.request.user)
        except PartnersError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)
        messages.success(self.request, 'Session added.')
        return redirect(cohort_url(self.cohort))


class SessionObjectMixin(CohortMixin):
    def get_session(self):
        if not hasattr(self, '_session'):
            self._session = get_object_or_404(
                CohortSession.objects.select_related('course', 'facilitator'),
                public_id=self.kwargs['session_id'], cohort=self.cohort,
            )
        return self._session


class SessionUpdateView(SessionObjectMixin, UpdateView):
    template_name = 'partners/portal/session_form.html'
    form_class = SessionForm
    manager_required = True
    context_object_name = 'session_obj'

    def get_object(self, queryset=None):
        return self.get_session()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['cohort'] = self.cohort
        return kwargs

    def form_valid(self, form):
        try:
            roster.update_session(form.save(commit=False), self.request.user)
        except PartnersError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)
        messages.success(self.request, 'Session saved.')
        return redirect(cohort_url(self.cohort))


class SessionDeleteView(SessionObjectMixin, ServiceErrorMixin, View):
    http_method_names = ['post']
    manager_required = True

    def post(self, request, location_id, cohort_id, session_id):
        try:
            roster.delete_session(self.get_session(), request.user)
        except PartnersError as exc:
            return self.service_error(exc, cohort_url(self.cohort))
        messages.success(request, 'Session deleted.')
        return redirect(cohort_url(self.cohort))


class SessionRegisterView(SessionObjectMixin, ServiceErrorMixin, TemplateView):
    """The online register, also used to type in a paper register after class."""

    template_name = 'partners/portal/register.html'

    def memberships(self):
        return list(
            self.cohort.memberships.exclude(status=CohortMembership.Status.WITHDRAWN)
            .select_related('participant').order_by('participant__last_name', 'participant__first_name')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        session = self.get_session()
        marks = {record.membership_id: record for record in session.attendance.all()}
        context.update({
            'session_obj': session,
            'rows': [(membership, marks.get(membership.pk)) for membership in self.memberships()],
            'statuses': SessionAttendance.Status.choices,
            'editable': self.can_record and session.status != CohortSession.Status.CANCELLED
            and licences.allows_recording(self.location.licence, self.cohort),
        })
        return context

    def post(self, request, location_id, cohort_id, session_id):
        if not self.can_record:
            raise PermissionDenied
        session = self.get_session()
        valid = set(SessionAttendance.Status.values)
        marks = {}
        for membership in self.memberships():
            status = request.POST.get(f'mark_{membership.public_id}')
            if status in valid:
                marks[membership] = status
        if not marks:
            messages.error(request, 'Mark at least one participant before saving.')
            return redirect(request.path)
        try:
            roster.save_register(session, marks, request.user)
        except PartnersError as exc:
            return self.service_error(exc, request.path)
        messages.success(request, f'Register saved for {len(marks)} participant(s).')
        return redirect(cohort_url(self.cohort))


class SessionRegisterPdfView(SessionObjectMixin, View):
    """A printable register for locations that take attendance on paper."""

    def get(self, request, location_id, cohort_id, session_id):
        from weasyprint import HTML

        session = self.get_session()
        memberships = (
            self.cohort.memberships.exclude(status=CohortMembership.Status.WITHDRAWN)
            .select_related('participant').order_by('participant__last_name', 'participant__first_name')
        )
        html = render_to_string('partners/print/register.html', {
            'brand': settings.SITE_BRAND,
            'location': self.location,
            'cohort': self.cohort,
            'session_obj': session,
            'memberships': memberships,
            'statuses': SessionAttendance.Status.choices,
            'generated_at': timezone.localtime(),
        }, request=request)
        pdf = HTML(string=html, base_url=request.build_absolute_uri('/')).write_pdf()
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = (
            f'inline; filename="register-{session.course.code.replace(" ", "")}-{session.session_date}.pdf"'
        )
        return response


# ==================== RESULTS ====================

class ResultsView(CohortMixin, ServiceErrorMixin, TemplateView):
    template_name = 'partners/portal/results.html'

    def get_course(self):
        code = self.request.GET.get('course') or self.request.POST.get('course')
        courses = ProgrammeCourse.objects.filter(is_active=True)
        return courses.filter(code=code).first() if code else courses.first()

    def build_form(self, course, data=None):
        memberships = list(
            self.cohort.memberships.exclude(status=CohortMembership.Status.WITHDRAWN)
            .select_related('participant').order_by('participant__last_name', 'participant__first_name')
        )
        results = {
            result.membership_id: result
            for result in CourseResult.objects.filter(membership__in=memberships, course=course)
        }
        return ResultEntryForm(data, memberships=memberships, results=results)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        course = kwargs.get('course') or self.get_course()
        form = kwargs.get('form') or (self.build_form(course) if course else None)
        rows = []
        if form is not None:
            memberships = [row[0] for row in form.rows]
            hours = roster.attended_hours_by_membership(memberships, course)
            online = results.online_results(memberships, [course]) if online_results_enabled() else {}
            for membership, result, score_name, version_name in form.rows:
                rows.append({
                    'membership': membership,
                    'result': result,
                    'score_field': form[score_name],
                    'version_field': form[version_name],
                    'attended': hours[membership.pk],
                    'online': online.get((membership.pk, course.pk)),
                })
        context.update({
            'course': course,
            'courses': ProgrammeCourse.objects.filter(is_active=True),
            'form': form,
            'rows': rows,
            'required_hours': roster.required_hours(course) if course else None,
            'pass_mark': settings.PARTNERS_PASS_MARK,
            'is_online_course': bool(course and course.online_course_id) and online_results_enabled(),
            'online_synced_at': results.last_synced(),
            'editable': self.can_record and licences.allows_recording(self.location.licence, self.cohort),
            'unsigned_count': CourseResult.objects.filter(
                membership__cohort=self.cohort, course=course, signed_off_at__isnull=True,
            ).exclude(status=CourseResult.Status.IN_PROGRESS).count() if course else 0,
        })
        return context

    def post(self, request, location_id, cohort_id):
        if not self.can_record:
            raise PermissionDenied
        course = self.get_course()
        if course is None:
            raise PermissionDenied
        back = f"{request.path}?course={course.code}"
        if request.POST.get('sign_off') == '1':
            return self.sign_off(course, back)
        form = self.build_form(course, request.POST)
        if not form.is_valid():
            messages.error(request, 'Some scores are not valid percentages. Check the highlighted rows.')
            return self.render_to_response(self.get_context_data(course=course, form=form))
        saved, conflicts = 0, []
        try:
            for membership, score, version in form.changed_rows():
                try:
                    roster.record_result(membership, course, score, request.user, base_version=version)
                    saved += 1
                except SyncConflict:
                    conflicts.append(membership.participant.full_name)
        except PartnersError as exc:
            return self.service_error(exc, back)
        if conflicts:
            messages.warning(request, (
                'Someone else changed these results while you were editing, so yours were not saved: '
                + ', '.join(conflicts) + '. Check them and save again.'
            ))
        messages.success(request, f'{saved} result(s) saved for {course.code}.')
        return redirect(back)

    def sign_off(self, course, back):
        if not self.is_coordinator:
            raise PermissionDenied
        results = CourseResult.objects.filter(
            membership__cohort=self.cohort, course=course, signed_off_at__isnull=True,
        ).exclude(status=CourseResult.Status.IN_PROGRESS)
        count = 0
        try:
            for result in results.select_related('membership__cohort__location'):
                roster.sign_off_result(result, self.request.user)
                count += 1
        except PartnersError as exc:
            return self.service_error(exc, back)
        messages.success(self.request, f'{count} result(s) signed off for {course.code}.')
        return redirect(back)


# ==================== TEAM ====================

class TeamView(LocationMixin, TemplateView):
    template_name = 'partners/portal/team.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        members = list(self.location.memberships.select_related('user').order_by('-is_active', 'role'))
        for member in members:
            member.awaiting_activation = accounts.needs_activation(member.user)
        context.update({
            'members': members,
            'can_invite': scope.can_invite(self.request.user, self.location, LocationMembership.Role.FACILITATOR),
            'activation': pop_activation_link(self.request),
        })
        return context


class MemberInviteView(LocationMixin, ServiceErrorMixin, FormView):
    template_name = 'partners/portal/member_form.html'
    form_class = MemberInviteForm

    def allowed_roles(self):
        return [
            role for role in LocationMembership.Role.values
            if scope.can_invite(self.request.user, self.location, role)
        ]

    def get(self, request, *args, **kwargs):
        if not self.allowed_roles():
            raise PermissionDenied
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if not self.allowed_roles():
            raise PermissionDenied
        return super().post(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['allowed_roles'] = self.allowed_roles()
        return kwargs

    def form_valid(self, form):
        data = form.cleaned_data
        try:
            membership, created_account = accounts.invite_member(
                self.location, self.request.user, data['first_name'], data['last_name'], data['role'],
                email=data['email'], phone=data['phone'], trained_on=data['trained_on'],
            )
        except PartnersError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)
        user = membership.user
        if created_account or accounts.needs_activation(user):
            remember_activation_link(self.request, user, f'Set-password link for {user.get_full_name()}')
            if user.email:
                from partners.tasks import send_activation_email
                user_id, location_id = user.pk, self.location.pk
                transaction.on_commit(lambda: enqueue(send_activation_email, user_id, location_id))
        messages.success(self.request, f'{user.get_full_name()} added as {membership.get_role_display()}.')
        return redirect('partners:team', location_id=self.location.public_id)


class MemberActionView(LocationMixin, ServiceErrorMixin, View):
    http_method_names = ['post']
    ACTIONS = {'deactivate', 'link'}

    def post(self, request, location_id, pk, action):
        if action not in self.ACTIONS:
            raise Http404
        membership = get_object_or_404(LocationMembership.objects.select_related('user'), pk=pk,
                                       location=self.location)
        team_url = reverse('partners:team', kwargs={'location_id': self.location.public_id})
        if not scope.can_invite(request.user, self.location, membership.role):
            raise PermissionDenied
        if action == 'deactivate':
            try:
                accounts.deactivate_member(membership, request.user)
            except PartnersError as exc:
                return self.service_error(exc, team_url)
            messages.success(request, f'{membership.user.get_full_name()} removed from this location.')
        else:
            if not accounts.needs_activation(membership.user):
                messages.info(request, 'This person has already set a password.')
            else:
                remember_activation_link(
                    request, membership.user, f'Set-password link for {membership.user.get_full_name()}',
                )
        return redirect(team_url)


# ==================== CONFLICTS ====================

class ConflictListView(LocationMixin, ListView):
    template_name = 'partners/portal/conflicts.html'
    context_object_name = 'conflicts'
    manager_required = True

    def get_queryset(self):
        return sync.open_conflicts(self.location).order_by('received_at')


class ConflictResolveView(LocationMixin, ServiceErrorMixin, View):
    http_method_names = ['post']
    manager_required = True

    def post(self, request, location_id, op_id):
        operation = get_object_or_404(SyncOperation, op_id=op_id, location=self.location)
        back = reverse('partners:conflicts', kwargs={'location_id': self.location.public_id})
        try:
            sync.resolve_conflict(operation, request.user, request.POST.get('choice') == 'incoming')
        except PartnersError as exc:
            return self.service_error(exc, back)
        messages.success(request, 'Conflict resolved.')
        return redirect(back)


class CohortResultsOverviewView(CohortMixin, TemplateView):
    """Every participant across all twelve courses, as programme results or online quiz results."""

    template_name = 'partners/portal/results_overview.html'

    def dispatch(self, request, *args, **kwargs):
        if not online_results_enabled():
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        view = self.request.GET.get('view')
        view = view if view in results.VIEWS else results.PROGRAMME
        courses = results.active_courses()
        memberships = (
            self.cohort.memberships.exclude(status=CohortMembership.Status.WITHDRAWN)
            .select_related('participant').order_by('participant__last_name', 'participant__first_name')
        )
        rows = results.grid(memberships, courses)
        context.update({
            'view': view,
            'courses': courses,
            'rows': rows,
            'summaries': results.course_summaries(rows, courses, view),
            'pass_mark': settings.PARTNERS_PASS_MARK,
            'online_synced_at': results.last_synced(),
            'show_cohort': False,
            'export_url': reverse('partners:cohort_results_export', kwargs={
                'location_id': self.location.public_id, 'cohort_id': self.cohort.public_id,
            }) + '?',
        })
        return context


class CohortResultsExportView(CohortMixin, View):
    """One cohort's results as a spreadsheet, for anyone who can open the cohort."""

    def dispatch(self, request, *args, **kwargs):
        if not online_results_enabled():
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        from partners.services import exports
        from partners.utils import workbook_response
        view = request.GET.get('view') if request.GET.get('view') in results.VIEWS else results.PROGRAMME
        workbook = exports.build_workbook(request.user, view, {'cohort': self.cohort.public_id})
        return workbook_response(workbook, exports.filename(view))
