# AuditLogService

A tamper-evident, append-only audit log service with hash chain integrity, field-level redaction, and configurable retention policies.

Built with **FastAPI · PostgreSQL 16 · SQLAlchemy 2.0 async · Python 3.14**

---

## Local Development

### Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.14 | Run the app and tests directly from the terminal |
| Docker Desktop | 4.x+ | Only needed to run PostgreSQL |
| Docker Compose | v2 (plugin) | Bundled with Docker Desktop |

---

### 1. Clone and configure environment

```bash
git clone <repo-url>
cd AuditLogService

# Copy the example env file — defaults work out of the box
cp .env.example .env
```

---

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

> Optional: use a virtual environment for isolation
> ```bash
> python -m venv .venv
> # Windows: .venv\Scripts\activate
> # macOS / Linux: source .venv/bin/activate
> pip install -r requirements.txt
> ```

---

### 3. Start only the database

Only PostgreSQL needs to run in Docker. The app runs directly in your terminal.

```bash
docker compose up db
```

This starts a single container — PostgreSQL 16 on port `5432`. That's it.

---

### 4. Run the app from your terminal

```bash
python -m uvicorn app.main:app --reload
```

- Hot-reloads on every file save — no Docker rebuild needed
- Debugger attaches natively
- Logs print directly in the terminal

**Verify it's running:**

```bash
curl http://localhost:8000/health
# → {"status":"ok","version":"0.1.0"}
```

Interactive API docs (Swagger UI):

```
http://localhost:8000/docs
```

---

### 5. Stop the database

```bash
# Stop Postgres but keep the volume (data preserved)
docker compose down

# Stop and wipe the database volume (clean slate)
docker compose down -v
```

---

### 6. Run tests

#### Hashing tests — no services needed at all

```bash
pytest tests/test_hashing.py -v
```

Pure Python, instant, no DB required.

#### Full test suite — uses in-memory SQLite

```bash
pytest -v
```

All tests use `aiosqlite` (SQLite in-memory) by default — no running Postgres needed.

> **Note:** `SELECT ... FOR UPDATE` (chain lock) is not supported by SQLite. That specific test is skipped locally and covered by the PostgreSQL integration test when running against the live DB.

#### Against live Postgres (integration)

With `docker compose up db` running and the app's `DATABASE_URL` pointing at it:

```bash
pytest -v --integration
```

---

### 7. Run Alembic migrations

```bash
# Generate a new migration after changing a model
python -m alembic revision --autogenerate -m "describe_change"

# Apply pending migrations
python -m alembic upgrade head

# Roll back one migration
python -m alembic downgrade -1
```

> Run these from your terminal (venv activated) while `docker compose up db` is running.

---

### 8. Project structure

```
AuditLogService/
├── app/
│   ├── api/
│   │   └── routes/          # FastAPI route handlers (events, verify, redaction, export)
│   ├── core/
│   │   ├── config.py        # Pydantic settings (reads .env)
│   │   └── hashing.py       # SHA-256 chain functions — foundation of tamper detection
│   ├── db/
│   │   └── session.py       # Async SQLAlchemy engine + session factory
│   ├── models/
│   │   └── audit_entry.py   # SQLAlchemy ORM model — core chain columns
│   ├── schemas/
│   │   └── event.py         # Pydantic v2 request/response schemas
│   ├── services/
│   │   ├── chain_service.py     # append() with FOR UPDATE lock, verify_chain()
│   │   ├── query_service.py     # cursor-based pagination (Phase 3)
│   │   ├── redaction_service.py # field-level redaction (Phase 4)
│   │   ├── export_service.py    # JSON / CSV export (Phase 4)
│   │   └── retention_service.py # soft-delete archival (Phase 4)
│   └── main.py              # FastAPI app + lifespan + router registration
├── alembic/
│   └── versions/
│       └── 001_initial_schema.py  # Core chain columns
├── tests/
│   └── test_hashing.py      # Exhaustive hashing unit tests (run these first)
├── ATTESTATION.md           # Authorship and AI usage declaration
├── AI_LOG.md                # Running log of AI interactions by phase
├── DECISIONS.md             # Architecture Decision Records (ADR-001 → ADR-010)
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── pytest.ini
├── alembic.ini
└── .env.example
```

---

### 12. Implementation status

| Phase | What | Status |
|---|---|---|
| 1 | Scaffold, hashing core, config, Docker, tests | ✅ Complete |
| 2 | ORM model, schemas, ChainService, Alembic migration 001 | ✅ Complete |
| 3 | API routes: POST/GET events, GET verify, cursor pagination, API tests | ✅ Complete |
| 4 | Redaction (migration 002), archival (migration 003), export | 🔲 Next |

---

### 9. Environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://audit:audit_pass@localhost:5432/audit_log` | Points at `localhost:5432` — correct when the app runs in your terminal and DB runs in Docker. |
| `APP_ENV` | `development` | Set to `production` to silence SQL echo logs. |
| `APP_TITLE` | `Audit Log Service` | Shown in Swagger UI. |
| `APP_VERSION` | `0.1.0` | Shown in `/health` and Swagger UI. |

---

### 10. API overview

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/audit/events` | Append a new audit event |
| `GET` | `/audit/events` | List events (cursor pagination: `?after_sequence=<int>`) |
| `GET` | `/audit/events/{id}` | Get a single event |
| `GET` | `/audit/verify` | Verify end-to-end chain integrity |
| `PATCH` | `/audit/events/{id}/redact` | Redact fields (chain-safe) |
| `GET` | `/audit/export` | Export events as JSON or CSV |
| `POST` | `/audit/events/{id}/archive` | Soft-archive an event (never physically deleted) |

---

### 11. Key design decisions

See [`DECISIONS.md`](DECISIONS.md) for the full Architecture Decision Records covering database choice, hash algorithm, pagination strategy, redaction approach, and more.

The most critical design point: **`entry_hash` is computed over `payload_hash` (SHA-256 of the original payload), not over the raw payload JSON.** This means field-level redaction can change the payload without invalidating the hash chain — the chain continues to verify end-to-end after any redaction.
