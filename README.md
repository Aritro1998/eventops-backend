# 🚀 EventOps Backend

A **production-style backend system** for event management and seat booking, designed with **concurrency safety**, **clean architecture**, and **scalable workflows** in mind.

This project focuses on solving real-world backend challenges like:

* preventing **double booking under high concurrency**
* handling **distributed service dependencies**
* designing **extensible systems for async processing**

---

## 🧱 Tech Stack

* **Backend:** Django, Django REST Framework (DRF)
* **Database:** PostgreSQL
* **Cache / Queue (planned):** Redis
* **Containerization:** Docker & Docker Compose

---

## ✨ Features

### ✅ Implemented

* Dockerized multi-service setup (Django + Postgres + Redis)
* Environment-based configuration using `.env`
* Production-style service startup with entrypoint script

### 🚧 In Progress

* Event management APIs
* Seat booking system with **row-level locking**
* Validation layer using DRF serializers

### 🔜 Planned

* Concurrency-safe booking (`select_for_update`)
* Workflow job system (async processing with retries)
* Role-Based Access Control (RBAC)
* API rate limiting, filtering, pagination
* Analytics endpoints (revenue, bookings, etc.)

---

## ⚙️ Local Setup (Docker)

### 1️⃣ Clone the repository

```bash
git clone <your-repo-url>
cd eventops-backend
```

---

### 2️⃣ Create `.env` file

Create a `.env` file in the project root:

```env
DEBUG=True
SECRET_KEY=dev-secret-key

DB_NAME=eventops
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=db
DB_PORT=5432
```

---

### 3️⃣ Build and start services

```bash
docker compose up --build
```

---

### 4️⃣ Run migrations (first time only)

```bash
docker compose exec web python manage.py migrate
```

---

### 5️⃣ Create superuser

```bash
docker compose exec web python manage.py createsuperuser
```

---

### 6️⃣ Access the application

* App: http://localhost:8000
* Admin: http://localhost:8000/admin

---

## 🧠 Architecture Overview

```text
Client → Django (API Layer)
              ↓
        Business Logic (Services)
              ↓
        ORM Layer (QuerySets)
              ↓
         PostgreSQL Database
              ↓
        Redis (Async / Caching)
```

---

## 🔥 Key Design Highlights

### 1️⃣ Concurrency-Safe Booking

* Uses **database transactions**
* Implements **row-level locking (`select_for_update`)**
* Prevents **race conditions and double booking**

---

### 2️⃣ Clean Backend Architecture

```text
Views → Services → QuerySets → Database
```

* Thin views
* Business logic isolated in services
* Reusable query logic via custom QuerySets

---

### 3️⃣ Production-Oriented Setup

* Dockerized environment
* Service dependency handling (DB readiness)
* Environment-based configuration
* Ready for async workflows (Celery + Redis)

---

## 🧪 Future Enhancements

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



