#!/bin/sh

echo "Waiting for postgres..."

while ! nc -z db 5432; do
  sleep 1
done

echo "PostgreSQL started"

python manage.py migrate

if [ "$DJANGO_SETTINGS_MODULE" = "core.settings.prod" ]; then
  python manage.py collectstatic --noinput
  echo "Starting uvicorn (production)"
  uvicorn core.asgi:application --host 0.0.0.0 --port 8000 --workers 3
else
  echo "Starting Django dev server (autoreload)"
  python manage.py runserver 0.0.0.0:8000
fi