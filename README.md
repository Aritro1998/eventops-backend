# 🚀 EventOps Backend

A **production-style backend system** for event management and seat booking, designed with **concurrency safety**, **clean architecture**, and **scalable workflows** in mind.

This project focuses on solving real-world backend challenges like:

* preventing **double booking under high concurrency**
* handling **distributed service dependencies**
* designing **extensible systems for async processing**

---

## 🧱 Tech Stack

* **Backend:** Django 5.x, Django REST Framework
* **Auth:** JWT (djangorestframework-simplejwt)
* **Database:** PostgreSQL
* **Cache / Queue:** Redis
* **Async tasks:** Celery
* **Containerization:** Docker & Docker Compose

---

## ✨ Implemented Capabilities

### ✅ Authentication & Users

* `POST /api/auth/register/` with `username`, `email`, `password`
* `POST /api/auth/token/` for access + refresh tokens
* `POST /api/auth/token/refresh/`
* `users.User` includes `role` (`ADMIN`, `ORGANIZER`, `USER`)
* Registration normalizes email to lowercase and always creates `USER` role accounts
* Django password validation is enforced during registration

### ✅ Event Management

* CRUD via `api/events/` using `EventViewSet`
  * `GET /api/events/`
  * `GET /api/events/{id}/`
  * `POST /api/events/` (admin/organizer only)
  * `PUT/PATCH/DELETE` with `IsAdminOrOrganizer`
* `available_seats` is annotated in read responses
* Event list and detail responses are cached in Redis for 5 minutes
* Event cache entries are invalidated after event create, update, and delete operations
* Event creation auto-generates seats (`events.Seat`) from `total_seats`
* Event total seats update adjusts seats safely with transaction lock
* Event seat reductions are blocked when higher-numbered seats still have active bookings
* Events include `price` field for ticket pricing
* model constraints:
  * `total_seats > 0`
  * `end_time > start_time`
  * `price >= 0`
  * unique `seat_number` per event
  * `seat_number > 0`

### ✅ Booking System

* `POST /api/bookings/` with:
  * `event`, `seat`, `idempotency_key`
* Amount is automatically set from the event's price (not user input)
* Idempotency + repeatable client safety:
  * first checks `user + idempotency_key`
  * re-checks inside `transaction.atomic()`
* Row-level locking via `Seat.objects.select_for_update()`
* CONFIRMED seat uniqueness enforced at DB level
* Booking expiration workflow:
  * bookings expire after 15 minutes if not confirmed
  * expiry is scheduled with a Celery workflow job
  * expired bookings free the seat for reuse
* Booking confirmation workflow:
  * confirmation emails are sent via Celery jobs when SMTP is configured
  * email notification payloads are generated on successful confirmation
* Payment integration with simulated gateway:
  * Automatic payment processing on booking creation
  * Retry logic with `POST /api/bookings/{id}/retry-payment/`
  * Retry limits: 3 attempts
  * Booking statuses: `PENDING`, `CONFIRMED`, `FAILED`, `EXPIRED`, `CANCELLED`
* Rate limiting (throttling) on booking endpoints to prevent abuse
* `GET /api/bookings/` (user scope + optional `?status=` filter + pagination)
* `GET /api/bookings/{id}/`
* `POST /api/bookings/{id}/cancel/` sets `CANCELLED`
* Event cache is invalidated when a payment confirms a booking or a confirmed booking is cancelled
* `Booking` model constraints:
  * unique `(idempotency_key, user)`
  * unique confirmed seat
  * `retry_count >= 0`

### ✅ Workflow Monitoring & Recovery

* Admin-only workflow monitoring endpoints:
  * `GET /api/workflows/jobs/`
  * `GET /api/workflows/jobs/{id}/`
  * `GET /api/workflows/stuck-jobs/`
  * `GET /api/workflows/failed-jobs/`
  * `POST /api/workflows/retry-job/{job_id}/`
* Workflow list supports filtering by:
  * `job_type`
  * `status`
  * `created_date=YYYY-MM-DD`
* Failed jobs endpoint supports filtering by:
  * `job_type`
  * `created_date=YYYY-MM-DD`
* Stuck jobs endpoint surfaces jobs in `IN_PROGRESS` for more than 5 minutes
* Retrying a failed job resets retry metadata, timestamps, result payload, and email-sent state before requeueing
* `WorkflowJob` tracks:
  * `status`
  * `retry_count`
  * `last_error`
  * `started_at`
  * `completed_at`
  * `result`
  * `payload`
* Workflow job constraints and indexing:
  * `0 <= retry_count <= 5`
  * composite index on `(status, job_type)`

### ✅ Infrastructure

* `Dockerfile` and `docker-compose.yml` with `db`, `redis`, `web`, `celery`, and `celery-beat`
* `entrypoint.sh` waits for PostgreSQL, runs migrations, and starts the Django dev server
* Database and email settings are environment-driven in `core/settings.py`
* Redis-backed Celery broker and result backend
* Scheduled workflow requeue via Celery beat every 5 minutes
* Email backend placeholders and workflow notification pipeline (optional SMTP config)
* Custom pagination in `core/pagination.py`
* Rate limiting/throttling configured:
  * Booking endpoints: 5 requests/min per user
  * Auth endpoints: 10 requests/min per user
  * Default: 100 requests/min per user

### ✅ Tests

* Unit test suites added for bookings, payments, events, users, and workflows

### ✅ Postman Support

* `EventOps.postman_collection.json`
* `event_ops.postman_environment.json`
* Environment includes convenience variables for workflow endpoints such as `workflow_job_id` and `created_date`

---

## 🚧 In Progress

* Role-Based Access Control (RBAC) beyond events
* Analytics endpoints (revenue, bookings, etc.)
* API documentation / OpenAPI schema generation

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

`entrypoint.sh` handles:
* waiting for Postgres on `db:5432`
* `python manage.py migrate`
* `python manage.py runserver 0.0.0.0:8000`

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

## 📌 Why This Project?

This project is designed to demonstrate:

* Strong understanding of **backend fundamentals**
* Ability to handle **real-world concurrency issues**
* Knowledge of **system design and architecture**
* Experience with **production-like environments**


