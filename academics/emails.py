"""
Email notification service for the academics module.

All outbound email goes through the _send() helper, which:
  - Renders both a plain-text and an HTML template
  - Logs success/failure at INFO/ERROR level
  - Never raises — callers receive a boolean result

Backend selection is handled entirely in settings.py:
  DEBUG=True  -> console (stdout) — no SMTP credentials needed
  DEBUG=False -> SMTP via EMAIL_HOST/EMAIL_HOST_USER/EMAIL_HOST_PASSWORD
"""
import logging
import os
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from academics.models.lms_link import OnlineProgressAlert

logger = logging.getLogger(__name__)

# ---- Configurable thresholds (override via .env without code changes) ----
STALL_DAYS       = int(os.getenv('PROGRESS_STALL_DAYS',         '7'))
NOT_STARTED_DAYS = int(os.getenv('PROGRESS_NOT_STARTED_DAYS',   '3'))
ALERT_COOLDOWN   = int(os.getenv('PROGRESS_ALERT_COOLDOWN_DAYS', '7'))


# ==================== INTERNAL SEND HELPER ====================

def _send(subject, to_emails, template_name, context, from_email=None):
    """
    Render a template pair (txt + html), build a multipart email, and send it.
    Templates live at templates/academics/emails/<template_name>.(txt|html).

    Returns True on success, False on any failure.
    Never raises — errors are always logged and swallowed.
    """
    from_email = from_email or settings.DEFAULT_FROM_EMAIL

    try:
        text_body = render_to_string(
            f'academics/emails/{template_name}.txt', context
        ).strip()
        html_body = render_to_string(
            f'academics/emails/{template_name}.html', context
        )
    except Exception as exc:
        logger.error(f'Email template render failed ({template_name}): {exc}')
        return False

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=from_email,
        to=to_emails,
    )
    msg.attach_alternative(html_body, 'text/html')

    try:
        msg.send(fail_silently=False)
        # Log the backend name for easy debugging in dev
        backend_label = settings.EMAIL_BACKEND.split('.')[-1]
        logger.info(
            f'Email sent | subject="{subject}" | to={to_emails} '
            f'| backend={backend_label}'
        )
        return True
    except Exception as exc:
        logger.error(
            f'Email send failed | subject="{subject}" | to={to_emails} '
            f'| error={exc}'
        )
        return False


# ==================== PROGRESS STALL EMAIL SERVICE ====================

class ProgressStallEmailService:
    """
    Detects stalled online course progress and notifies relevant staff.

    Two alert types:
      not_started — learner enrolled but hasn't begun after NOT_STARTED_DAYS
      stall       — learner started but hasn't moved in STALL_DAYS

    A cooldown of ALERT_COOLDOWN days prevents re-alerting the same
    learner+course pair. Each sent alert is recorded in OnlineProgressAlert.
    """

    # ---- Recipient resolution ----

    def _get_recipient_emails(self, learner, current_year):
        """
        Return email addresses of staff to notify for this learner.
        Targets homeroom teachers who have an email address on record.
        Returns an empty list when no recipients are found.
        """
        if not current_year:
            return []

        from academics.models.marks import SubjectEnrollment
        from academics.models.structure import GradeStream

        # Find streams this learner is enrolled in for the current year
        stream_ids = (
            SubjectEnrollment.objects.filter(
                learner=learner,
                academic_year=current_year,
                is_active=True,
            ).values_list('stream_id', flat=True).distinct()
        )

        # Collect unique email addresses from homeroom teachers
        teacher_emails = set(
            GradeStream.objects.filter(
                pk__in=stream_ids,
                homeroom_teacher__isnull=False,
            ).exclude(
                homeroom_teacher__email=''
            ).filter(
                homeroom_teacher__email__isnull=False,
            ).values_list('homeroom_teacher__email', flat=True)
        )

        return list(teacher_emails)

    # ---- Cooldown enforcement ----

    def _already_alerted(self, learner, course):
        """
        Return True if an alert was recorded for this learner+course
        within the ALERT_COOLDOWN window.
        """
        from academics.models.lms_link import OnlineProgressAlert
        cutoff = timezone.now() - timedelta(days=ALERT_COOLDOWN)
        return OnlineProgressAlert.objects.filter(
            learner=learner,
            course=course,
            sent_at__gte=cutoff,
        ).exists()

    def _record_alert(self, enrollment, alert_type, days, recipient_emails):
        """Persist the alert record so the cooldown check can find it."""
        from academics.models.lms_link import OnlineProgressAlert
        OnlineProgressAlert.objects.create(
            learner=enrollment.learner,
            course=enrollment.course,
            alert_type=alert_type,
            days_stalled=days,
            progress_at_send=enrollment.progress_percentage,
            sent_to_emails=','.join(recipient_emails),
        )

    # ---- Detection ----

    def _find_stalled(self):
        """
        Yield (enrollment, alert_type, days_stalled) tuples.

        Criteria:
          not_started: 0% progress AND enrolled_at > NOT_STARTED_DAYS ago
          stall: 1–99% AND progress_last_synced > STALL_DAYS ago
        """
        from academics.models.lms_link import OnlineEnrollment

        now               = timezone.now()
        stall_cutoff      = now - timedelta(days=STALL_DAYS)
        not_started_cutoff = now - timedelta(days=NOT_STARTED_DAYS)

        active = OnlineEnrollment.objects.filter(
            status=OnlineEnrollment.STATUS_ACTIVE,
        ).select_related('learner', 'course__category')

        for enrollment in active:
            # Skip if already alerted recently
            if self._already_alerted(enrollment.learner, enrollment.course):
                continue

            progress    = float(enrollment.progress_percentage or 0)
            last_synced = enrollment.progress_last_synced
            enrolled_at = getattr(enrollment, 'enrolled_at', None)

            # Not started check
            if (
                progress == 0
                and enrolled_at
                and enrolled_at < not_started_cutoff
            ):
                days = (now - enrolled_at).days
                yield enrollment, OnlineProgressAlert.ALERT_NOT_STARTED, days
                continue

            # Stalled check
            if (
                0 < progress < 100
                and last_synced
                and last_synced < stall_cutoff
            ):
                days = (now - last_synced).days
                yield enrollment, OnlineProgressAlert.ALERT_STALL, days

    # ---- Main entry point ----

    def detect_and_send(self):
        """
        Find all stalled enrollments, build context, and dispatch emails.
        Called by the Celery task.

        Returns a summary dict for logging:
          {stalled, not_started, emails_sent, skipped_no_recipients}
        """
        from academics.models.lms_link import OnlineProgressAlert
        from academics.models.school import AcademicYear, School

        current_year = AcademicYear.get_current()

        school_name = 'Scholr'
        try:
            school = School.get_current()
            if school:
                school_name = school.name
        except Exception:
            pass

        results = {
            'stalled':               0,
            'not_started':           0,
            'emails_sent':           0,
            'skipped_no_recipients': 0,
        }

        for enrollment, alert_type, days in self._find_stalled():
            learner = enrollment.learner
            course  = enrollment.course

            recipients = self._get_recipient_emails(learner, current_year)
            if not recipients:
                results['skipped_no_recipients'] += 1
                logger.warning(
                    f'No recipients for {alert_type} alert: '
                    f'{learner} in {course.title}'
                )
                continue

            # Build absolute learner URL for the email link
            try:
                learner_path = reverse(
                    'academics:learner_analysis',
                    args=[learner.pk],
                )
                learner_url = f'{settings.SITE_URL}{learner_path}'
            except Exception:
                learner_url = settings.SITE_URL

            context = {
                'learner':     learner,
                'course':      course,
                'enrollment':  enrollment,
                'alert_type':  alert_type,
                'days':        days,
                'progress':    float(enrollment.progress_percentage or 0),
                'school_name': school_name,
                'learner_url': learner_url,
                'threshold': (
                    NOT_STARTED_DAYS
                    if alert_type == OnlineProgressAlert.ALERT_NOT_STARTED
                    else STALL_DAYS
                ),
            }

            if alert_type == OnlineProgressAlert.ALERT_NOT_STARTED:
                subject = (
                    f'[{school_name}] Online Learning — '
                    f'{learner.get_full_name()} has not started '
                    f'"{course.title}"'
                )
            else:
                subject = (
                    f'[{school_name}] Online Learning — '
                    f'{learner.get_full_name()}\'s progress stalled in '
                    f'"{course.title}"'
                )

            sent = _send(
                subject=subject,
                to_emails=recipients,
                template_name='progress_stall',
                context=context,
            )

            if sent:
                self._record_alert(enrollment, alert_type, days, recipients)
                results['emails_sent'] += 1

            if alert_type == OnlineProgressAlert.ALERT_NOT_STARTED:
                results['not_started'] += 1
            else:
                results['stalled'] += 1

        return results