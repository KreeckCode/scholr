from pathlib import Path
import os

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def env_flag(name, default='False'):
    """Environment variables are strings; 'False' is truthy, so parse properly."""
    return os.getenv(name, default).strip().lower() in ('1', 'true', 'yes', 'on')


# ==================== CORE SECURITY ====================

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-fallback-key-change-this')
DEBUG = env_flag('DEBUG')

ALLOWED_HOSTS = os.getenv(
    'ALLOWED_HOSTS', '138.68.146.233,judea.uorivo.com,localhost,127.0.0.1'
).split(',')

CSRF_TRUSTED_ORIGINS = os.getenv(
    'CSRF_TRUSTED_ORIGINS', 'https://judea.uorivo.com'
).split(',')


# ==================== PRODUCT FEATURE FLAGS ====================

SCHOLR_LMS_ENABLED        = env_flag('SCHOLR_LMS_ENABLED', 'True')
SCHOLR_ACADEMICS_ENABLED  = env_flag('SCHOLR_ACADEMICS_ENABLED', 'True')
SCHOLR_ENROLLMENT_ENABLED = env_flag('SCHOLR_ENROLLMENT_ENABLED')
SCHOLR_BURSARY_ENABLED    = env_flag('SCHOLR_BURSARY_ENABLED')
SCHOLR_BRIDGE_ENABLED     = env_flag('SCHOLR_BRIDGE_ENABLED')
SCHOLR_SELF_REGISTRATION = env_flag('SCHOLR_SELF_REGISTRATION', 'True')
SCHOLR_PARTNERS_ENABLED   = env_flag('SCHOLR_PARTNERS_ENABLED')

# ==================== SITE PROFILE AND BRANDING ====================

from .brands import SITE_BRANDS

SITE_PROFILE = os.getenv('SITE_PROFILE', 'school').strip().lower()
SITE_BRAND = SITE_BRANDS.get(SITE_PROFILE, SITE_BRANDS['school'])


# ==================== APPLICATIONS ====================

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'accounts',
    'integration.apps.IntegrationConfig',
    'shop',
    'academics',
    'main',
    'enrollment.apps.EnrollmentConfig',
    'partners.apps.PartnersConfig',
]

if DEBUG:
    INSTALLED_APPS += ['debug_toolbar']


# ==================== MIDDLEWARE ====================

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'scholr.middleware.FeatureFlagMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

if DEBUG:
    MIDDLEWARE.insert(0, 'debug_toolbar.middleware.DebugToolbarMiddleware')
    INTERNAL_IPS = ['127.0.0.1']

ROOT_URLCONF = 'scholr.urls'
WSGI_APPLICATION = 'scholr.wsgi.application'


# ==================== TEMPLATES ====================

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.media',
                'academics.context_processors.school_context',
                'scholr.context_processors.features',
                'scholr.context_processors.cart',
            ],
        },
    },
]


# ==================== DATABASE ====================

import dj_database_url

DATABASE_URL = os.getenv('DATABASE_URL')

if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.config(
            default=DATABASE_URL, conn_max_age=600, conn_health_checks=True,
        )
    }
elif DEBUG:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
else:
    raise ImproperlyConfigured('DATABASE_URL must be set when DEBUG is off.')


# ==================== AUTHENTICATION ====================

AUTH_USER_MODEL = 'accounts.User'

LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'accounts:dashboard'
LOGOUT_REDIRECT_URL = 'accounts:login'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8},
    },
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ==================== INTERNATIONALISATION ====================

LANGUAGE_CODE = 'en-gb'
TIME_ZONE = 'Africa/Johannesburg'
USE_I18N = True
USE_TZ = True


# ==================== STATIC AND MEDIA ====================

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

STATICFILES_DIRS = []
if (BASE_DIR / 'static').exists():
    STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL = '/media/'
MEDIA_ROOT = os.getenv('MEDIA_ROOT', str(BASE_DIR / 'media'))

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ==================== SESSIONS AND CSRF ====================

SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
SESSION_CACHE_ALIAS = 'sessions'
SESSION_COOKIE_AGE = 1209600
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = env_flag('SESSION_COOKIE_SECURE')
SESSION_COOKIE_SAMESITE = 'Lax'

CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SECURE = env_flag('CSRF_COOKIE_SECURE')
CSRF_COOKIE_SAMESITE = 'Lax'


# ==================== TRANSPORT SECURITY ====================

SECURE_SSL_REDIRECT = env_flag('SECURE_SSL_REDIRECT')
SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_flag('SECURE_HSTS_INCLUDE_SUBDOMAINS')
SECURE_HSTS_PRELOAD = env_flag('SECURE_HSTS_PRELOAD')
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') if SECURE_SSL_REDIRECT else None

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'


# ==================== EMAIL ====================

if DEBUG:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
else:
    EMAIL_BACKEND = os.getenv(
        'EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend'
    )

EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_USE_TLS = env_flag('EMAIL_USE_TLS', 'True')
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')

DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'noreply@scholr.co.za')
SERVER_EMAIL = os.getenv('SERVER_EMAIL', 'server@scholr.co.za')
SITE_URL = os.getenv('SITE_URL', 'http://localhost:8000')


# ==================== SHOP AND PAYMENTS ====================

CURRENCY_SYMBOL = 'R'
CURRENCY_CODE = 'ZAR'
SHOP_NAME = SITE_BRAND['name']

PAYSTACK_PUBLIC_KEY = os.getenv('PAYSTACK_PUBLIC_KEY', '')
PAYSTACK_SECRET_KEY = os.getenv('PAYSTACK_SECRET_KEY', '')
STRIPE_PUBLIC_KEY = os.getenv('STRIPE_PUBLIC_KEY', '')
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')


# ==================== BANKING DETAILS (EFT) ====================

BANK_NAME           = os.getenv('BANK_NAME', '')
BANK_ACCOUNT_NAME   = os.getenv('BANK_ACCOUNT_NAME', '')
BANK_ACCOUNT_NUMBER = os.getenv('BANK_ACCOUNT_NUMBER', '')
BANK_BRANCH_CODE    = os.getenv('BANK_BRANCH_CODE', '')
BANK_ACCOUNT_TYPE   = os.getenv('BANK_ACCOUNT_TYPE', 'Cheque / Current')


# ==================== MOODLE INTEGRATION ====================

MOODLE_URL = os.getenv('MOODLE_URL', 'https://judea.uorivo.com/moodle501')
MOODLE_WS_TOKEN = os.getenv('MOODLE_WS_TOKEN', '')

# Moodle role IDs given to staff who open the LMS from their dashboard.
# Stock Moodle: 1 Manager, 3 Teacher (editing), 4 Non-editing teacher.
MOODLE_STAFF_COURSE_ROLES = {
    'admin':           int(os.getenv('MOODLE_ROLE_ADMIN', '3')),
    'programme_staff': int(os.getenv('MOODLE_ROLE_PROGRAMME_STAFF', '3')),
    'dean':            int(os.getenv('MOODLE_ROLE_DEAN', '4')),
}
MOODLE_MANAGER_ROLE_ID = int(os.getenv('MOODLE_MANAGER_ROLE_ID', '1'))
MOODLE_ASSIGN_MANAGER_TO_ADMINS = env_flag('MOODLE_ASSIGN_MANAGER_TO_ADMINS')


# ==================== PARTNER NETWORK ====================

PARTNERS_PRIVATE_ROOT = os.getenv('PARTNERS_PRIVATE_ROOT', str(BASE_DIR / 'private_media'))
PARTNERS_STAFF_EMAILS = [
    address.strip() for address in os.getenv('PARTNERS_STAFF_EMAILS', '').split(',') if address.strip()
]
PARTNERS_DEFAULT_CURRENCY = os.getenv('PARTNERS_DEFAULT_CURRENCY', 'CAD')
PARTNERS_ONLINE_PRODUCT_SLUG = os.getenv('PARTNERS_ONLINE_PRODUCT_SLUG', 'metp-online-programme')
PARTNERS_PASS_MARK = int(os.getenv('PARTNERS_PASS_MARK', '50'))
PARTNERS_MIN_ATTENDANCE_PERCENT = int(os.getenv('PARTNERS_MIN_ATTENDANCE_PERCENT', '75'))
PARTNERS_STALL_DAYS = int(os.getenv('PARTNERS_STALL_DAYS', '21'))
PARTNERS_PACK_DAYS = int(os.getenv('PARTNERS_PACK_DAYS', '30'))
PARTNERS_LICENCE_REMINDER_DAYS = (90, 30, 7)
PARTNERS_MAP_TILE_URL = os.getenv('PARTNERS_MAP_TILE_URL', 'https://tile.openstreetmap.org/{z}/{x}/{y}.png')
PARTNERS_MAP_ATTRIBUTION = os.getenv(
    'PARTNERS_MAP_ATTRIBUTION', '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
)
PARTNERS_MAP_MAX_ZOOM = int(os.getenv('PARTNERS_MAP_MAX_ZOOM', '19'))
PARTNERS_MAP_SEARCH_URL = os.getenv('PARTNERS_MAP_SEARCH_URL', 'https://nominatim.openstreetmap.org/search')
PARTNERS_MAP_USER_AGENT = os.getenv('PARTNERS_MAP_USER_AGENT', 'Scholr partner map (https://metp.uorivo.com)')


# ==================== FILE UPLOADS ====================

FILE_UPLOAD_MAX_MEMORY_SIZE = 5242880
DATA_UPLOAD_MAX_MEMORY_SIZE = 5242880
FILE_UPLOAD_PERMISSIONS = 0o644


# ==================== CACHING ====================

REDIS_URL = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379')

CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': f'{REDIS_URL}/1',
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'SOCKET_CONNECT_TIMEOUT': 5,
            'SOCKET_TIMEOUT': 5,
            'COMPRESSOR': 'django_redis.compressors.zlib.ZlibCompressor',
            'CONNECTION_POOL_KWARGS': {
                'max_connections': 50,
                'retry_on_timeout': True,
            },
            'SERIALIZER': 'django_redis.serializers.json.JSONSerializer',
        },
        'KEY_PREFIX': 'scholr',
        'TIMEOUT': 300,
    },
    'sessions': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': f'{REDIS_URL}/2',
        'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient'},
        'KEY_PREFIX': 'session',
        'TIMEOUT': 86400,
    },
    'moodle': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': f'{REDIS_URL}/3',
        'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient'},
        'KEY_PREFIX': 'moodle',
        'TIMEOUT': 3600,
    },
}


# ==================== CELERY ====================

from celery.schedules import crontab

CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', f'{REDIS_URL}/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', f'{REDIS_URL}/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1000
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

CELERY_TASK_ANNOTATIONS = {
    'shop.tasks.sync_user_progress_task': {'rate_limit': '10/m'},
    'shop.tasks.sync_moodle_courses': {'rate_limit': '5/m'},
}

CELERY_BEAT_SCHEDULE = {
    'cleanup-expired-enrollments': {
        'task': 'shop.tasks.cleanup_expired_enrollments',
        'schedule': crontab(hour=2, minute=0),
    },
    'sync-all-user-progress': {
        'task': 'shop.tasks.sync_all_user_progress',
        'schedule': crontab(hour=3, minute=0),
    },
    'sync-online-learning-progress': {
        'task': 'academics.tasks.sync_online_learning_progress_task',
        'schedule': crontab(hour=4, minute=0),
    },
    'send-progress-stall-notifications': {
        'task': 'academics.tasks.send_stall_notifications_task',
        'schedule': crontab(hour=6, minute=0),
    },
    'partners-update-licence-statuses': {
        'task': 'partners.tasks.update_licence_statuses',
        'schedule': crontab(hour=1, minute=30),
    },
    'partners-stalled-partnerships-digest': {
        'task': 'partners.tasks.stalled_partnerships_digest',
        'schedule': crontab(hour=7, minute=0, day_of_week='mon'),
    },
}


# ==================== LOGGING ====================

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'filters': {
        'ignore_browser_requests': {
            '()': 'django.utils.log.CallbackFilter',
            'callback': lambda record: '.well-known' not in record.getMessage(),
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
            'filters': ['ignore_browser_requests'],
        },
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': BASE_DIR / 'logs' / 'django.log',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.server': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'accounts': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'shop': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'integration': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'academics': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'enrollment': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'partners': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}