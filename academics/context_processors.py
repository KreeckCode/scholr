from .models.school import School


def school_context(request):
    """
    Inject the school profile into every academics template context.
    I'm caching the result on the request object to avoid a DB hit
    on every template tag call within a single request.
    """
    if not hasattr(request, "_cached_school"):
        request._cached_school = School.get_current()
    return {"school": request._cached_school}