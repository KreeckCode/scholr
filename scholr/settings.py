from pathlib import Path
import os

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Build paths inside the project
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY SETTINGS
# Get from environment variables - never hardcode in production
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-fallback-key-change-this')
DEBUG = os.getenv('DEBUG', 'False') == 'True'

# Parse allowed hosts from comma-separated string
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '165.232.37.122,judea.uorivo.com,localhost,127.0.0.1').split(',')

# CSRF trusted origins for domain
CSRF_TRUSTED_ORIGINS = os.getenv('CSRF_TRUSTED_ORIGINS', 'https://judea.uorivo.com').split(',')

# INSTALLED APPS
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Project apps
    'accounts',
    'integration.apps.IntegrationConfig',
    'shop',
    'academics',
    'main',
    'debug_toolbar',

]

# MIDDLEWARE
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

ROOT_URLCONF = 'scholr.urls'

# TEMPLATES
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
            ],
        },
    },
]

WSGI_APPLICATION = 'scholr.wsgi.application'


# ==================== PRODUCT FEATURE FLAGS ====================

SCHOLR_LMS_ENABLED = os.getenv('SCHOLR_LMS_ENABLED', 'True') == 'True'
SCHOLR_ACADEMICS_ENABLED = os.getenv('SCHOLR_ACADEMICS_ENABLED', 'True') == 'True'
# DATABASE
# Use DATABASE_URL for easy configuration
import dj_database_url

if os.getenv('DATABASE_URL'):
    # Production - PostgreSQL from environment
    DATABASES = {
        'default': dj_database_url.config(
            default=os.getenv('DATABASE_URL'),
            conn_max_age=600,
            conn_health_checks=True,
        )
    }
else:
    # Fallback to SQLite for local dev (should never happen in production)
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# PASSWORD VALIDATION
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 8,
        }
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# INTERNATIONALIZATION
LANGUAGE_CODE = 'en-gb'
TIME_ZONE = 'Africa/Johannesburg'
USE_I18N = True
USE_TZ = True

# CUSTOM USER MODEL
AUTH_USER_MODEL = 'accounts.User'

# AUTHENTICATION URLs
LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'accounts:dashboard'
LOGOUT_REDIRECT_URL = 'accounts:login'

# STATIC FILES
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Only include static/ directory if it exists
STATICFILES_DIRS = []
if (BASE_DIR / 'static').exists():
    STATICFILES_DIRS = [BASE_DIR / 'static']

# MEDIA FILES
MEDIA_URL = '/media/'
MEDIA_ROOT = os.getenv('MEDIA_ROOT', str(BASE_DIR / 'media'))

# DEFAULT PRIMARY KEY
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# SESSION SETTINGS
SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
SESSION_CACHE_ALIAS = 'sessions'
SESSION_COOKIE_AGE = 1209600  # 2 weeks
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'False') == 'True'
SESSION_COOKIE_SAMESITE = 'Lax'

# CSRF SETTINGS
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SECURE = os.getenv('CSRF_COOKIE_SECURE', 'False') == 'True'
CSRF_COOKIE_SAMESITE = 'Lax'

# SECURITY SETTINGS (enabled when HTTPS is configured)
SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'False') == 'True'
SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv('SECURE_HSTS_INCLUDE_SUBDOMAINS', 'False') == 'True'
SECURE_HSTS_PRELOAD = os.getenv('SECURE_HSTS_PRELOAD', 'False') == 'True'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') if SECURE_SSL_REDIRECT else None

# EMAIL CONFIGURATION
if DEBUG:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
else:
    EMAIL_BACKEND = os.getenv(
        'EMAIL_BACKEND',
        'django.core.mail.backends.smtp.EmailBackend',
    )
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True') == 'True'
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'noreply@scholr.co.za')
SERVER_EMAIL = os.getenv('SERVER_EMAIL', 'server@scholr.co.za')
SITE_URL = os.getenv('SITE_URL', 'http://localhost:8000')
# SHOP CONFIGURATION
CURRENCY_SYMBOL = 'R'
CURRENCY_CODE = 'ZAR'
SHOP_NAME = 'African Pastoral Academy'

# PAYMENT GATEWAYS
PAYSTACK_PUBLIC_KEY = os.getenv('PAYSTACK_PUBLIC_KEY', '')
PAYSTACK_SECRET_KEY = os.getenv('PAYSTACK_SECRET_KEY', '')
STRIPE_PUBLIC_KEY = os.getenv('STRIPE_PUBLIC_KEY', '')
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')

# MOODLE INTEGRATION
MOODLE_URL = os.getenv('MOODLE_URL', 'https://judea.uorivo.com/moodle501')

# FILE UPLOAD SETTINGS
FILE_UPLOAD_MAX_MEMORY_SIZE = 5242880  # 5MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 5242880  # 5MB
FILE_UPLOAD_PERMISSIONS = 0o644

# REDIS & CACHING CONFIGURATION
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
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        },
        'KEY_PREFIX': 'session',
        'TIMEOUT': 86400,
    },
    'moodle': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': f'{REDIS_URL}/3',
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        },
        'KEY_PREFIX': 'moodle',
        'TIMEOUT': 3600,
    },
}

# CELERY CONFIGURATION
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

# Celery task rate limits
CELERY_TASK_ANNOTATIONS = {
    'shop.tasks.sync_user_progress_task': {'rate_limit': '10/m'},
    'shop.tasks.sync_moodle_courses': {'rate_limit': '5/m'},
}

# LOGGING CONFIGURATION
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
            'callback': lambda record: '.well-known' not in record.getMessage()
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
    },
}

from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    # Shop: clean up expired enrollments
    'cleanup-expired-enrollments': {
        'task': 'shop.tasks.cleanup_expired_enrollments',
        'schedule': crontab(hour=2, minute=0),
    },
    # Shop: sync commercial LMS progress
    'sync-all-user-progress': {
        'task': 'shop.tasks.sync_all_user_progress',
        'schedule': crontab(hour=3, minute=0),
    },
    # Academics: pull Moodle progress for school learners (runs first)
    'sync-online-learning-progress': {
        'task': 'academics.tasks.sync_online_learning_progress_task',
        'schedule': crontab(hour=4, minute=0),
    },
    # Academics: email stall alerts (runs 2 hours after sync so data is fresh)
    'send-progress-stall-notifications': {
        'task': 'academics.tasks.send_stall_notifications_task',
        'schedule': crontab(hour=6, minute=0),
    },
}