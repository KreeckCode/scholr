"""
ApplicationService — business logic for creating and submitting applications.

Responsibilities:
  - Generate reference numbers
  - Calculate priority tier (sibling, staff child, general)
  - Run duplicate detection
  - Transition application status
  - Fire Celery email tasks
  - Record status history

This service is called from views. It never imports view layer code.
All database writes happen inside a single atomic transaction.
"""
import logging

from django.db import transaction
from django.utils import timezone

from ..models.application import (
    Application,
    ApplicationStatusHistory,
    EnrollmentConfig,
)

logger = logging.getLogger(__name__)


class ApplicationService:

    def create_application(
        self,
        template,
        form_data: dict,
        ip_address: str = '',
        user_agent: str = '',
    ) -> Application:
        """
        Create and submit a new Application from validated form data.
        Runs duplicate detection, sets initial status, and fires email tasks.

        Raises ValueError if the enrollment window is closed.
        All DB writes are wrapped in a transaction — if anything fails,
        nothing is committed.
        """
        if not template.is_open:
            raise ValueError('This enrollment window is no longer accepting applications.')

        with transaction.atomic():
            ref = Application.generate_reference_number(template.enrollment_year)

            priority = self._calculate_priority(template, form_data)

            application = Application.objects.create(
                reference_number=ref,
                template=template,
                enrollment_year=template.enrollment_year,
                target_grade=template.entry_grade,
                status=Application.STATUS_SUBMITTED,
                priority=priority,
                submitted_at=timezone.now(),
                ip_address=ip_address or None,
                user_agent=user_agent[:500],
                **self._clean_form_data(form_data),
            )

            ApplicationStatusHistory.objects.create(
                application=application,
                from_status='',
                to_status=Application.STATUS_SUBMITTED,
                notes='Application submitted via online form.',
            )

            # Duplicate detection: flag but do not auto-reject
            duplicate = self._check_for_duplicates(application)
            if duplicate:
                application.suspected_duplicate_of = duplicate
                application.transition_status(
                    Application.STATUS_POSSIBLE_DUPLICATE,
                    notes=(
                        f'System flagged possible duplicate of '
                        f'{duplicate.reference_number} — manual review required.'
                    ),
                )
                application.save(update_fields=['status', 'suspected_duplicate_of'])

            # Capacity check: if template is at capacity and waitlist enabled
            elif template.is_at_capacity and template.waitlist_enabled:
                position = self._next_waitlist_position(template)
                application.waitlist_position = position
                application.transition_status(
                    Application.STATUS_WAITLISTED,
                    notes=(
                        f'Application placed on waitlist at position {position}. '
                        f'Template capacity ({template.enrollment_capacity}) reached.'
                    ),
                )
                application.save(update_fields=['status', 'waitlist_position'])

            # Check if any required documents are expected
            elif template.document_requirements.filter(is_required=True).exists():
                application.transition_status(
                    Application.STATUS_DOCUMENTS_PENDING,
                    notes='Awaiting required document uploads.',
                )
                application.save(update_fields=['status'])

        # Fire emails outside the transaction so a mail failure
        # doesn't roll back the application record.
        self._fire_email_tasks(application)

        logger.info(
            f'Application created: {application.reference_number} '
            f'— {application.get_full_name()} '
            f'(status: {application.status})'
        )
        return application

    # ---- Private helpers ----

    def _clean_form_data(self, form_data: dict) -> dict:
        """
        Remove form-only fields (declarations, submit button, etc.)
        that don't map to Application model fields.
        """
        non_model_fields = {
            'declaration_accurate',
            'declaration_popia',
            'csrfmiddlewaretoken',
        }
        return {k: v for k, v in form_data.items() if k not in non_model_fields}

    def _calculate_priority(self, template, form_data: dict) -> int:
        """
        Assign a priority tier based on template rules and family relationships.
        Falls back to PRIORITY_GENERAL when checks are inconclusive.
        """
        if template.sibling_priority_enabled:
            sibling_exists = self._check_sibling_enrolled(
                guardian_email=form_data.get('guardian_email', ''),
                guardian_phone=form_data.get('guardian_phone', ''),
            )
            if sibling_exists:
                logger.debug(f'Sibling priority assigned for {form_data.get("guardian_email")}')
                return Application.PRIORITY_SIBLING

        return Application.PRIORITY_GENERAL

    def _check_sibling_enrolled(self, guardian_email: str, guardian_phone: str) -> bool:
        """
        Return True if any currently enrolled learner shares a guardian
        with the same email or phone number.
        Requires academics to be installed and have data.
        """
        try:
            from academics.models.learner import Guardian, LearnerGuardian
            from academics.models.school import AcademicYear

            current_year = AcademicYear.get_current()
            if not current_year:
                return False

            from django.db.models import Q
            return Guardian.objects.filter(
                Q(email=guardian_email) | Q(phone_primary=guardian_phone)
            ).filter(
                learner_relationships__learner__enrollments__academic_year=current_year,
                learner_relationships__learner__enrollments__is_active=True,
            ).exists()
        except Exception:
            return False

    def _check_for_duplicates(self, application) -> 'Application | None':
        """
        Check for possible duplicate applications.

        Checks (in order):
        1. Exact ID match within the same enrollment year and not terminal status
        2. Exact ID match against existing Learners in Academics

        Returns the suspected duplicate Application, or None if clean.
        Note: existing-Learner matches are logged but do not return a duplicate
        Application (the admin notification includes this warning separately).
        """
        # Check 1: another Application with the same ID this year
        existing_app = (
            Application.objects
            .filter(
                id_number=application.id_number,
                id_type=application.id_type,
                enrollment_year=application.enrollment_year,
            )
            .exclude(pk=application.pk)
            .exclude(status__in=[
                Application.STATUS_REJECTED,
                Application.STATUS_WITHDRAWN,
            ])
            .first()
        )
        if existing_app:
            return existing_app

        # Check 2: ID already exists as a Learner record
        try:
            from academics.models.learner import Learner
            if Learner.objects.filter(id_number=application.id_number).exists():
                logger.warning(
                    f'Application {application.reference_number}: '
                    f'ID number {application.id_number} already exists in Learners. '
                    f'This learner may already be enrolled.'
                )
        except Exception:
            pass

        return None

    def _next_waitlist_position(self, template) -> int:
        """Return the next available waitlist position for this template."""
        last = (
            Application.objects
            .filter(template=template, status=Application.STATUS_WAITLISTED)
            .order_by('-waitlist_position')
            .values_list('waitlist_position', flat=True)
            .first()
        )
        return (last or 0) + 1

    def _fire_email_tasks(self, application):
        """Dispatch Celery tasks for guardian confirmation and admin notification."""
        from ..tasks import (
            send_application_received_email_task,
            send_admin_notification_email_task,
        )
        send_application_received_email_task.delay(application.pk)
        send_admin_notification_email_task.delay(application.pk)