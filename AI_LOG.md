# AI Interaction Log

Running log of AI assistance used during development. Entries are grouped by feature/phase rather than by commit, since changes are committed incrementally. Each entry records what was prompted, what the AI produced, and what I changed or decided myself.

---

## Phase 1 — Project scaffold

**Tool:** Claude (Anthropic)

**What I prompted for:**
- Generate a FastAPI + PostgreSQL project skeleton for a tamper-evident audit log service
- Hash chain integrity using SHA-256
- Field-level redaction that preserves the chain
- Async SQLAlchemy 2.0, Pydantic v2, Alembic migrations
- Cursor-based pagination, soft-delete retention, JSON/CSV export

**What AI produced:**
- Full directory structure and all skeleton files (stubs with `pass` or `TODO` bodies)
- `app/core/hashing.py` — `compute_payload_hash`, `compute_entry_hash`, `compute_chain_hash`, `verify_entry`
- `app/db/session.py` — async engine + session factory
- `app/main.py` — FastAPI app with lifespan and router registration
- `tests/test_hashing.py` — unit tests covering chain continuity, tamper detection, redaction-safe hash
- `DECISIONS.md` — Architecture Decision Records (ADR-001 through ADR-010)
- `docker-compose.yml`, `Dockerfile`, `.env.example`, `pytest.ini`, `alembic.ini`

**What I decided / changed:**
- The core design choice — hashing `payload_hash` instead of raw payload JSON — was my own decision to make redaction chain-safe; AI implemented what I specified
- Removed the `app` service from `docker-compose.yml`; the app runs from the terminal, only the DB runs in Docker
- Reviewed and kept all ADRs as accurate representations of the design rationale

---

## Phase 1 — Dependencies and README

**Tool:** Claude (Anthropic)

**What I prompted for:**
- Local development instructions in README.md (venv setup, DB start, app run, test run, Alembic)
- Fix Python 3.14 compatibility in `requirements.txt` (asyncpg and pydantic-core had no 3.14 wheels at pinned versions)

**What AI produced:**
- Full `README.md` with prerequisites table, step-by-step local dev guide, environment variable reference, API overview
- Updated `requirements.txt` — loosened `asyncpg` to `>=0.30.0` and `pydantic` to `>=2.10.0` for Python 3.14 wheel availability

**What I decided / changed:**
- Chose Python 3.14 as the primary version for the project
- Confirmed DB-only-in-Docker architecture (no Docker for the app service)

---

## Phase 1 — Attestation

**Tool:** Claude (Anthropic)

**What I prompted for:**
- `ATTESTATION.md` declaring authorship, AI usage disclosure, and integrity statement

**What AI produced:**
- Draft `ATTESTATION.md` with authorship, AI assistance disclosure breakdown, and integrity clause

**What I decided / changed:**
- Reviewed and confirmed all content accurately reflects the level of AI involvement

---

---

## Phase 2 — ORM model, schemas, chain service, Alembic

**Tool:** Claude (Anthropic)

**What I prompted for:**
- SQLAlchemy ORM model for `audit_entries` table — core chain fields only (no archival/redaction yet, incremental schema)
- Pydantic v2 request/response schemas for Phase 2 endpoints
- Real `ChainService.append()` with `SELECT ... FOR UPDATE` tail lock and `verify_chain()` walking the full chain
- Async-compatible `alembic/env.py` and initial migration `001_initial_schema.py`

**What AI produced:**
- `app/models/audit_entry.py` — ORM model with 9 core columns, indexes, and comments documenting which migration each future column belongs to
- `app/schemas/event.py` — `EventCreate`, `EventResponse`, `EventListResponse`, `VerifyResponse`
- `app/services/chain_service.py` — `append()` with FOR UPDATE lock + UTC datetime normalisation for SQLite/PostgreSQL compat; `verify_chain()` recomputing both `entry_hash` and `chain_hash` per entry
- `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/001_initial_schema.py`
- Import wiring in `app/main.py` so `Base.metadata` picks up the model

**What I decided / changed:**
- Pushed back on including `is_archived`/`archived_at`/`redacted_fields` in the initial schema — those belong to Phase 4 (incremental development principle)
- Confirmed service-layer sequence_number assignment (under the FOR UPDATE lock) rather than a PostgreSQL SEQUENCE — equivalent correctness, simpler cross-DB compatibility

---

## Phase 3 — API routes (events + verify)

**Tool:** Claude (Anthropic)

**What I prompted for:**
- `POST /audit/events`, `GET /audit/events`, `GET /audit/events/{id}` — events router
- `GET /audit/verify` — verify router
- Cursor-based pagination via `query_service`
- `tests/conftest.py` with in-memory SQLite fixture and FastAPI dependency override
- `tests/test_api.py` covering all Phase 3 endpoints

**What AI produced:**
- `app/services/query_service.py` — `list_events()` (keyset pagination, fetch limit+1 trick) and `get_event_by_id()`
- `app/api/routes/events.py` — three endpoints wired to chain_service and query_service via `Depends(get_db)`
- `app/api/routes/verify.py` — single GET route delegating to `chain_service.verify_chain()`
- `app/main.py` updated — events and verify routers registered; redaction/export commented out for Phase 4
- `tests/conftest.py` — `db_engine` and `client` fixtures; `get_db` dependency overridden with per-request SQLite sessions
- `tests/test_api.py` — 27 test cases across health, append, get-by-id, list (pagination), and verify

**What I decided / changed:**
- `after_sequence` instruction: update DECISIONS.md and README after each phase — my standing rule
- Confirmed keyset cursor uses `sequence_number` (not a UUID or opaque token) — transparent, debuggable, and directly usable for chain ordering

---

<!-- ============================================================
     TEMPLATE — copy and fill in for each future phase/feature
     ============================================================

## Phase N — <Feature name>

**Tool:** Claude (Anthropic)

**What I prompted for:**
- <What you asked the AI to build or explain>

**What AI produced:**
- <Files generated, functions written, tests added>

**What I decided / changed:**
- <Your own choices, things you rewrote, design calls you made>

-->
