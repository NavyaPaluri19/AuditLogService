# AuditLogService

A tamper-evident, append-only audit log service with hash chain integrity, field-level redaction, and configurable retention policies.

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

This starts a single container — PostgreSQL 16 on port `5432`.

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
