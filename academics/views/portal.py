"""
Parent portal views and parent account administration.

Two sections:
  1. Portal views (ParentPortalMixin) - parents and admin preview
  2. Admin views (ManagementMixin) - create/manage parent portal accounts
"""

import secrets
import string

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View

from ..mixins import ManagementMixin
from ..models.attendance import AttendanceSummary
from ..models.intervention import LearnerIntervention, ProgressNote
from ..models.learner import Guardian, Learner, LearnerEnrollment, LearnerGuardian
from ..models.marks import FinalMark, SubjectEnrollment
from ..models.reports import ReportCard
from ..models.school import AcademicYear, Term
from ..utils.caps_marks import get_symbol_and_description

User = get_user_model()


# ==================== PARENT PORTAL MIXIN ====================

class ParentPortalMixin(LoginRequiredMixin):
    """
    Base mixin for all parent portal views.
    Allows parents, admins, and principals (admin can preview for support).
    Individual views call _get_child() for per-learner access checks.
    """

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role not in {"parent", "admin", "principal"}:
            raise PermissionDenied(
                "The parent portal is accessible to parents only. "
                "Use the staff interface for school management."
            )
        return super().dispatch(request, *args, **kwargs)

    def _get_guardian(self):
        """Return the Guardian linked to the current user, or None."""
        try:
            return self.request.user.guardian_profile
        except Exception:
            return None

    def _get_child(self, learner_pk):
        """
        Return a Learner the current user is authorised to view.
        Admin and principal bypass the guardian check (portal preview).
        """
        learner = get_object_or_404(Learner, pk=learner_pk, is_active=True)
        user = self.request.user

        if user.role in {"admin", "principal"}:
            return learner

        guardian = self._get_guardian()
        if not guardian:
            raise PermissionDenied(
                "Your account is not linked to a guardian profile. "
                "Contact the school office."
            )

        if not LearnerGuardian.objects.filter(
            guardian=guardian, learner=learner
        ).exists():
            raise PermissionDenied(
                "You are not authorised to view this learner's information."
            )

        return learner

    def _get_children(self):
        """All active learners linked to the current parent's guardian."""
        user = self.request.user
        if user.role in {"admin", "principal"}:
            return Learner.objects.filter(is_active=True).order_by(
                "last_name", "first_name"
            )[:20]

        guardian = self._get_guardian()
        if not guardian:
            return Learner.objects.none()

        return Learner.objects.filter(
            guardian_relationships__guardian=guardian,
            is_active=True,
        ).distinct().order_by("last_name", "first_name")


# ==================== PORTAL VIEWS ====================

class PortalDashboardView(ParentPortalMixin, View):
    """
    Parent portal home. Shows cards for each linked child with
    quick-glance stats: attendance %, marks status.
    """

    template_name = "academics/portal/dashboard.html"

    def get(self, request):
        current_year = AcademicYear.get_current()
        current_term = Term.get_current()
        children = self._get_children()

        child_cards = []
        for child in children:
            enrollment = LearnerEnrollment.objects.filter(
                learner=child,
                academic_year=current_year,
                is_active=True,
            ).select_related("grade", "stream").first() if current_year else None

            attendance = AttendanceSummary.objects.filter(
                learner=child,
                academic_year=current_year,
                term_number=current_term.term_number,
            ).first() if current_year and current_term else None

            # Count released report cards
            report_count = ReportCard.objects.filter(
                learner=child,
                academic_year=current_year,
                is_released=True,
            ).count() if current_year else 0

            child_cards.append({
                "learner": child,
                "enrollment": enrollment,
                "attendance": attendance,
                "report_count": report_count,
            })

        return render(request, self.template_name, {
            "child_cards": child_cards,
            "current_year": current_year,
            "current_term": current_term,
        })


class PortalChildOverviewView(ParentPortalMixin, View):
    """
    Single child overview page.
    Shows attendance summary, marks overview, shared notes, and report cards.
    """

    template_name = "academics/portal/child_overview.html"

    















    def get(self, request, learner_pk):
        learner = self._get_child(learner_pk)
        current_year = AcademicYear.get_current()
        current_term = Term.get_current()

        enrollment = None
        if current_year:
            enrollment = LearnerEnrollment.objects.filter(
                learner=learner,
                academic_year=current_year,
                is_active=True,
            ).select_related("grade", "stream").first()

        # Attendance for current term
        attendance = None
        if current_year and current_term:
            attendance = AttendanceSummary.objects.filter(
                learner=learner,
                academic_year=current_year,
                term_number=current_term.term_number,
            ).first()

        # Published final marks — parents only see published results
        marks_summary = self._get_marks_summary(learner, current_year)

        # Released report cards
        report_cards = (
            ReportCard.objects.filter(
                learner=learner,
                is_released=True,
            ).order_by("-academic_year__year", "term_number")
            if current_year else []
        )

        # Progress notes shared with parent
        notes = (
            ProgressNote.objects.filter(
                learner=learner,
                academic_year=current_year,
                share_with_parent=True,
            ).select_related("created_by", "subject_offering__subject")
            .order_by("-created_at")[:5]
            if current_year else []
        )

        # Active interventions (parents benefit from knowing what support is in place)
        interventions = (
            LearnerIntervention.objects.filter(
                learner=learner,
                academic_year=current_year,
                status=LearnerIntervention.STATUS_ACTIVE,
            ).select_related("program")
            if current_year else []
        )

        passing = sum(1 for m in marks_summary if m.get("passed"))
        failing = sum(1 for m in marks_summary if m.get("passed") is False)

        # ---- Online learning (bridge only) ----
        # Parents can see their child's active online courses and progress.
        # This is intentionally read-only — no enroll/withdraw actions in the portal.
        from scholr.features import bridge_enabled as _bridge_enabled
        _bridge = _bridge_enabled()
        portal_online_enrollments = []
        if _bridge and current_year:
            from ..models.lms_link import OnlineEnrollment
            portal_online_enrollments = list(
                OnlineEnrollment.objects.filter(
                    learner=learner,
                    academic_year=current_year,
                    status=OnlineEnrollment.STATUS_ACTIVE,
                ).select_related('course__category', 'subject')
            )

        return render(request, self.template_name, {
            "learner":         learner,
            "enrollment":      enrollment,
            "attendance":      attendance,
            "marks_summary":   marks_summary,
            "report_cards":    report_cards,
            "notes":           notes,
            "interventions":   interventions,
            "current_year":    current_year,
            "current_term":    current_term,
            "subjects_passing": passing,
            "subjects_failing": failing,
            # Bridge
            "bridge_enabled":             _bridge,
            "portal_online_enrollments":  portal_online_enrollments,
        })


    def _get_marks_summary(self, learner, academic_year):
        """
        Build a marks summary for the portal overview.
        Only includes subjects where FinalMark.is_published = True.
        """
        if not academic_year:
            return []

        results = []
        sub_enrollments = SubjectEnrollment.objects.filter(
            learner=learner,
            academic_year=academic_year,
            is_active=True,
        ).select_related("subject_offering__subject")

        for se in sub_enrollments:
            try:
                fm = se.final_mark
            except FinalMark.DoesNotExist:
                fm = None

            if not fm or not fm.is_published:
                continue

            symbol, description = get_symbol_and_description(fm.final_percentage)
            offering = se.subject_offering

            results.append({
                "subject_name": offering.subject.name,
                "percentage": fm.final_percentage,
                "symbol": symbol,
                "description": description,
                "passed": fm.final_percentage is not None and float(fm.final_percentage) >= offering.pass_mark,
            })

        return sorted(results, key=lambda x: x["subject_name"])


class PortalChildMarksView(ParentPortalMixin, View):
    """Full marks breakdown per subject per term for one child."""

    template_name = "academics/portal/child_marks.html"

    def get(self, request, learner_pk):
        learner = self._get_child(learner_pk)
        current_year = AcademicYear.get_current()

        subject_rows = []
        if current_year:
            sub_enrollments = SubjectEnrollment.objects.filter(
                learner=learner,
                academic_year=current_year,
                is_active=True,
            ).select_related("subject_offering__subject", "subject_offering")

            for se in sub_enrollments:
                offering = se.subject_offering

                # Term marks (always visible)
                from ..models.marks import TermMark
                term_marks = {
                    tm.term_number: tm
                    for tm in TermMark.objects.filter(subject_enrollment=se)
                }

                # Final mark (only if published)
                try:
                    fm = se.final_mark
                    if not fm.is_published:
                        fm = None
                except FinalMark.DoesNotExist:
                    fm = None

                symbol, description = (
                    get_symbol_and_description(fm.final_percentage)
                    if fm and fm.final_percentage is not None
                    else (None, "")
                )

                subject_rows.append({
                    "subject_name": offering.subject.name,
                    "sba_weighting": offering.sba_weighting,
                    "exam_weighting": offering.exam_weighting,
                    "pass_mark": offering.pass_mark,
                    "t1": term_marks.get(1),
                    "t2": term_marks.get(2),
                    "t3": term_marks.get(3),
                    "t4": term_marks.get(4),
                    "final_mark": fm,
                    "symbol": symbol,
                    "description": description,
                })

        return render(request, self.template_name, {
            "learner": learner,
            "subject_rows": sorted(subject_rows, key=lambda x: x["subject_name"]),
            "current_year": current_year,
            "has_published": any(r["final_mark"] for r in subject_rows),
        })


class PortalChildAttendanceView(ParentPortalMixin, View):
    """Attendance records for one child."""

    template_name = "academics/portal/child_attendance.html"

    def get(self, request, learner_pk):
        learner = self._get_child(learner_pk)
        current_year = AcademicYear.get_current()

        # Summaries per term
        summaries = AttendanceSummary.objects.filter(
            learner=learner,
            academic_year=current_year,
        ).order_by("term_number") if current_year else []

        # Absence records for current year
        from ..models.attendance import AttendanceRecord
        absence_records = AttendanceRecord.objects.filter(
            learner=learner,
            session__academic_year=current_year,
            status__in=[
                AttendanceRecord.STATUS_ABSENT,
                AttendanceRecord.STATUS_LATE,
                AttendanceRecord.STATUS_EXCUSED,
            ],
        ).select_related("session", "reason").order_by("-session__date")[:30]

        return render(request, self.template_name, {
            "learner": learner,
            "summaries": summaries,
            "absence_records": absence_records,
            "current_year": current_year,
        })


class PortalChildReportsView(ParentPortalMixin, View):
    """Released report cards for one child."""

    template_name = "academics/portal/child_reports.html"

    def get(self, request, learner_pk):
        learner = self._get_child(learner_pk)

        report_cards = ReportCard.objects.filter(
            learner=learner,
            is_released=True,
        ).order_by("-academic_year__year", "term_number")

        return render(request, self.template_name, {
            "learner": learner,
            "report_cards": report_cards,
        })


class PortalReportDownloadView(ParentPortalMixin, View):
    """Download a released report card PDF."""

    def get(self, request, learner_pk, report_pk):
        learner = self._get_child(learner_pk)

        report_card = get_object_or_404(
            ReportCard,
            pk=report_pk,
            learner=learner,
            is_released=True,
            is_generated=True,
        )

        if not report_card.pdf_file:
            raise Http404("Report card PDF not available.")

        try:
            return FileResponse(
                report_card.pdf_file.open("rb"),
                content_type="application/pdf",
                as_attachment=True,
                filename=(
                    f"Report_T{report_card.term_number}_"
                    f"{report_card.academic_year.year}_"
                    f"{learner.admission_number}.pdf"
                ),
            )
        except (OSError, FileNotFoundError):
            raise Http404("Report card file not found.")


# ==================== PARENT ACCOUNT ADMINISTRATION ====================

class ParentAccountsView(ManagementMixin, View):
    """
    Admin view: list guardians with and without portal accounts.
    Shows which guardians have been granted portal access.
    """

    template_name = "academics/parent_admin/accounts.html"

    def get(self, request):
        guardians_with_account = Guardian.objects.filter(
            user__isnull=False, is_active=True
        ).select_related("user").order_by("last_name", "first_name")

        guardians_without_account = Guardian.objects.filter(
            user__isnull=True, is_active=True
        ).order_by("last_name", "first_name")

        # Count learners per guardian for context
        def with_learner_count(qs):
            return [
                {
                    "guardian": g,
                    "learner_count": g.learner_relationships.count(),
                }
                for g in qs
            ]

        return render(request, self.template_name, {
            "with_account": with_learner_count(guardians_with_account),
            "without_account": with_learner_count(guardians_without_account),
        })


class CreateParentAccountView(ManagementMixin, View):
    """
    Create a portal User(role=PARENT) account for a guardian.
    Shows credentials once on screen for admin to communicate to parent.
    """

    template_name = "academics/parent_admin/create_account.html"

    def _suggest_username(self, guardian):
        base = (
            f"{guardian.first_name.lower().replace(' ', '')}"
            f".{guardian.last_name.lower().replace(' ', '')}"
        )
        username = base[:30]
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base[:27]}{counter}"
            counter += 1
        return username

    def _generate_password(self):
        """Generate a readable temporary password: e.g. River#8472"""
        words = [
            "River", "Stone", "Cloud", "Flame", "Eagle",
            "Tiger", "Cedar", "Maple", "Delta", "Solar",
        ]
        import random
        word = random.choice(words)
        digits = "".join(random.choices(string.digits, k=4))
        return f"{word}#{digits}"

    def get(self, request, guardian_pk):
        guardian = get_object_or_404(Guardian, pk=guardian_pk)

        if guardian.user:
            messages.info(
                request,
                f"{guardian.get_full_name()} already has a portal account "
                f"(username: {guardian.user.username}).",
            )
            return redirect("academics:parent_accounts")

        return render(request, self.template_name, {
            "guardian": guardian,
            "suggested_username": self._suggest_username(guardian),
            "suggested_password": self._generate_password(),
            "learners": guardian.get_learners(),
        })

    def post(self, request, guardian_pk):
        guardian = get_object_or_404(Guardian, pk=guardian_pk)

        if guardian.user:
            messages.error(request, "This guardian already has a portal account.")
            return redirect("academics:parent_accounts")

        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "").strip()
        email = request.POST.get("email", "").strip() or guardian.email or ""

        if not username:
            messages.error(request, "Username is required.")
            return redirect(request.path)

        if User.objects.filter(username=username).exists():
            messages.error(
                request,
                f"Username '{username}' is already taken. Try a different one.",
            )
            return redirect(request.path)

        if not password:
            password = self._generate_password()

        with transaction.atomic():
            user = User(
                username=username,
                first_name=guardian.first_name,
                last_name=guardian.last_name,
                phone_number=guardian.phone_primary,
                email=email,
                role="parent",
                is_active=True,
                is_verified=True,
            )
            user.set_password(password)
            user.save()

            # Create a UserProfile to prevent attribute errors elsewhere
            from accounts.models import UserProfile
            UserProfile.objects.get_or_create(
                user=user,
                defaults={"country": "South Africa", "region": ""},
            )

            guardian.user = user
            guardian.save(update_fields=["user"])

        return render(request, self.template_name, {
            "guardian": guardian,
            "account_created": True,
            "username": username,
            "password": password,
            "learners": guardian.get_learners(),
        })


class RevokeParentAccountView(ManagementMixin, View):
    """
    Deactivate a parent's portal account.
    The User record is deactivated (not deleted) - can be re-enabled later.
    """

    def post(self, request, guardian_pk):
        guardian = get_object_or_404(Guardian, pk=guardian_pk, user__isnull=False)

        user = guardian.user
        user.is_active = False
        user.save(update_fields=["is_active"])

        messages.success(
            request,
            f"Portal access for {guardian.get_full_name()} has been deactivated.",
        )
        return redirect("academics:parent_accounts")