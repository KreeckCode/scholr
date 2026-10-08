import os
import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.validators import RegexValidator
from django.db import models
from django.utils.deconstruct import deconstructible

from partners.countries import COUNTRIES, COUNTRY_NAMES


phone_validator = RegexValidator(
    regex=r'^\+?1?\d{9,15}$',
    message="Phone must be in international format, for example '+27821234567'.",
)


@deconstructible
class PrivateStorage(FileSystemStorage):
    """
    Storage outside MEDIA_ROOT so licence agreements are never served by Nginx.
    Files are only reachable through a permission-checked view.
    The location is read from settings on every access so tests can override it.
    """

    @property
    def base_location(self):
        return self._value_or_setting(self._location, settings.PARTNERS_PRIVATE_ROOT)

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):
        raise NotImplementedError('Private files have no public URL.')


# I'm allowing overwrite because agreement paths are fixed per licence; a replacement must not orphan the old file
private_storage = PrivateStorage(allow_overwrite=True)


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PublicIdModel(models.Model):
    """UUID used in URLs and the offline API so integer keys are never exposed."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class Meta:
        abstract = True


class CountryField(models.CharField):
    """ISO 3166-1 alpha-2 code with the full country list as choices."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('max_length', 2)
        kwargs.setdefault('choices', COUNTRIES)
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        # I'm keeping the 249 choices out of migrations; they live in partners.countries
        kwargs.pop('choices', None)
        if kwargs.get('max_length') == 2:
            kwargs.pop('max_length')
        return name, path, args, kwargs


def country_name(code):
    return COUNTRY_NAMES.get(code or '', code or '')
