"""
Permission decorators for enrollment admin views.

All admin views require staff status. Specific permissions (like approve/reject)
can be gated further with custom permissions added to user groups.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from scholr.features import enrollment_enabled


def enrollment_admin_required(view_func):
    """
    Decorator requiring both login and staff status.
    Checks enrollment_enabled() first — redirects to home if disabled.
    """
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not enrollment_enabled():
            return redirect('main:home')
        
        if not request.user.is_staff:
            raise PermissionDenied('You do not have permission to access the enrollment admin.')
        
        return view_func(request, *args, **kwargs)
    
    return wrapper