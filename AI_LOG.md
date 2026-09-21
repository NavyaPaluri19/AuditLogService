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

## Phase 4 — Redaction, archival, and export

**Tool:** Claude (Anthropic)

**What I prompted for:**
- Alembic migrations 002 (add `redacted_fields` JSON column) and 003 (add `is_archived`, `archived_at` columns)
- Update ORM model and Pydantic schemas to include the three new columns
- `redaction_service.py` — field-level redaction that never touches `payload_hash`, `entry_hash`, or `chain_hash`
- `retention_service.py` — soft-archive (sets `is_archived`/`archived_at`; no physical deletion ever)
- `export_service.py` — streaming JSON and CSV export using async generators and cursor pagination
- `PATCH /audit/events/{id}/redact` and `POST /audit/events/{id}/archive` routes
- `GET /audit/export?format=json|csv&include_archived=true|false` route
- `tests/test_redaction.py` — 14 tests covering redaction and archival behaviour
- `tests/test_export.py` — 13 tests covering JSON and CSV export

**What AI produced:**
- `alembic/versions/002_add_redaction.py` — adds `redacted_fields` JSON column (nullable, null until first redaction)
- `alembic/versions/003_add_archival.py` — adds `is_archived` boolean and `archived_at` timestamptz
- Updated `app/models/audit_entry.py` — `Boolean` added to imports; three new mapped columns with comments
- Updated `app/schemas/event.py` — `redacted_fields`, `is_archived`, `archived_at` added to `EventResponse`; `RedactRequest` and `ArchiveResponse` added
- `app/services/redaction_service.py` — `redact_fields()`: sets payload values to None, records SHA-256 proof in `redacted_fields`, skips missing/null fields idempotently, raises `ValueError` on archived entry
- `app/services/retention_service.py` — `archive_entry()`: sets `is_archived=True` and `archived_at=now(UTC)`, idempotent on already-archived entries
- `app/services/export_service.py` — `export_json()` and `export_csv()` as async generators, paginated in 500-row batches to avoid buffering large result sets
- `app/api/routes/redaction.py` — PATCH redact (200/404/409) and POST archive (200/404) endpoints
- `app/api/routes/export.py` — GET export with `format` and `include_archived` query params; FastAPI `StreamingResponse`
- Updated `app/main.py` — redaction and export routers registered
- `tests/test_redaction.py` — 14 test cases: payload nulling, multi-field, hash immutability, chain validity post-redact, 404, 409 on archived, idempotency, 422 on empty fields list; archive sets flag, persists, idempotent, 404, chain valid after archive
- `tests/test_export.py` — 13 test cases: empty, all events, field coverage, exclude/include archived, redacted_fields in export, content-type, 422 on invalid format; CSV header, JSON-encoded payload column, content-type

**What I decided / changed:**
- Confirmed soft-archive over physical delete: deleting a row would orphan the next row's `previous_hash` reference and break chain verification
- Confirmed chain-safe redaction: `payload_hash` is the immutable anchor — `entry_hash` and `chain_hash` derive from it, not from the raw payload, so nulling payload fields never invalidates the chain
- `redacted_fields` stores SHA-256 proofs so verifiers can prove what a redacted field originally held without revealing its value
- Export uses async generators + cursor pagination to stream arbitrarily large result sets without server-side buffering

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
