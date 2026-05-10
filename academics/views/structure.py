from django.contrib.messages.views import SuccessMessageMixin
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView

from ..mixins import ManagementMixin, SeniorManagementMixin
from ..models.school import AcademicYear
from ..models.structure import Grade, GradeStream


class GradeListView(ManagementMixin, ListView):
    """
    All active grades with their streams for a selected academic year.
    Defaults to the current year. Allows year switching via query param.
    """

    model = Grade
    template_name = "academics/structure/grade_list.html"
    context_object_name = "grades"

    def get_queryset(self):
        return Grade.objects.filter(is_active=True).order_by("grade_number")

    def _get_selected_year(self):
        year_id = self.request.GET.get("year")
        if year_id:
            return AcademicYear.objects.filter(pk=year_id).first()
        return AcademicYear.get_current()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        selected_year = self._get_selected_year()
        ctx["selected_year"] = selected_year
        ctx["all_years"] = AcademicYear.objects.order_by("-year")

        if selected_year:
            streams = GradeStream.objects.filter(
                academic_year=selected_year, is_active=True
            ).select_related("homeroom_teacher", "grade").order_by("name")

            streams_by_grade = {}
            for stream in streams:
                streams_by_grade.setdefault(stream.grade_id, []).append(stream)
        else:
            streams_by_grade = {}

        # I'm building a flat list so templates can iterate without variable
        # dict key lookups, which Django templates cannot do
        ctx["grade_data"] = [
            {"grade": grade, "streams": streams_by_grade.get(grade.pk, [])}
            for grade in self.get_queryset()
        ]
        ctx["can_manage"] = self.request.user.role in {"admin", "principal"}
        return ctx


class GradeStreamCreateView(SeniorManagementMixin, SuccessMessageMixin, CreateView):
    """
    Create a stream for a grade in a given year.
    Only Principal and Admin - not Admin Clerks.
    """

    model = GradeStream
    fields = ["grade", "academic_year", "name", "homeroom_teacher", "room_number", "capacity"]
    template_name = "academics/structure/stream_form.html"
    success_url = reverse_lazy("academics:grade_list")
    success_message = "Class created."

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = "Create New Class"
        ctx["submit_label"] = "Create Class"
        return ctx


class GradeStreamUpdateView(SeniorManagementMixin, SuccessMessageMixin, UpdateView):
    """Edit an existing stream - teacher assignment, room, capacity."""

    model = GradeStream
    fields = ["homeroom_teacher", "room_number", "capacity", "is_active"]
    template_name = "academics/structure/stream_form.html"
    success_url = reverse_lazy("academics:grade_list")
    success_message = "Class updated."

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = f"Edit: {self.object}"
        ctx["submit_label"] = "Save Changes"
        return ctx