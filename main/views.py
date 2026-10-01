
import logging

from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.shortcuts import redirect, render
from django.views import View

logger = logging.getLogger(__name__)


# ==================== HOME ====================

class HomeView(View):
    def get(self, request):
        if request.user.is_authenticated:
            return redirect('accounts:dashboard')
        return render(request, settings.SITE_BRAND['home_template'])


# ==================== ABOUT ====================

class AboutView(View):
    """About Scholr — mission, history, team."""

    def get(self, request):
        team = [
            {
                'name': 'David Kreeck',
                'role': 'Founder & CEO',
                'bio': 'Passionate about making quality education accessible across Africa.',
                'image': 'https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?w=400&h=400&fit=crop',
            },
        ]
        return render(request, 'main/about.html', {'team': team})


# ==================== FEATURES ====================

class FeaturesView(View):
    """Detailed breakdown of platform features."""

    def get(self, request):
        features = [
            {
                'title': 'Online Learning Management',
                'description': 'Full-featured LMS with Moodle integration, course catalog, shopping cart, and payment processing via Paystack.',
                'icon': 'lms',
                'color': 'blue',
                'items': [
                    'Course catalog with grade and subject filtering',
                    'Shopping cart and Paystack checkout',
                    'Automatic Moodle enrollment on payment',
                    'SSO into Moodle from your account',
                    'Progress tracking and completion certificates',
                ],
            },
            {
                'title': 'School Management System',
                'description': 'Complete SMS built for South African CAPS curriculum — learners, marks, attendance, reports, and more.',
                'icon': 'school',
                'color': 'indigo',
                'items': [
                    'Learner registration with SA ID validation',
                    'Term marks and final mark calculation',
                    'Attendance registers and summary reports',
                    'WeasyPrint PDF report cards',
                    'LURITS export compliance',
                ],
            },
            {
                'title': 'LMS Bridge — Online Learning for Schools',
                'description': 'Connect school learners to Moodle online courses. Track progress, detect stalls, and notify staff automatically.',
                'icon': 'bridge',
                'color': 'purple',
                'items': [
                    'Per-learner Moodle account creation',
                    'Bulk account creation for whole grades',
                    'Progress sync and stall detection',
                    'Email alerts to homeroom teachers',
                    'Parent portal access to course progress',
                ],
            },
            {
                'title': 'Analytics & Reporting',
                'description': 'School-wide dashboards with Chart.js, Excel exports, LURITS compliance, and per-learner at-risk analysis.',
                'icon': 'analytics',
                'color': 'teal',
                'items': [
                    'Chart.js enrollment, attendance, and risk charts',
                    'openpyxl Excel export (5 sheets)',
                    'At-risk learner detection with risk scoring',
                    'Intervention tracking and progress notes',
                    'Academic and enrollment reports',
                ],
            },
        ]
        return render(request, 'main/features.html', {'features': features})


# ==================== INSTITUTIONS ====================

class InstitutionsView(View):
    """Partner institutions, video testimonials, and case studies."""

    def get(self, request):
        video_testimonials = [
            {
                'name': 'Nairobi Theological Seminary',
                'country': 'Kenya',
                'contact': 'Dr. James Odhiambo',
                'role': 'Academic Dean',
                'youtube_id': 'a3ICNMQW7Ok',
                'quote': 'Scholr transformed how we deliver theological education across our three campuses.',
                'learners': 340,
            },
            {
                'name': 'Lagos Christian University',
                'country': 'Nigeria',
                'contact': 'Rev. Grace Adeyemi',
                'role': 'Director of E-Learning',
                'youtube_id': 'dQw4w9WgXcQ',
                'quote': 'The online learning bridge means our pastors can study from any village with a phone.',
                'learners': 820,
            },
            {
                'name': 'Cape Town Bible College',
                'country': 'South Africa',
                'contact': 'Dr. Peter van der Berg',
                'role': 'Principal',
                'youtube_id': 'a3ICNMQW7Ok',
                'quote': 'The school management tools saved our admin team 20 hours a week on mark capture.',
                'learners': 210,
            },
        ]

        written_testimonials = [
            {
                'name': 'Pastor John Makau',
                'role': 'Senior Pastor',
                'institution': 'Grace Community Church, Mombasa',
                'quote': 'This training has completely transformed my preaching. I now understand Scripture at a deeper level.',
                'image': 'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=400&h=400&fit=crop',
                'rating': 5,
            },
            {
                'name': 'Grace Acheampong',
                'role': 'Church Administrator',
                'institution': 'New Life Church, Accra',
                'quote': 'As an administrator, these courses gave me the biblical and practical knowledge I needed.',
                'image': 'https://images.unsplash.com/photo-1580489944761-15a19d654956?w=400&h=400&fit=crop',
                'rating': 5,
            },
            {
                'name': 'Elder David Khumalo',
                'role': 'Church Elder',
                'institution': 'Redeemer Baptist, Johannesburg',
                'quote': 'African context in the teaching made all the difference. Real examples from our continent.',
                'image': 'https://images.unsplash.com/photo-1531427186611-ecfd6d936c79?w=400&h=400&fit=crop',
                'rating': 5,
            },
            {
                'name': 'Rev. Amina Hassan',
                'role': 'Youth Pastor',
                'institution': 'Dayspring Church, Dar es Salaam',
                'quote': 'Our young leaders are being equipped in ways we could never achieve with in-person sessions alone.',
                'image': 'https://images.unsplash.com/photo-1438761681033-6461ffad8d80?w=400&h=400&fit=crop',
                'rating': 5,
            },
        ]

        stats = [
            {'label': 'Institutions', 'value': '47+', 'detail': 'across 12 countries'},
            {'label': 'Learners', 'value': '12,000+', 'detail': 'actively enrolled'},
            {'label': 'Courses Completed', 'value': '38,000+', 'detail': 'this academic year'},
            {'label': 'Countries', 'value': '12', 'detail': 'across Africa'},
        ]

        return render(request, 'main/institutions.html', {
            'video_testimonials': video_testimonials,
            'written_testimonials': written_testimonials,
            'stats': stats,
        })


# ==================== PRICING ====================

class PricingView(View):
    """Pricing tiers for institutions."""

    def get(self, request):
        tiers = [
            {
                'name': 'Starter',
                'price': 'R 1,500',
                'period': 'per month',
                'description': 'For small churches and individual ministries.',
                'learners': 'Up to 50 learners',
                'color': 'blue',
                'features': [
                    'Online LMS with Moodle',
                    'Up to 50 enrolled learners',
                    'Course catalog and payments',
                    'Email support',
                    'Progress tracking',
                ],
                'cta': 'Get started',
                'highlight': False,
            },
            {
                'name': 'Institution',
                'price': 'R 4,500',
                'period': 'per month',
                'description': 'For Bible colleges and theological seminaries.',
                'learners': 'Up to 500 learners',
                'color': 'indigo',
                'features': [
                    'Everything in Starter',
                    'School Management System',
                    'Attendance and mark capture',
                    'PDF report cards',
                    'Parent portal',
                    'Analytics dashboard',
                    'LMS Bridge',
                    'Priority support',
                ],
                'cta': 'Book a demo',
                'highlight': True,
            },
            {
                'name': 'Enterprise',
                'price': 'Custom',
                'period': 'contact us',
                'description': 'For denominations and multi-campus institutions.',
                'learners': 'Unlimited learners',
                'color': 'purple',
                'features': [
                    'Everything in Institution',
                    'Multi-campus support',
                    'Custom branding',
                    'Dedicated account manager',
                    'On-site training',
                    'Custom integrations',
                    'SLA guarantee',
                ],
                'cta': 'Contact sales',
                'highlight': False,
            },
        ]
        return render(request, 'main/pricing.html', {'tiers': tiers})


# ==================== BOOK A DEMO ====================

class BookDemoView(View):
    """Demo booking form — sends email to admin and confirmation to requester."""

    template_name = 'main/demo.html'

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        first_name     = request.POST.get('first_name', '').strip()
        last_name      = request.POST.get('last_name', '').strip()
        email          = request.POST.get('email', '').strip()
        phone          = request.POST.get('phone', '').strip()
        institution    = request.POST.get('institution', '').strip()
        country        = request.POST.get('country', '').strip()
        learner_count  = request.POST.get('learner_count', '').strip()
        current_lms    = request.POST.get('current_lms', '').strip()
        preferred_time = request.POST.get('preferred_time', '').strip()
        message        = request.POST.get('message', '').strip()

        if not all([first_name, last_name, email, institution]):
            messages.error(request, 'Please fill in all required fields.')
            return render(request, self.template_name)

        # Email to admin
        admin_body = f"""
New Demo Request — Scholr

Name:         {first_name} {last_name}
Email:        {email}
Phone:        {phone}
Institution:  {institution}
Country:      {country}
Learners:     {learner_count}
Current LMS:  {current_lms}
Preferred:    {preferred_time}

Message:
{message}
        """.strip()

        try:
            send_mail(
                subject=f'[Scholr] Demo Request — {institution}',
                message=admin_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[settings.DEFAULT_FROM_EMAIL],
                fail_silently=False,
            )
            # Confirmation to requester
            send_mail(
                subject='Your Scholr demo request is confirmed',
                message=(
                    f"Hi {first_name},\n\n"
                    f"Thanks for requesting a Scholr demo. "
                    f"Our team will reach out within 1 business day to schedule your session.\n\n"
                    f"In the meantime, explore our platform at {settings.SITE_URL}\n\n"
                    f"The Scholr Team"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=True,
            )
            messages.success(
                request,
                "Demo request received. We'll be in touch within 1 business day.",
            )
        except Exception as exc:
            logger.error(f'Demo request email failed: {exc}')
            messages.error(
                request,
                'Something went wrong sending your request. '
                'Please email us directly at support@scholr.co.za',
            )

        return redirect('main:demo')


# ==================== CONTACT ====================

class ContactView(View):
    """Contact form — sends email to support."""

    template_name = 'main/contact.html'

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        name    = request.POST.get('name', '').strip()
        email   = request.POST.get('email', '').strip()
        subject = request.POST.get('subject', 'General Enquiry').strip()
        body    = request.POST.get('message', '').strip()

        if not all([name, email, body]):
            messages.error(request, 'Please fill in all required fields.')
            return render(request, self.template_name)

        try:
            send_mail(
                subject=f'[Scholr Contact] {subject} — {name}',
                message=f"From: {name} <{email}>\n\n{body}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[settings.DEFAULT_FROM_EMAIL],
                fail_silently=False,
            )
            messages.success(request, "Message sent. We'll respond within 2 business days.")
        except Exception as exc:
            logger.error(f'Contact form email failed: {exc}')
            messages.error(request, 'Failed to send. Email us at support@scholr.co.za')

        return redirect('main:contact')


# ==================== STATIC PAGES ====================

class FaqView(View):
    def get(self, request):
        faqs = [
            {
                'q': 'What is Scholr?',
                'a': 'Scholr is a learning management platform built for African educational institutions, combining an online LMS (powered by Moodle), a school management system, and an LMS bridge for connecting school learners to online courses.',
            },
            {
                'q': 'Do I need to install Moodle separately?',
                'a': 'No. Scholr includes a pre-configured Moodle integration. Your Moodle instance is managed alongside your Scholr installation — users, courses, and enrollments sync automatically.',
            },
            {
                'q': 'Can I accept payments in Rands?',
                'a': 'Yes. Scholr integrates with Paystack for ZAR payments and Stripe for international cards. Free courses auto-enroll verified learners with no payment required.',
            },
            {
                'q': 'Is Scholr CAPS-compliant?',
                'a': 'Yes. The school management system is built for South African CAPS curriculum — term marks, SBA weightings, exam weightings, CAPS achievement levels (1–7), and LURITS export.',
            },
            {
                'q': 'What devices can learners use?',
                'a': 'Scholr is fully responsive and works on desktop, tablet, and mobile. Moodle has native iOS and Android apps. We design for low-bandwidth environments common in rural Africa.',
            },
            {
                'q': 'Can parents see their children\'s progress?',
                'a': 'Yes. The parent portal allows guardians to view their child\'s attendance, published marks, released report cards, progress notes from teachers, and online course progress.',
            },
            {
                'q': 'How does the school-to-Moodle bridge work?',
                'a': 'The LMS Bridge creates Moodle user accounts for school learners, enrolls them in online courses, syncs their progress nightly, and emails homeroom teachers when progress stalls.',
            },
            {
                'q': 'Is there a free trial?',
                'a': 'Yes — book a demo and we\'ll set up a 30-day trial environment for your institution at no cost. No credit card required.',
            },
        ]
        return render(request, 'main/faq.html', {'faqs': faqs})


class PrivacyView(View):
    def get(self, request):
        return render(request, 'main/privacy.html')


class TermsView(View):
    def get(self, request):
        return render(request, 'main/terms.html')


# ==================== ERROR HANDLERS ====================

def handler404(request, exception):
    return render(request, 'errors/404.html', status=404)


def handler500(request):
    return render(request, 'errors/500.html', status=500)