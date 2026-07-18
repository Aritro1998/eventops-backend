"""
Settings shared by every environment.

This project splits settings into base.py (this file) + one file per
environment (dev.py, prod.py) that does `from .base import *` and then
overrides only what differs — database engine, DEBUG, allowed hosts, etc.
`core/settings/__init__.py` decides which environment file is actually
loaded. Put anything that should be identical everywhere here; put
environment-specific values (like dev.py's SQLite fallback) in the
environment file instead.
"""

import os
from datetime import timedelta
from pathlib import Path

from celery.schedules import crontab


BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Falls back to a hardcoded dev-only value so the project runs out of the
# box without a .env file. Always set SECRET_KEY explicitly in production.
SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "django-insecure-l(zh$)@+c&r8+#d-_dkcnrx0l@5(&8^-6zm_l*8j11y&*otlnw",
)

ALLOWED_HOSTS = ["*"]

# Every Django app in this project. Order mostly doesn't matter, except
# that an app must appear before anything that references its models in
# a migration dependency.
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_filters",
    "rest_framework_simplejwt",
    "users",
    "events",
    "bookings",
    "workflows",
    "payments",
    "ai_assistant",
    'venues',
    'knowledge'
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"

# Postgres in every real environment. dev.py overrides this with SQLite
# only when DB_NAME isn't set, so contributors can run the project without
# a .env file — see the docstring at the top of dev.py.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME"),
        "USER": os.getenv("DB_USER"),
        "PASSWORD": os.getenv("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST"),
        "PORT": os.getenv("DB_PORT"),
    }
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_THROTTLE_CLASSES": [
        "core.throttles.DefaultThrottle",
    ],
    # Per-endpoint rate limits, keyed by the `throttle_scope` a view sets
    # (see core/throttles.py). "booking" is deliberately the tightest —
    # it's the only one that touches money/seat locks.
    "DEFAULT_THROTTLE_RATES": {
        "booking": "5/min",
        "auth": "10/min",
        "default": "100/min",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# Redis-backed cache, used for event/seat listing caches (see
# BookingService.invalidate_event_cache) and anywhere else `cache.get`/
# `cache.set` is used directly.
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://redis:6379/1",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    }
}

# Recurring background jobs, run by Celery Beat (see docker-compose.yml's
# `celery-beat` service) and defined in workflows/tasks.py. All four run
# every 5 minutes and exist to clean up or retry state that a request/
# response cycle can't reliably handle on its own — e.g. a booking hold
# that expires while nobody is looking at it.
CELERY_BEAT_SCHEDULE = {
    "requeue-pending-jobs": {
        "task": "workflows.tasks.requeue_pending_jobs_task",
        "schedule": crontab(minute="*/5"),
    },
    "cleanup-expired-pending-bookings": {
        "task": "workflows.tasks.cleanup_expired_pending_bookings_task",
        "schedule": crontab(minute="*/5"),
    },
    "cleanup-expired-pending-cancellations": {
        "task": "workflows.tasks.cleanup_expired_pending_cancellations_task",
        "schedule": crontab(minute="*/5"),
    },
    "cleanup-expired-pending-payment-retries": {
        "task": "workflows.tasks.cleanup_expired_pending_payment_retries_task",
        "schedule": crontab(minute="*/5"),
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
# For static assets not owned by any single app (e.g. shared admin JS) —
# `core` itself isn't in INSTALLED_APPS (it's a plain shared-utilities
# package, not a Django app), so its own static/ folder wouldn't be
# discovered otherwise.
STATICFILES_DIRS = [BASE_DIR / "static"]

AUTH_USER_MODEL = "users.User"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CELERY_BROKER_URL = "redis://redis:6379/0"
CELERY_RESULT_BACKEND = "redis://redis:6379/0"

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PORT = os.getenv("EMAIL_PORT")
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS") == "True"
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
