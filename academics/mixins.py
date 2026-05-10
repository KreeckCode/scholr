from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied


# I'm defining role sets once here so view classes don't scatter string comparisons
SENIOR_ROLES = {"admin", "principal"}
MANAGEMENT_ROLES = {"admin", "principal", "admin_clerk"}
STAFF_ROLES = {"admin", "principal", "hod", "teacher", "admin_clerk"}
ALL_SCHOOL_ROLES = {"admin", "principal", "hod", "teacher", "admin_clerk", "parent", "learner"}


class AcademicsAccessMixin(LoginRequiredMixin):
    """
    Base mixin for every academics view.
    Subclasses set allowed_roles to tighten access.
    The LMS 'admin' role inherits full access as the system operator.
    """

    allowed_roles = STAFF_ROLES  # Default: any internal school staff

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role not in self.allowed_roles:
            raise PermissionDenied(
                f"Your role ({request.user.get_role_display()}) "
                f"does not have permission to access this page."
            )
        return super().dispatch(request, *args, **kwargs)


class SeniorManagementMixin(AcademicsAccessMixin):
    """
    Principal and Admin only.
    For: deactivating learners, year setup, promotion decisions,
    staff management, school profile changes.
    """
    allowed_roles = SENIOR_ROLES


class ManagementMixin(AcademicsAccessMixin):
    """
    Principal, Admin, and Admin Clerk.
    For: learner registration, editing records, document uploads,
    enrollment management.
    """
    allowed_roles = MANAGEMENT_ROLES

class HODOrSeniorMixin(AcademicsAccessMixin):
    """
    HODs, Principals, and Admins.
    For: assessment task management (HODs scoped to their department in the view).
    """
    allowed_roles = {"admin", "principal", "hod"}

class StaffMixin(AcademicsAccessMixin):
    """
    All teaching and administrative staff.
    For: read access, registers, marks capture.
    Note: get_queryset() in each view further scopes to the user's classes.
    """
    allowed_roles = STAFF_ROLES