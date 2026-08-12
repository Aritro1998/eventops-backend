# 🚀 EventOps Backend

A backend system for event management and seat booking — built to demonstrate Django/Python backend engineering beyond CRUD: concurrency control, transactional correctness, distributed background processing, real-time updates, and caching consistency, with an AI assistant integrated on top of the same service layer the REST API uses.

The AI assistant is a real capability, not a demo bolted onto the side of the app — it reads and writes through the exact same transactional service layer, tools, and cache as the REST API, with destructive actions always gated behind explicit human confirmation.

---

## What This Project Demonstrates

**Backend engineering**
* Django / DRF: layered API design, JWT auth, role-based permissions, throttling, pagination
* Concurrency & data integrity: `select_for_update()` row locking, idempotency keys, database constraints
* Distributed systems: Celery + Redis-backed task queue, a persistent `WorkflowJob` model for durable async state
* Real-time systems: Django Channels + a Redis-backed channel layer for cross-process WebSocket coordination
* Caching: a single cached service layer shared by the REST API and the AI assistant, with consistent invalidation on every write
* Search: PostgreSQL trigram indexing (`pg_trgm`) for fuzzy text search, no in-Python scanning

**AI integration**
* LangChain tool-calling agent, LangGraph for stateful, human-in-the-loop workflows (booking, cancellation, payment retry)
* Retrieval-augmented generation: `pgvector` similarity search over embedded knowledge documents
* Per-call token/cost observability for every LLM and embedding call

**Operational maturity**
* Structured JSON logging, an admin-facing usage/cost dashboard, and workflow retry tooling
* Deterministic test suite plus a separate behavioral eval harness for the LLM-driven paths
* Containerized deployment with a real dev/production split — Nginx reverse proxy, multi-worker ASGI server, environment-driven configuration

---

## Quick Start

```bash
git clone https://github.com/Aritro1998/eventops-backend.git
cd eventops-backend
cp .env.example .env   # add your OPENAI_API_KEY
docker compose up -d --build
```

Then open `http://localhost:8000` (API/admin) and `http://localhost:7860` (AI assistant demo chat). See [Setup & Running Locally](#️-setup--running-locally) for the full walkthrough, including the production configuration.

---

## 🧠 Architecture Overview

The system is organized around a few concrete backend problems: preventing double booking under concurrent requests, keeping a cache consistent with the database on every write, coordinating background work durably (not just fire-and-forget), pushing real-time updates to connected clients, and letting an AI agent act on real data without being able to make an unreviewed mutation.

```text
Client (REST) ---------- Client (WebSocket) ---------- Client (AI Chat)
     |                          |                            |
     v                          v                            v
DRF API Layer          Channels Consumer            Async Chat View (SSE)
     |                          |                            |
     v                          |                            v
Service Layer <------------------------------------  LangChain/LangGraph
     |                                                 Agent Loop
     +--> PostgreSQL (events, seats, bookings,                |
     |     workflow jobs, knowledge chunks +          (search events, check
     |     pgvector embeddings)                        seats, draft booking/
     |                                                  cancel/payment-retry)
     +--> Redis (cache + Celery broker/result                 |
     |     backend + Channels layer)                   shares the SAME
     |                                                   cached read paths
     v                                                   and service layer
WorkflowJob persistence                                  as the REST API
     |
     v
Celery Workers
     |
     +--> booking expiry handling
     +--> confirmation email delivery
     +--> knowledge document chunking + embedding generation
     +--> stale LangGraph checkpoint cleanup
```

Background workflows are coordinated through a persistent `WorkflowJob` model and executed by Celery workers. Live seat state is pushed to connected clients over WebSockets (Django Channels). The AI assistant reads through the exact same cached service layer the REST API uses — there's one source of truth for event/seat data, not two implementations that can drift apart.

---

## 🧱 Tech Stack

**Backend**
* Django 5.x, Django REST Framework
* JWT auth (djangorestframework-simplejwt)
* PostgreSQL, with `pgvector` and `pg_trgm` extensions
* Redis (cache, Celery broker/result backend, Channels layer)
* Celery (worker + beat)
* Django Channels (ASGI, WebSockets)
* Auto-generated OpenAPI schema (drf-spectacular) with Swagger UI/ReDoc

**AI integration**
* LangChain (tool-calling agent loop)
* LangGraph (stateful, human-in-the-loop workflows with a persistent Postgres checkpointer)
* OpenAI API (chat completions + embeddings), `tiktoken` for local token counting

**Deployment**
* Daphne (development), uvicorn with multiple worker processes (production)
* Nginx reverse proxy (production) — static files and WebSocket/HTTP proxying
* Docker & Docker Compose, with a profile-based dev/production split
* Gradio (demo chat frontend)

---

## Backend Design Highlights

| Problem | Approach |
|---|---|
| Concurrent seat booking | PostgreSQL `select_for_update()` + `transaction.atomic()` |
| Duplicate/retried requests | Per-user idempotency keys |
| Data integrity | Database constraints as a final safety net, not just app-level checks |
| Durable async processing | Celery + a persistent `WorkflowJob` model, not just in-memory task state |
| Real-time updates | Django Channels + a Redis-backed channel layer, safe across multiple server processes |
| Cache consistency | One consolidated invalidation + broadcast point per state change, not repeated per call site |
| Expiring bookings | Celery Beat + scheduled workflow cleanup |
| Fuzzy event search | PostgreSQL `pg_trgm` + a GIN index — one indexed query, no Python-side scan |
| AI-driven mutations | LangGraph pauses the graph; nothing writes until a human explicitly confirms |
| Knowledge retrieval | `pgvector` similarity search over chunked, embedded documents |
| Observability | Structured logs, per-call token/cost tracking, admin analytics |

---

## Key Workflows

### Concurrent Booking

```text
Request → Idempotency check → transaction.atomic() → select_for_update()
   → Create PENDING booking → Payment → CONFIRMED
   → Invalidate cache + broadcast WebSocket update → WorkflowJob (Celery)
```

1. `POST /api/bookings/` — authenticated, validated, throttled.
2. `BookingService` checks idempotency, then re-checks it again inside `transaction.atomic()`.
3. The seat row is locked with `select_for_update()` so no two concurrent requests can claim it.
4. A `PENDING` booking is created with an expiry timestamp, and a `WorkflowJob` is scheduled to expire it if unconfirmed.
5. On successful payment the booking becomes `CONFIRMED`; on any seat-affecting change, the cache is invalidated and a WebSocket update is broadcast from one consolidated place, so neither can be forgotten independently.

### AI Assistant

```text
User message → LangChain tool-calling loop → (read tools: cached service layer)
   → (write intent: booking / cancel / payment retry)
      → LangGraph pauses the graph, awaiting confirmation
         → Human clicks confirm → BookingService / PaymentService → Database
```

1. The client opens a streaming chat session (`POST /api/ai-assistant/chat/stream/`, Server-Sent Events).
2. The assistant runs a LangChain tool-calling loop against `gpt-4o-mini`, with tools for searching events, checking seats, querying the knowledge base, and reading bookings — all through the same cached service layer the REST API uses.
3. Anything destructive never executes directly from a tool call. It pauses a dedicated LangGraph graph, and only a separate, explicit confirm action ever resumes it and calls into `BookingService`/`PaymentService`. Unconfirmed drafts and their underlying LangGraph checkpoints both expire automatically via scheduled Celery cleanup.
4. Every OpenAI call — chat and embeddings alike — is logged with token counts and cost-relevant metadata to `UsageLog`, visible in a custom admin dashboard.

---

## ⚙️ Engineering Decisions & Trade-offs

Real problems found during development, and the design change that fixed them — not just a list of technologies used.

* **Cache/WebSocket consistency** — centralized seat-release side effects into one place (`Booking.release_seats()`) instead of repeating both calls at every call site that can release a seat. A real bug (two call sites silently missing the cache invalidation) surfaced and was fixed by making this the single owner of that side effect.
* **Payment retry targeting** — added `PendingPaymentRetry` as a marker row after testing surfaced a real bug: guessing "the most recent FAILED booking" silently resolved to the wrong one once a user had more than one failed booking at once.
* **Fuzzy search performance** — replaced an in-Python `rapidfuzz` scan with a single indexed Postgres query (`pg_trgm` + GIN + `TrigramWordSimilarity`). Removes a full-table scan on every AI tool call and drops an external dependency.
* **AI mutation safety** — destructive AI actions stay behind an explicit human-confirmed draft (`Pending*` models + LangGraph's `interrupt()`) rather than letting the model call booking/cancellation/payment mutations directly.
* **Settings isolation bug** — `core/settings/__init__.py` used to re-export dev settings (`from .dev import *`), which meant simply importing `core.settings.prod` silently imported dev settings too, in-place-mutating a list shared with `base.py` and leaking dev-only apps into production. Fixed by removing the re-export and switching the mutation to a non-in-place list operation.
* **Fail-fast production config** — `core.settings.prod` raises `ImproperlyConfigured` if `SECRET_KEY` isn't explicitly set, instead of silently inheriting `base.py`'s dev-only fallback key.
* **Static files stay in Docker** — `collectstatic` output is written to a Docker-managed named volume rather than the bind-mounted project directory, so generated build artifacts never land on the host filesystem, while still being shared between the app and Nginx containers.
* **Admin permissions mirror the API** — a `post_save` signal keeps every `ORGANIZER` user's Django Group membership in sync automatically, and `EventAdmin`/`SeatAdmin` add object-level ownership checks on top, so admin access enforces the same rule as the API (`IsAdminOrOrganizer`) instead of a separate, looser one.
* **Multi-instance scaling, verified not just assumed** — tried `docker compose up --scale web=N` directly rather than assuming the stateless design was enough on its own, and found two concrete blockers: a fixed `container_name` (Docker refuses to scale it) and a published host port (only one replica can bind it). Nginx's static `upstream` block was a third, quieter one — it resolves Docker's internal DNS once at startup and never again, so even with multiple replicas running, every request would have kept going to whichever one it saw first. Fixed with an optional Compose overlay for the first two, and a dynamic DNS resolver in `nginx.conf` for the third — confirmed traffic actually distributes across replicas afterward, not just that the containers start.
* **Auto-generated API docs over a hand-maintained collection** — added `drf-spectacular` specifically because the Postman collection had already drifted from reality once (a stale, 404ing endpoint, six missing ones). Most of the app's real endpoints are plain `APIView`s rather than `ModelViewSet`s, which schema generation can't introspect automatically — the first pass surfaced 52 introspection errors across 12 views. Fixed by declaring explicit request/response serializers (several already existed and just weren't wired to the schema; a few genuinely new ones for the AI assistant's ad-hoc dict responses) — down to 0 warnings, verified via the `--fail-on-warn` management command, not just "the page loads."

---

## 🚀 Performance & Scaling

* `select_related`, Redis caching, and targeted database indexes on hot read paths
* Event list/detail responses cached in Redis, shared between the REST API and the AI assistant
* Confirmed seat uniqueness enforced at the database level as a final safety net, on top of application-level locking
* DRF throttling on auth and booking endpoints
* Celery worker concurrency capped explicitly (`--concurrency=2`) after profiling showed the CPU-count default spawning far more full Django processes than local task volume needed
* Production uvicorn runs multiple worker processes safely because no application state (auth, chat history, checkpoint data) lives in a single worker's memory — everything is already externalized to Redis or Postgres

## 📊 Observability

* Structured JSON logging (`python-json-logger`) across booking, payment, cache, and workflow events
* `WorkflowJob` tracks async lifecycle state — `status`, `retry_count`, `last_error`, timestamps, `result`
* Admin APIs expose failed/stuck jobs and manual retry actions
* `UsageLog` records token counts and cost-relevant metadata for every OpenAI call (chat and embeddings), aggregated in a custom admin dashboard by model, day, and call type

---

## ✨ Core Capabilities

Reference detail for each area — see [Backend Design Highlights](#backend-design-highlights) for the scannable version.

<details>
<summary><strong>Authentication & Users</strong></summary>

* JWT-based auth: registration, login, token refresh
* `ADMIN`, `ORGANIZER`, and `USER` roles
* A signal keeps `ORGANIZER` users in sync with an admin Group; object-level ownership checks layered on top, mirroring the API's own permission rule
</details>

<details>
<summary><strong>Event Management</strong></summary>

* Role-aware CRUD (admins: any event; organizers: only their own), enforced identically in the API and Django admin
* `available_seats` annotated live from booking state, never a stored/stale count
* Seat generation from either a manually-typed count or an auto-generated layout from a Venue's Space
</details>

<details>
<summary><strong>Booking System</strong></summary>

* Idempotency keys, transactional re-checks, row-level seat locking
* Simulated payment gateway with retry and expiration handling
* Filtering, pagination, cancellation, throttling on all booking endpoints
</details>

<details>
<summary><strong>Real-Time Seat Availability</strong></summary>

* `ws/events/<event_id>/seats/` — snapshot on connect, live diffs as seats lock/book/release
* Redis-backed Channels layer, verified working across separate worker processes
</details>

<details>
<summary><strong>AI Assistant</strong></summary>

* Streaming SSE chat, LangChain tool-calling loop, LangGraph-gated destructive actions
* A minimal LLM eval harness (`ai_assistant/evals/`) runs real prompts through the actual `chat_stream` code path — not mocks, caught two real intermittent tool-selection bugs during development
* Sidebar quick-action suggestions blend a small deterministic slot (state-driven, always reliable) with AI-generated ones for the rest
</details>

<details>
<summary><strong>Knowledge Base (RAG)</strong></summary>

* Venue/event-scoped or global reference documents, auto-chunked and embedded via a Celery job on save
* `pgvector` similarity search, scoped to the venue/event in context when one is selected
</details>

<details>
<summary><strong>Venues & Spaces</strong></summary>

* Physical location + seat layout modeling (rows/columns or general admission)
* Chained venue → space dropdowns in Django admin, client-side, no third-party dependency
</details>

<details>
<summary><strong>Workflow Monitoring & Recovery</strong></summary>

* Admin endpoints for workflow listing, failed/stuck job inspection, manual retries
* Scheduled cleanup for both expired action drafts and their underlying LangGraph checkpoint data
</details>

<details>
<summary><strong>Deployment Configuration</strong></summary>

* A single `DJANGO_SETTINGS_MODULE` variable controls both which Django settings load and which server `entrypoint.sh` starts
* Nginx reverse-proxies HTTP/WebSocket traffic and serves static files directly in production; not started at all in development (Docker Compose profile-gated)
* Static files live in a Docker-managed volume, isolated from the host filesystem
</details>

---

## 🧪 Testing & Evals

Two separate things, run separately — deterministic unit/integration tests for the transactional backend, and behavioral evals for the LLM-driven assistant (an LLM's correctness can't be asserted the same way a plain function's can — the same prompt at `temperature=0` can still occasionally take a different tool-calling path).

A concurrency test fires 10 simultaneous booking requests for the same seat via real threads and verifies exactly one reaches `CONFIRMED` — checked against the database state directly, not just response codes. It runs in its own `TransactionTestCase`, since its threads open real DB connections that a plain `TestCase`'s rollback-per-test isolation can't clean up after.

The full suite also runs in CI via GitHub Actions on every push to `main` and every pull request (Postgres + Redis service containers, same test command as below), and can be triggered manually from the Actions tab.

```bash
# Run the full Django test suite
docker compose exec web python manage.py test

# Run tests for a single app
docker compose exec web python manage.py test bookings
docker compose exec web python manage.py test payments
docker compose exec web python manage.py test events
docker compose exec web python manage.py test workflows

# Run the AI assistant's eval suite against a real user account
# (needs OPENAI_API_KEY set — these are real API calls, not mocks)
docker compose exec web python manage.py run_evals --username <username>
docker compose exec web python manage.py run_evals --username <username> --suite discovery
docker compose exec web python manage.py run_evals --username <username> --suite booking
docker compose exec web python manage.py run_evals --username <username> --suite knowledge

# Regenerate knowledge-base chunks/embeddings (normally automatic via a
# signal + Celery job on save; useful after bulk-loading documents or
# changing the embedding model)
docker compose exec web python manage.py generate_knowledge_embeddings
docker compose exec web python manage.py generate_knowledge_embeddings --document-id <id>
```

Test suites cover bookings, payments, events, venues, knowledge, and workflows, with growing coverage for the AI assistant — the AI-calling parts of the knowledge service (embedding/search) are deliberately left to the eval harness instead, since they need a real `OPENAI_API_KEY` and aren't deterministic unit-test material.

---

## 📁 Project Structure

```text
eventops-backend/
├── ai_assistant/         # LangChain/LangGraph agent, tools, evals
│   ├── langchain_tools/
│   ├── langgraph_flows/
│   ├── actions/          # confirm/dismiss handlers for staged AI drafts
│   └── evals/
├── bookings/              # Booking creation, cancellation, payment retry
├── events/                 # Events, seats, caching, WebSocket consumer
├── payments/               # Simulated payment gateway
├── workflows/               # WorkflowJob model + Celery tasks
├── knowledge/                # RAG documents, chunking, embeddings
├── venues/                    # Venue/Space seat-layout modeling
├── users/                      # Auth, roles
└── core/
    └── settings/                # base / dev / prod split
```

Each domain app follows the same internal shape: `models.py` for schema, `services.py` for business logic, `views.py`/`serializers.py` for the API surface — business logic lives in the service layer, not in views, so the REST API and the AI assistant's tools can call the exact same functions.

---

## ⚙️ Setup & Running Locally

### 1. Clone

```bash
git clone https://github.com/Aritro1998/eventops-backend.git
cd eventops-backend
```

### 2. Configure environment

```bash
cp .env.example .env
```

Then set `OPENAI_API_KEY` (required for the AI assistant and knowledge base). Everything else in `.env.example` has a working default for local development.

### 3. Development vs Production

The project runs in two modes from the same codebase and the same Docker images — which one you get is controlled entirely by `.env`.

**Development** (default):

```env
DJANGO_SETTINGS_MODULE=core.settings.dev
DJANGO_WS_HOST=localhost:8000
```

```bash
docker compose up -d --build
```

Starts Postgres (`pgvector` + `pg_trgm` enabled), Redis, the Django app (`web`), the Gradio demo chat (`gradio`), and Celery worker + beat. `web` runs `manage.py runserver` with autoreload and serves its own static files. `nginx` is **not** started in this mode.

**Production**:

```env
DJANGO_SETTINGS_MODULE=core.settings.prod
SECRET_KEY=<a real random secret — the app refuses to start without one>
ALLOWED_HOSTS=your-domain.com
DJANGO_WS_HOST=your-domain.com
```

```bash
docker compose --profile prod up -d --build
```

`--profile prod` is what additionally starts `nginx`. `entrypoint.sh` runs `collectstatic` and starts the app under uvicorn with multiple worker processes; `nginx` serves collected static files directly and reverse-proxies HTTP and WebSocket traffic to `web`. `DEBUG` is hardcoded off in `core.settings.prod` regardless of any environment value.

Switching between modes only needs a container recreate (`docker compose up -d --force-recreate web`), never a rebuild — rebuilds (`--build`) are only needed after changing `requirements.txt`, the `Dockerfile`, or `entrypoint.sh`.

**Production, scaled to multiple `web` instances**:

```bash
docker compose -f docker-compose.yml -f docker-compose.scale.yml --profile prod up -d --build --scale web=3
```

`docker-compose.scale.yml` is an optional overlay, only applied when explicitly passed with `-f` — it removes `web`'s fixed container name and published port, both of which block running more than one replica. `nginx.conf` re-resolves Docker's internal DNS on every request (`resolver 127.0.0.11 valid=10s;`) rather than caching one replica's address at startup, so traffic actually gets distributed across all of them instead of pinning to whichever one nginx happened to see first. No application code is aware of how many replicas exist — auth, chat history, and LangGraph checkpoint state all live in Redis/Postgres rather than a worker's own memory, which is what makes this safe to do without any other change.

Verified directly: requests distribute across replicas (confirmed via per-container request logs), and the seat-picker WebSocket still upgrades and streams correctly through nginx with multiple replicas running behind it.

### 4. Create superuser (optional)

```bash
docker compose exec web python manage.py createsuperuser
```

### 5. Open

**Development:** app/admin at `http://localhost:8000`, AI assistant demo at `http://localhost:7860`, interactive API docs at `http://localhost:8000/api/docs/` (or `/api/redoc/` for the alternate layout).

**Production:** app/admin through Nginx at `http://localhost` (port 80), AI assistant demo at `http://localhost:7860`, API docs at `http://localhost/api/docs/`.

### 6. Postman

Import `EventOps.postman_collection.json` and `event_ops.postman_environment.json`, set the environment to `event_ops`, and adjust the base URL if needed. The environment includes starter values for `admin`/`organizer`/`user` credentials and common IDs.

---

## 🔜 Future Enhancements

* Concurrent (parallelized) LLM tool calls, currently constrained to one call at a time
* Semantic response caching for the AI assistant
* A warm-up request against the OpenAI client at worker startup, to absorb first-request connection setup cost
* Real cost tracking on top of the existing token-usage data
* Deeper automated test coverage for the venues and knowledge apps
* A real load balancer (health checks, connection draining) in front of scaled `web` instances — the current DNS round-robin approach has neither

---
