# 🚀 EventOps Backend

A **production-style backend system** for event management and seat booking, designed with **concurrency safety**, **clean architecture**, and **scalable workflows** in mind.

This project focuses on solving real-world backend challenges like:

* preventing **double booking under high concurrency**
* handling **distributed service dependencies**
* designing **extensible systems for async processing**

---

## 🧠 System Architecture Overview

The system uses a layered backend architecture:

```text
Client
  |
  v
DRF API Layer
  |
  v
Service Layer
  |
  +--> PostgreSQL (events, seats, bookings, workflow jobs)
  |
  +--> Redis (cache + Celery broker/result backend)
  |
  v
WorkflowJob persistence
  |
  v
Celery Workers
  |
  +--> booking expiry handling
  +--> confirmation delivery
```

Background workflows are coordinated through a persistent `WorkflowJob` model and executed by `Celery` workers for async follow-up tasks such as booking expiry handling and confirmation delivery.

### Booking Flow

1. The client sends a booking request to `POST /api/bookings/`.
2. The API layer authenticates the user, validates input, and applies throttling.
3. `BookingService` performs an idempotency check and re-checks it again inside `transaction.atomic()`.
4. The seat row is locked with `select_for_update()` to prevent concurrent seat claims.
5. A `PENDING` booking is created in PostgreSQL with an expiry timestamp.
6. A `WorkflowJob` is created to expire the booking later if it is not confirmed in time.
7. Payment is processed in the request flow; on success the booking becomes `CONFIRMED`.
8. A confirmation workflow job is queued on state transition, and Celery workers handle email delivery and expiry processing in the background.
9. Event cache entries are invalidated on write paths that affect seat availability.

This architecture is designed to provide:

* concurrency safety for seat allocation
* retry-safe booking requests via idempotency
* durable async workflow tracking beyond the task queue alone
* cache consistency after state changes

## ⚙️ Key Engineering Decisions

* Used `select_for_update()` for row-level locking to prevent double booking under concurrency
* Implemented per-user idempotency keys so booking creation is safe to retry
* Added a `WorkflowJob` model for persistent async job tracking instead of relying on Celery state alone
* Used Redis for both caching and Celery infrastructure
* Combined database constraints, targeted indexes, and a composite workflow index for data integrity and query efficiency
* Tied cache invalidation to write operations that change event or seat availability state

## 🚀 Performance & Scaling Highlights

* Optimized hot paths with `select_related`, Redis caching, and targeted database indexes
* Cached event list and detail responses to reduce repeated read load on PostgreSQL
* Added a concurrency test with parallel booking attempts to verify that at most one booking reaches `CONFIRMED` for the same seat
* Enforced uniqueness for confirmed seat bookings at the database level as a final safety net
* Applied DRF throttling on auth and booking endpoints to reduce abuse risk

## 📊 Observability

* Structured JSON logging is configured through `python-json-logger`
* Key workflows emit logs for booking creation, payment outcomes, cache invalidation, workflow execution, retries, and failures
* `WorkflowJob` tracks async lifecycle state including `status`, `retry_count`, `last_error`, `started_at`, `completed_at`, and `result`
* Admin APIs expose failed jobs, stuck jobs, and retry actions for operational recovery

## 🧱 Tech Stack

* **Backend:** Django 5.x, Django REST Framework
* **Auth:** JWT (djangorestframework-simplejwt)
* **Database:** PostgreSQL
* **Cache / Queue:** Redis
* **Async tasks:** Celery
* **Containerization:** Docker & Docker Compose

---

## ✨ Core Capabilities

### ✅ Authentication & Users

* JWT-based auth with registration, login, and token refresh endpoints
* `users.User` supports `ADMIN`, `ORGANIZER`, and `USER` roles
* Registration normalizes email and enforces Django password validation

### ✅ Event Management

* Event CRUD with role-aware write permissions
* `available_seats` is exposed in read responses
* Event list and detail responses are cached in Redis for 5 minutes
* Event writes invalidate cache entries automatically
* Seat generation and seat-count updates are handled safely inside transactional logic
* Database constraints enforce event and seat integrity

### ✅ Booking System

* Booking creation uses idempotency keys, transactional re-checks, and row-level seat locking
* Confirmed seat uniqueness is enforced at the database level
* Payment flow supports retries and expiration handling
* Booking expiry and confirmation follow-up actions are managed through Celery-backed workflow jobs
* Booking endpoints support filtering, pagination, cancellation, and abuse protection via throttling
* Cache is invalidated when booking state changes affect seat availability

### ✅ Workflow Monitoring & Recovery

* Admin endpoints support workflow listing, failed-job inspection, stuck-job detection, and manual retries
* Workflow jobs can be filtered by type, status, and creation date
* Retry operations reset workflow state before requeueing

### ✅ Infrastructure & Tooling

* Docker Compose includes `db`, `redis`, `web`, `celery`, and `celery-beat`
* Redis powers both caching and Celery infrastructure
* Structured JSON logs are emitted for key operational paths
* Test suites cover bookings, payments, events, users, and workflows
* Postman collection and environment files are included for quick API exploration

---

## 📌 Why This Project?

This repository is built to showcase backend engineering beyond basic CRUD, with a focus on **concurrency control**, **service-layer design**, **async workflow reliability**, and **production-style operational thinking**.

---

## ⚙️ Local Setup (Docker)

1. Clone:

```bash
git clone https://github.com/Aritro1998/eventops-backend.git
cd eventops-backend
```

2. Create `.env`:

```env
DEBUG=True
SECRET_KEY=dev-secret-key
DJANGO_SETTINGS_MODULE=core.settings.dev
DB_NAME=eventops
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=db
DB_PORT=5432
```

Optional email settings (SMTP delivery rather than logging):

```env
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=user@example.com
EMAIL_HOST_PASSWORD=secret
EMAIL_USE_TLS=True
```

3. Build and run:

```bash
docker compose up --build
```

If you install dependencies outside Docker, make sure `python-json-logger` from `requirements.txt` is installed so Django can load the JSON log formatter from `core/settings.py`.

`entrypoint.sh` handles:
* waiting for Postgres on `db:5432`
* `python manage.py migrate`
* `python manage.py runserver 0.0.0.0:8000`

### Settings Modules

The project now uses split Django settings:

* `core.settings.base`
* `core.settings.dev`
* `core.settings.prod`

Default local entry points (`manage.py`, `wsgi.py`, `asgi.py`, and `celery.py`) use:

```env
DJANGO_SETTINGS_MODULE=core.settings.dev
```

To run with production settings, set:

```env
DJANGO_SETTINGS_MODULE=core.settings.prod
```

Production hosts are loaded from the `ALLOWED_HOSTS` environment variable as a comma-separated list.

Example:

```env
DJANGO_SETTINGS_MODULE=core.settings.prod
ALLOWED_HOSTS=example.com,www.example.com,api.example.com
```

4. Create superuser (optional):

```bash
docker compose exec web python manage.py createsuperuser
```

5. Open:
* App: `http://localhost:8000`
* Admin: `http://localhost:8000/admin`

6. Import Postman collection and environment:
* Open Postman and import `EventOps.postman_collection.json` and `event_ops.postman_environment.json`
* Set the environment to `event_ops` and update the base URL to `http://localhost:8000` if needed.
* The bundled environment also includes starter values for `admin`, `organizer`, `user`, booking IDs, and workflow-job filters that you can adjust for your local data.

---

## 🔜 Future Enhancements

* Dynamic pricing engine
* AI-based seat recommendations
* Fraud detection workflows
* Horizontal scaling support
* API docs and endpoint discovery

---
