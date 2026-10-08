import logging

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import FormView, TemplateView

from partners.forms import EnquiryForm
from partners.services import pipeline
from partners.utils import client_ip, enqueue, rate_limited

logger = logging.getLogger(__name__)

ENQUIRY_LIMIT = 5
ENQUIRY_WINDOW = 3600


class EnquiryView(FormView):
    """Public 'become a training location' form, linked from the Get METP page."""

    template_name = 'partners/public/enquiry.html'
    form_class = EnquiryForm
    success_url = reverse_lazy('partners:enquiry_thanks')

    def post(self, request, *args, **kwargs):
        if rate_limited(f'enquiry:{client_ip(request)}', ENQUIRY_LIMIT, ENQUIRY_WINDOW):
            messages.error(request, 'Too many enquiries from this connection. Please try again in an hour.')
            return redirect('partners:enquire')
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        from partners.tasks import notify_staff_of_enquiry

        partnership, created = pipeline.intake_enquiry(form.cleaned_data)
        partnership_id = partnership.pk
        transaction.on_commit(lambda: enqueue(notify_staff_of_enquiry, partnership_id, created))
        logger.info('Website enquiry stored for partnership %s (new=%s)', partnership_id, created)
        return super().form_valid(form)


class EnquiryThanksView(TemplateView):
    template_name = 'partners/public/enquiry_thanks.html'


class ActivateAccountView(auth_views.PasswordResetConfirmView):
    """One-time link for new coordinators, facilitators and online students to set a password."""

    template_name = 'partners/public/activate.html'
    success_url = reverse_lazy('partners:activate_done')
    post_reset_login = False


class ActivateDoneView(TemplateView):
    template_name = 'partners/public/activate_done.html'
