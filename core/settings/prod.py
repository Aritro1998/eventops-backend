"""
Production overrides on top of base.py. Active whenever
DJANGO_SETTINGS_MODULE=core.settings.prod — entrypoint.sh checks that
same variable to decide whether to run uvicorn (this file) or
manage.py runserver (dev.py) instead.
"""

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *


DEBUG = False

# base.py falls back to a hardcoded, publicly-visible dev key so the
# project runs out of the box without a .env file - fine for dev, not
# acceptable here. Production must supply its own, or refuse to start
# rather than silently run on a key anyone can read in this repo.
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "SECRET_KEY must be set in the environment when running with "
        "core.settings.prod - refusing to fall back to base.py's dev-only key."
    )

# Comma-separated host list from the environment (e.g. "example.com,www.example.com"),
# rather than base.py's wide-open ALLOWED_HOSTS = ["*"] used in dev. Can
# still be set to "*" here too (no domain yet) - see this file's own
# ALLOWED_HOSTS handling below, which passes it through unchanged either way.
raw_hosts = os.getenv("ALLOWED_HOSTS", "")
ALLOWED_HOSTS = [host.strip() for host in raw_hosts.split(",") if host.strip()]
