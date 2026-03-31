# 🚀 EventOps Backend

A **production-style backend system** for event management and seat booking, designed with **concurrency safety**, **clean architecture**, and **scalable workflows** in mind.

This project focuses on solving real-world backend challenges like:

* preventing **double booking under high concurrency**
* handling **distributed service dependencies**
* designing **extensible systems for async processing**

---

## 🧱 Tech Stack

* **Backend:** Django 6.0, Django REST Framework
* **Auth:** JWT (djangorestframework-simplejwt)
* **Database:** PostgreSQL
* **Cache / Queue placeholder:** Redis
* **Containerization:** Docker & Docker Compose

---

## ✨ Implemented Capabilities

### ✅ Authentication & Users

* `POST /api/auth/register/` with `username`, `email`, `password`
* `POST /api/auth/token/` for access + refresh tokens
* `POST /api/auth/token/refresh/`
* `users.User` includes `role` (`ADMIN`, `ORGANIZER`, `USER`)

### ✅ Event Management

* CRUD via `api/events/` using `EventViewSet`
  * `GET /api/events/`
  * `GET /api/events/{id}/`
  * `POST /api/events/` (admin/organizer only)
  * `PUT/PATCH/DELETE` with `IsAdminOrOrganizer`
* `available_seats` is annotated in read responses
* Event creation auto-generates seats (`events.Seat`) from `total_seats`
* Event total seats update adjusts seats safely with transaction lock
* Events include `price` field for ticket pricing
* model constraints:
  * `total_seats > 0`
  * `end_time > start_time`
  * `price >= 0`
  * unique `seat_number` per event

### ✅ Booking System

* `POST /api/bookings/` with:
  * `event`, `seat`, `idempotency_key`
* Amount is automatically set from the event's price (not user input)
* Idempotency + repeatable client safety:
  * first checks `user + idempotency_key`
  * re-checks inside `transaction.atomic()`
* Row-level locking via `Seat.objects.select_for_update()`
* CONFIRMED seat uniqueness enforced at DB level
* Payment integration with simulated gateway:
  * Automatic payment processing on booking creation
  * Retry logic with `POST /api/bookings/{id}/retry-payment/`
  * Booking expiry (15 minutes) and retry limits (3 attempts)
  * Statuses: PENDING, CONFIRMED, FAILED, EXPIRED, CANCELLED
* Rate limiting (throttling) on booking endpoints to prevent abuse
* `GET /api/bookings/` (user scope + optional `?status=` filter + pagination)
* `GET /api/bookings/{id}/`
* `POST /api/bookings/{id}/cancel/` sets `CANCELLED`
* `Booking` model constraints:
  * unique `(idempotency_key, user)`
  * unique confirmed seat

### ✅ Infrastructure

* `Dockerfile` and `docker-compose.yml` with `db`, `redis`, `web`
* `entrypoint.sh` waits for PostgreSQL, runs migrations, starts Django dev server
* Environment-driven settings in `core/settings.py`
* Custom pagination in `core/pagination.py`
* Rate limiting/throttling configured:
  * Booking endpoints: 5 requests/min per user
  * Auth endpoints: 10 requests/min per user
  * Default: 100 requests/min per user

### ✅ Postman Support

* `EventOps.postman_collection.json`
* `event_ops.postman_environment.json`

---

## 🚧 In Progress

* Workflow job system (async processing with retries)
* Role-Based Access Control (RBAC) beyond events
* Analytics endpoints (revenue, bookings, etc.)

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

3. Build and run:

```bash
docker compose up --build
```

`entrypoint.sh` handles:
* waiting for Postgres on `db:5432`
* `python manage.py makemigrations`
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

---

## 🔜 Future Enhancements

* Payment integration
* Dynamic pricing engine
* AI-based seat recommendations
* Fraud detection workflows
* Horizontal scaling support

---

## 📌 Why This Project?

This project is designed to demonstrate:

* Strong understanding of **backend fundamentals**
* Ability to handle **real-world concurrency issues**
* Knowledge of **system design and architecture**
* Experience with **production-like environments**




