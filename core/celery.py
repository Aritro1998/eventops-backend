"""
Celery application entrypoint. This is what `celery -A core.celery worker`
and `celery -A core.celery beat` (see docker-compose.yml's `celery` and
`celery-beat` services) actually load.
"""

import os
from celery import Celery

# Celery starts independently of `manage.py`, so Django settings aren't
# configured yet at this point — this line makes sure they are before
# `config_from_object` below reads CELERY_BROKER_URL etc. from them.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.dev")

app = Celery("core")

# Pulls all CELERY_* settings (broker URL, result backend, beat schedule)
# from Django settings instead of duplicating them here.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Only workflows/ defines Celery tasks in this project, so that's the only
# app scanned for an @app.task-decorated tasks.py.
app.autodiscover_tasks(["workflows"])
