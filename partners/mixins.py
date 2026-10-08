"""
Access control for partners views.

Staff areas raise 403 for anyone who is not programme staff. Portal objects are looked up
inside the user's scope, so an object outside it returns 404 and its existence is not
revealed.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect

from partners.services import scope


class ProgrammeStaffRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not scope.is_programme_staff(request.user):
            raise PermissionDenied('Programme staff only.')
        return super().dispatch(request, *args, **kwargs)


class LocationMixin(LoginRequiredMixin):
    """
    Resolves self.location from the URL and self.portal_role for the current user.
    Set manager_required = True for views that change location-wide data, and
    member_required = True for views only working team members may use (not staff viewers).
    """

    manager_required = False
    member_required = False

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.location = get_object_or_404(
            scope.locations_for(request.user).select_related('organisation', 'licence'),
            public_id=kwargs['location_id'],
        )
        self.portal_role = scope.role_at(request.user, self.location)
        if self.manager_required and self.portal_role != scope.COORDINATOR:
            raise PermissionDenied('Only the location coordinator can do that.')
        if self.member_required and self.portal_role not in (scope.COORDINATOR, scope.FACILITATOR):
            raise PermissionDenied('Only members of this location can do that.')
        return super().dispatch(request, *args, **kwargs)

    @property
    def is_coordinator(self):
        return self.portal_role == scope.COORDINATOR

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'location': self.location,
            'portal_role': self.portal_role,
            'is_coordinator': self.is_coordinator,
            'is_staff_viewer': self.portal_role == scope.STAFF,
            'can_view_contacts': self.portal_role in (scope.STAFF, scope.COORDINATOR),
        })
        return context


class CohortMixin(LocationMixin):
    """Resolves self.cohort inside the location and inside the user's cohort scope."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.cohort = get_object_or_404(
            scope.cohorts_for(request.user).select_related('location__organisation', 'location__licence'),
            public_id=kwargs['cohort_id'],
            location__public_id=kwargs['location_id'],
        )
        return super().dispatch(request, *args, **kwargs)

    @property
    def can_record(self):
        return scope.can_record_for_cohort(self.request.user, self.cohort)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({'cohort': self.cohort, 'can_record': self.can_record})
        return context


class ServiceErrorMixin:
    """Turns a PartnersError from a service into a message and a redirect, never a 500."""

    def service_error(self, exc, redirect_to):
        messages.error(self.request, exc.message)
        return redirect(redirect_to)
