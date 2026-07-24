"""
Development settings. This is the default: manage.py sets
DJANGO_SETTINGS_MODULE to "core.settings.dev" unless something overrides
it, so any `python manage.py ...` command — run directly on the host, or
inside a Docker container — uses this file.

The important thing to understand here is the `if not os.getenv("DB_NAME")`
block below. Docker Compose loads `.env` (which sets DB_NAME=eventops,
pointing at the real Postgres `db` service), so inside Docker this block
is skipped entirely and DATABASES/CACHES/CELERY_* fall through to base.py's
real Postgres/Redis config. But `.env` is NOT automatically loaded when you
run `python manage.py ...` directly on the host (Django doesn't read .env
files itself) — so on a bare host shell, DB_NAME is unset, and this file
silently falls back to a local SQLite file (db.sqlite3) plus in-memory
cache/Celery, so the project still runs without any setup. The two
databases are NOT the same data — commands run on the host land in
db.sqlite3, commands run via `docker exec eventops_web ...` land in the
real Postgres. When in doubt about which one you're touching, always
prefer `docker exec eventops_web python manage.py ...`.
"""

import os, socket

from .base import *

DEBUG = os.getenv("DEBUG", "True").strip().lower() == "true"

ALLOWED_HOSTS = ["*"]

if not os.getenv("DB_NAME"):
    # No .env loaded (bare host shell) — fall back to a throwaway local
    # SQLite database and in-memory cache/broker so the project still runs.
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
    # Loosen throttle limits drastically in this no-.env fallback mode so
    # local exploratory testing doesn't get rate-limited by accident.
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["booking"] = "1000/min"
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["auth"] = "1000/min"
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["default"] = "10000/min"

# django-debug-toolbar, dev-only — shows SQL queries, request timing, etc.
# as an overlay in the browser. Never enabled in prod.py.
INSTALLED_APPS += [
    "debug_toolbar",
]

MIDDLEWARE = [
    "debug_toolbar.middleware.DebugToolbarMiddleware",
] + MIDDLEWARE

# debug_toolbar only renders for requests coming from an IP in this list.
INTERNAL_IPS = [
    "127.0.0.1",
    "localhost",
]

# Also allow the toolbar when running inside Docker: the container's own
# network interface IP isn't 127.0.0.1, so without this, browsing the app
# from the host machine while it runs in a container would never show it.
try:
    hostname, _, ips = socket.gethostbyname_ex(socket.gethostname())
except socket.gaierror:
    ips = []

INTERNAL_IPS += [ip.rsplit(".", 1)[0] + ".1" for ip in ips]
