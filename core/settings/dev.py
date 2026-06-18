import os, socket

from .base import *

DEBUG = os.getenv("DEBUG", "True").strip().lower() == "true"

ALLOWED_HOSTS = ["*"]

if not os.getenv("DB_NAME"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "eventops-dev-test-cache",
        }
    }
    CELERY_BROKER_URL = "memory://"
    CELERY_RESULT_BACKEND = "cache+memory://"
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["booking"] = "1000/min"
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["auth"] = "1000/min"
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["default"] = "10000/min"

INSTALLED_APPS += [
    "debug_toolbar",
]

MIDDLEWARE = [
    "debug_toolbar.middleware.DebugToolbarMiddleware",
] + MIDDLEWARE

INTERNAL_IPS = [
    "127.0.0.1",
    "localhost",
]

try:
    hostname, _, ips = socket.gethostbyname_ex(socket.gethostname())
except socket.gaierror:
    ips = []

INTERNAL_IPS += [ip.rsplit(".", 1)[0] + ".1" for ip in ips]
