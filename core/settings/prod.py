"""
Production overrides on top of base.py. Not currently wired up as the
active settings module anywhere (manage.py defaults to core.settings.dev,
see that file's docstring) — this exists as the target to point
DJANGO_SETTINGS_MODULE at once there's a real production deployment.
"""

import os

from .base import *


DEBUG = False

# Comma-separated host list from the environment (e.g. "example.com,www.example.com"),
# rather than base.py's wide-open ALLOWED_HOSTS = ["*"] used in dev.
raw_hosts = os.getenv("ALLOWED_HOSTS", "")
ALLOWED_HOSTS = [host.strip() for host in raw_hosts.split(",") if host.strip()]
