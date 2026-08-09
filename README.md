# 🚀 EventOps Backend

A **production-style backend system** for event management and seat booking, combining **concurrency-safe transactional workflows** with an **LLM-powered AI assistant**, **real-time seat availability**, and a **RAG-based knowledge layer** — designed to demonstrate backend engineering beyond basic CRUD.

Built to showcase backend engineering beyond basic CRUD — **concurrency control**, **service-layer design**, **async workflow reliability**, and **production-style operational thinking**, applied to real-world challenges like:

* preventing **double booking under high concurrency**
* handling **distributed service dependencies** (Celery, Redis, WebSockets)
* designing **extensible systems for async processing**
* building a **tool-calling AI agent** on top of real transactional business logic, not a toy wrapper around an LLM, reading/writing through the same service layer as the REST API
* answering natural-language questions grounded in real data via **retrieval-augmented generation**
* per-call **cost/usage observability** for the LLM integration itself, not just the transactional core

---

## 🧠 System Architecture Overview

```text
Client (REST) ---------- Client (WebSocket) ---------- Client (AI Chat)
     |                          |                            |
     v                          v                            v
DRF API Layer          Channels Consumer            Async Chat View (SSE)
     |                          |                            |
     v                          |                            v
Service Layer <------------------------------------  AI Tool-Calling Loop
     |                                                        |
     +--> PostgreSQL (events, seats, bookings,        (search events, check
     |     workflow jobs, knowledge chunks +           seats, draft booking/
     |     pgvector embeddings)                        cancel/payment-retry)
     |                                                        |
     +--> Redis (cache + Celery broker/result          shares the SAME
     |     backend + Channels layer)                    cached read paths
     |                                                   and service layer
     v                                                   as the REST API
WorkflowJob persistence
     |
     v
Celery Workers
     |
     +--> booking expiry handling
     +--> confirmation email delivery
     +--> knowledge document chunking + embedding generation
```

Background workflows are coordinated through a persistent `WorkflowJob` model and executed by `Celery` workers for async follow-up tasks such as booking expiry handling, confirmation delivery, and knowledge-base embedding generation. Live seat state is pushed to connected clients over WebSockets (Django Channels), and the AI assistant reads through the exact same cached service layer the REST API uses — there's one source of truth for event/seat data, not two implementations that can drift apart.

### Booking Flow

1. The client sends a booking request to `POST /api/bookings/`.
2. The API layer authenticates the user, validates input, and applies throttling.
3. `BookingService` performs an idempotency check and re-checks it again inside `transaction.atomic()`.
4. The seat row is locked with `select_for_update()` to prevent concurrent seat claims.
5. A `PENDING` booking is created in PostgreSQL with an expiry timestamp.
6. A `WorkflowJob` is created to expire the booking later if it is not confirmed in time.
7. Payment is processed in the request flow; on success the booking becomes `CONFIRMED`.
8. A confirmation workflow job is queued on state transition, and Celery workers handle email delivery and expiry processing in the background.
9. Every state change that affects seat availability — new hold, confirmation, cancellation, expiry — invalidates the shared event cache **and** broadcasts a live seat update over WebSockets in one place (`Booking.release_seats()` / the payment-success path), so no call site can forget one half of the pair.

This architecture is designed to provide:

* concurrency safety for seat allocation
* retry-safe booking requests via idempotency
* durable async workflow tracking beyond the task queue alone
* cache consistency and real-time UI consistency after every state change

### AI Assistant Flow

1. The client opens a streaming chat session at `POST /api/ai-assistant/chat/stream/` (Server-Sent Events over an async Django view, using the async ORM directly — no `sync_to_async` wrapping the whole request).
2. The assistant runs a LangChain tool-calling loop (`gpt-4o-mini` via `ChatOpenAI`, `parallel_tool_calls=False`, up to 5 tool calls per turn) with tools for searching events, checking seat availability, querying the RAG knowledge base, and reading a user's bookings.
3. Anything destructive — creating a booking, cancelling one, retrying a payment — never executes directly from a tool call. Each one pauses its own dedicated LangGraph graph (`ai_assistant/langgraph_flows/`) via `interrupt()`, with a lightweight marker row (`PendingBookingThread` / `PendingBookingCancellation` / `PendingPaymentRetry`) pointing at whichever paused thread is currently relevant — the actual draft state lives in the graph's own checkpoint, not the row. Cancellation runs an additional two-step "are you sure" confirmation inside its graph before anything is touched. Only a separate, explicit confirm endpoint ever resumes a graph or calls into `BookingService`/`PaymentService`. Unconfirmed drafts expire automatically via the same Celery/`WorkflowJob` cleanup pattern used for booking holds.
4. Every read tool (event search, seat availability) goes through the **same cached service functions** the REST API uses (`events/caching.py`), so the AI and the API can never show inconsistent data or double the cache-warming cost.
5. Every OpenAI call is logged to `UsageLog` — prompt/completion/system-prompt token counts (via `tiktoken`) and which tool (if any) was invoked — surfaced in a custom Django admin analytics view.

---

## 🧱 Tech Stack

* **Backend:** Django 5.x, Django REST Framework
* **Auth:** JWT (djangorestframework-simplejwt)
* **Database:** PostgreSQL + `pgvector` (embedding storage/similarity search)
* **Cache / Queue:** Redis
* **Async tasks:** Celery (worker + beat)
* **Real-time:** Django Channels + Daphne (ASGI, WebSockets)
* **AI:** OpenAI API (chat completions + embeddings), streamed via native async Django views and the async ORM, `tiktoken` for local token counting
* **Frontend (demo client):** Gradio chat UI
* **Containerization:** Docker & Docker Compose

---

## ⚙️ Key Engineering Decisions

* Used `select_for_update()` for row-level locking to prevent double booking under concurrency
* Implemented per-user idempotency keys so booking creation is safe to retry
* Added a `WorkflowJob` model for persistent async job tracking instead of relying on Celery state alone
* Used Redis for caching, the Celery broker/result backend, **and** the Channels layer for WebSockets
* Combined database constraints, targeted indexes, and a composite workflow index for data integrity and query efficiency
* Consolidated cache invalidation and WebSocket broadcasting into `Booking.release_seats()` itself, instead of repeating both calls at every call site that can release a seat (cancellation, expiry, payment failure) — a real bug (two call sites silently missing invalidation) surfaced and was fixed by making this the single owner of that side effect
* Added `PendingPaymentRetry` as a marker row (mirroring `PendingBookingThread`), after testing surfaced a real bug: guessing "the most recent FAILED booking" for a payment retry silently resolved to the wrong one once a user had more than one failed booking at once
* Unified the REST API and the AI assistant's event/seat reads onto one cached service layer (`events/caching.py`) rather than the AI hitting the database uncached on every tool call
* Kept destructive AI actions behind an explicit human-confirmed draft (`Pending*` models) instead of letting the model call booking/cancellation/payment mutations directly
* Layered Django admin's own permission system with the app's business `role` field: a `post_save` signal keeps every `ORGANIZER` user's Group membership in sync automatically, and `EventAdmin`/`SeatAdmin` add object-level ownership checks on top — so an organizer with admin access can only ever touch events they created, matching the same rule already enforced on the API (`IsAdminOrOrganizer`)
* Per-call OpenAI token/cost visibility via a dedicated `UsageLog` model instead of trusting provider dashboards alone

## 🚀 Performance & Scaling Highlights

* Optimized hot paths with `select_related`, Redis caching, and targeted database indexes
* Cached event list and detail responses to reduce repeated read load on PostgreSQL — shared between the REST API and the AI assistant's tools
* Added a concurrency test with parallel booking attempts to verify that at most one booking reaches `CONFIRMED` for the same seat
* Enforced uniqueness for confirmed seat bookings at the database level as a final safety net
* Applied DRF throttling on auth and booking endpoints to reduce abuse risk
* Fuzzy event-name search (`rapidfuzz`) so the AI assistant can resolve a misspelled or partial event name to the right event
* Capped Celery worker concurrency explicitly (`--concurrency=2`) after profiling showed the CPU-count default (10 workers) spawning far more full Django-loaded processes than local task volume ever needed

## 📊 Observability

* Structured JSON logging is configured through `python-json-logger`
* Key workflows emit logs for booking creation, payment outcomes, cache invalidation, workflow execution, retries, and failures
* `WorkflowJob` tracks async lifecycle state including `status`, `retry_count`, `last_error`, `started_at`, `completed_at`, and `result`
* Admin APIs expose failed jobs, stuck jobs, and retry actions for operational recovery
* `UsageLog` records prompt/completion/system-prompt tokens and the tool invoked for every OpenAI API call — chat completions and the knowledge base's embedding calls alike; a custom Django admin changelist view aggregates this into per-model and per-day token totals plus system-prompt-overhead percentage, scoped to whatever filter is active, and labels each row "Chat" or "Embedding" so spend on the two is easy to tell apart

---

## ✨ Core Capabilities

### ✅ Authentication & Users

* JWT-based auth with registration, login, and token refresh endpoints
* `users.User` supports `ADMIN`, `ORGANIZER`, and `USER` roles
* Registration normalizes email and enforces Django password validation
* A signal keeps every `ORGANIZER` user in sync with an "Organizers" Django Group (base admin permissions), layered under object-level ownership checks in `EventAdmin`/`SeatAdmin` — organizers get admin access to only the events they created, mirroring the API's own permission rule

### ✅ Event Management

* Event CRUD with role-aware write permissions (admins: any event; organizers: only their own, enforced identically in the API and the Django admin)
* `available_seats` is exposed in read responses, annotated live from booking state — never a stored/stale count
* Event list and detail responses are cached in Redis for 5 minutes, shared between the REST API and the AI assistant
* Event writes invalidate cache entries automatically
* Seat generation and seat-count updates are handled safely inside transactional logic, driven by either a manually-typed seat count or an auto-generated layout from a Venue's Space
* Database constraints enforce event and seat integrity

### ✅ Booking System

* Booking creation uses idempotency keys, transactional re-checks, and row-level seat locking
* Confirmed seat uniqueness is enforced at the database level
* Payment flow supports retries and expiration handling (simulated gateway — see `payments/services.py`)
* Booking expiry and confirmation follow-up actions are managed through Celery-backed workflow jobs
* Booking endpoints support filtering, pagination, cancellation, and abuse protection via throttling
* Every seat-affecting state change invalidates the cache and broadcasts a live WebSocket update from one consolidated place (`Booking.release_seats()`)

### ✅ Real-Time Seat Availability (WebSockets)

* `ws/events/<event_id>/seats/` pushes live "available" / "locked" / "booked" status to every connected client watching an event's seat map
* A newly-connecting client gets an immediate snapshot of current seat state, then live diffs as other users lock/book/release seats
* Backed by Django Channels + a Redis-backed channel layer, so it works across multiple server processes, not just one

### ✅ AI Assistant

* Streaming chat (Server-Sent Events) over an async Django view using OpenAI's chat completions API with tool calling
* Tools for searching events (fuzzy name matching), checking seat availability, reading a user's bookings, and answering knowledge-base questions
* Destructive actions (booking, cancellation, payment retry) always go through a human-confirmed draft before anything is written — the model can propose, never directly execute
* Reads share the same Redis-cached service layer as the REST API — no separate, uncached data path for the AI
* Per-call OpenAI usage (tokens, system-prompt overhead, tool invoked) logged to `UsageLog` with an admin analytics dashboard
* A minimal LLM eval harness (`ai_assistant/evals/`) runs real prompts through the actual `chat_stream` code path and asserts on tool calls and response content — not mocks, the same reliability-testing approach used to catch two real intermittent tool-selection bugs during development
* The demo chat's quick-action suggestions blend a small, state-driven deterministic slot (e.g. "Book seats for X" once an event is selected) with AI-generated suggestions for the rest — the model is told which slots are already decided so it doesn't duplicate them, and is instructed to name real events from the conversation instead of generic placeholders

### ✅ Knowledge Base (RAG)

* `KnowledgeDocument`/`KnowledgeChunk` models hold venue- and/or event-scoped reference content (policies, FAQs, venue info), optionally global
* Documents are chunked and embedded (OpenAI embeddings, `pgvector` for storage and similarity search) automatically via a Celery workflow job whenever a document is created or edited
* The AI assistant's knowledge tool retrieves the most relevant chunks for a user's question, scoped to the venue/event in context when one is selected

### ✅ Venues & Spaces

* `Venue`/`Space` models describe a physical location and its seat layout (rows/columns or general admission, with a configurable label style)
* An Event can attach to a Space to auto-generate its seats from that layout, or stay fully custom with a manually-typed seat count
* Chained venue → space dropdowns in the Django admin (client-side filtering, no third-party dependency)

### ✅ Workflow Monitoring & Recovery

* Admin endpoints support workflow listing, failed-job inspection, stuck-job detection, and manual retries
* Workflow jobs can be filtered by type, status, and creation date
* Retry operations reset workflow state before requeueing

### ✅ Infrastructure & Tooling

* Docker Compose includes `db` (Postgres + pgvector), `redis`, `web`, `gradio` (demo chat frontend), `celery`, and `celery-beat`
* Redis powers caching, Celery infrastructure, and the Channels layer
* Structured JSON logs are emitted for key operational paths
* Test suites cover bookings, payments, events, and workflows in depth; venues/knowledge/ai_assistant currently have thinner coverage (see Testing & Evals below) — the AI assistant instead relies on its eval harness for behavioral coverage
* Postman collection and environment files are included for quick API exploration

---

## 🧪 Testing & Evals

Two separate things, run separately — deterministic unit/integration tests for the transactional backend, and behavioral evals for the LLM-driven assistant (an LLM's correctness can't be asserted the same way a plain function's can — the same prompt at `temperature=0` can still occasionally take a different tool-calling path).

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

# Required for the AI assistant and knowledge-base embeddings
OPENAI_API_KEY=sk-...

# Optional — defaults shown
OPENAI_CHAT_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
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

This starts Postgres (with the `pgvector` extension image), Redis, the Django app (`web`), the Gradio demo chat frontend (`gradio`), and the Celery worker + beat scheduler. `python manage.py migrate` (including enabling the `pgvector` Postgres extension) runs automatically on `web`'s startup — no manual database setup needed.

If you install dependencies outside Docker, make sure `python-json-logger` from `requirements.txt` is installed so Django can load the JSON log formatter, and that Postgres has the `pgvector` extension available (the `pgvector/pgvector:pg15` image already includes it).

`entrypoint.sh` handles:
* waiting for Postgres on `db:5432`
* `python manage.py migrate`
* `python manage.py runserver 0.0.0.0:8000`

### Settings Modules

The project uses split Django settings:

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
* AI Assistant demo chat (Gradio): `http://localhost:7860`

6. Import Postman collection and environment:
* Open Postman and import `EventOps.postman_collection.json` and `event_ops.postman_environment.json`
* Set the environment to `event_ops` and update the base URL to `http://localhost:8000` if needed.
* The bundled environment also includes starter values for `admin`, `organizer`, `user`, booking IDs, and workflow-job filters that you can adjust for your local data.

---

## 🔜 Future Enhancements

* Trigram/vector-based fuzzy event search (`pg_trgm` or embedding-based) to replace the current in-memory `rapidfuzz` scan as the event catalog grows
* Concurrent (parallelized) LLM tool calls, currently constrained to one call at a time (`parallel_tool_calls=False`)
* Semantic response caching for the AI assistant
* Real cost tracking (a pricing table on top of the existing token-usage data — OpenAI's API doesn't return cost directly)
* Deeper automated test coverage for the venues, knowledge, and ai_assistant apps (currently thinner than bookings/payments/events/workflows)
* Horizontal scaling support
* API docs and endpoint discovery

---
