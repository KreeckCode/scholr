import itertools
from datetime import date, timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.utils import timezone

from accounts.models import Product, User
from partners.models import (
    Cohort,
    CohortMembership,
    CohortSession,
    Licence,
    LocationMembership,
    Participant,
    PartnerContact,
    PartnerOrganisation,
    Partnership,
    ProgrammeCourse,
    TrainingLocation,
)

_counter = itertools.count(1)


def unique():
    return next(_counter)


def make_user(role, **extra):
    number = unique()
    defaults = {
        'username': f'{role}{number}',
        'first_name': role.title(),
        'last_name': f'Person{number}',
        'email': f'{role}{number}@example.org',
        'phone_number': f'+2771{number:07d}',
        'role': role,
    }
    defaults.update(extra)
    user = User(**defaults)
    user.set_password('a-strong-test-password')
    user.save()
    return user


def make_staff(**extra):
    return make_user(User.PROGRAMME_STAFF, **extra)


def make_organisation(name=None, country='ZA', **extra):
    organisation = PartnerOrganisation.objects.create(
        name=name or f'Grace Church {unique()}', country=country, kind=PartnerOrganisation.Kind.CHURCH, **extra,
    )
    Partnership.objects.create(organisation=organisation)
    PartnerContact.objects.create(
        organisation=organisation, name='Main Contact', email=f'contact{unique()}@example.org', is_primary=True,
    )
    return organisation


def make_licence(organisation, status=Licence.Status.ACTIVE, starts_on=None, ends_on=None):
    today = timezone.localdate()
    licence = Licence(
        organisation=organisation,
        number=f'METP-{organisation.country}-{today.year}-{unique():04d}',
        status=status,
        starts_on=starts_on or today - timedelta(days=30),
        ends_on=ends_on or today + timedelta(days=335),
        signed_on=today - timedelta(days=31),
    )
    licence.agreement_file.save('agreement.pdf', ContentFile(b'%PDF-1.4 test'), save=False)
    licence.save()
    return licence


def make_location(organisation=None, licence=None, **extra):
    organisation = organisation or make_organisation()
    licence = (
        licence or organisation.licences.filter(status=Licence.Status.ACTIVE).first()
        or make_licence(organisation)
    )
    defaults = {'name': f'Location {unique()}', 'country': organisation.country, 'region': 'Limpopo',
                'status': TrainingLocation.Status.ACTIVE}
    defaults.update(extra)
    return TrainingLocation.objects.create(organisation=organisation, licence=licence, **defaults)


def add_member(location, role=LocationMembership.Role.COORDINATOR, user=None, trained_on=None):
    user = user or make_user(User.PARTNER)
    LocationMembership.objects.create(
        user=user, location=location, role=role, trained_on=trained_on or date(2025, 1, 15),
    )
    return user


def seed_courses(count=12):
    courses = []
    for sequence in range(1, count + 1):
        course, _created = ProgrammeCourse.objects.get_or_create(
            code=f'METP {100 + sequence}',
            defaults={'title': f'Course {sequence}', 'sequence': sequence, 'classroom_hours': Decimal('12.0')},
        )
        courses.append(course)
    return courses


def make_cohort(location, facilitators=(), status=Cohort.Status.RUNNING, delivery=Cohort.Delivery.CLASSROOM, **extra):
    cohort = Cohort.objects.create(
        location=location, name=f'Cohort {unique()}', starts_on=timezone.localdate() - timedelta(days=7),
        status=status, delivery=delivery, **extra,
    )
    cohort.facilitators.set(facilitators)
    return cohort


def make_participant(location, **extra):
    number = unique()
    defaults = {'first_name': 'Thabo', 'last_name': f'Molefe{number}', 'phone': f'+2772{number:07d}'}
    defaults.update(extra)
    return Participant.objects.create(home_location=location, **defaults)


def enrol(cohort, participant=None):
    participant = participant or make_participant(cohort.location)
    return CohortMembership.objects.create(cohort=cohort, participant=participant)


def make_session(cohort, course, hours=Decimal('12.0'), **extra):
    return CohortSession.objects.create(
        cohort=cohort, course=course, session_date=timezone.localdate(), hours=hours, **extra,
    )


def make_online_product(slug='metp-online-programme'):
    return Product.objects.create(
        name='METP online programme', slug=slug, product_type=Product.BUNDLE,
        price=Decimal('0.00'), description='Online programme', is_active=False, auto_enroll_free=False,
    )
