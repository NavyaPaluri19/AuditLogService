# Architecture Decision Records

Decisions made during design and implementation, including tradeoffs considered.
Referenced during the live panel defense.

---

## ADR-001 — Database: PostgreSQL 16

**Decision:** PostgreSQL 16 (via Docker image `postgres:16-alpine`)

**Alternatives considered:**

| Option | Pros | Cons |
|---|---|---|
| **PostgreSQL** | JSONB indexing, advisory locks, sequences, production-grade, ACID | Heavier than SQLite |
| SQLite | Zero setup, great for tests | No async driver that supports `FOR UPDATE`, no JSONB, not suitable for concurrent writes (chain lock requirement) |
| MySQL / MariaDB | Widely used | No native JSONB, weaker JSON operators, `FOR UPDATE SKIP LOCKED` is less clean |

**Why PostgreSQL wins for this project:**
- `JSONB` column type for `payload` — supports GIN indexes for querying inside JSON payloads without scanning every row
- `SELECT ... FOR UPDATE` row-level locking lets `ChainService.append` safely read the last row and prevent concurrent chain forks in a single atomic block; `sequence_number` is assigned as `last.sequence_number + 1` inside that lock — gap-free and strictly monotonic without needing a separate DB sequence object
- ACID transactions mean write + hash update is truly atomic — no partial chain states
- Industry standard for financial systems; Schwab's own stack almost certainly includes PostgreSQL

**Note:** Tests use SQLite with `aiosqlite` for speed and zero-dependency test runs. This works because the test DB is never written concurrently.

---

## ADR-002 — Async Driver: asyncpg

**Decision:** `asyncpg` as the PostgreSQL driver (not `psycopg2`)

**Why:**
- `asyncpg` is a pure async driver — works natively with Python's `asyncio` and SQLAlchemy's async engine
- `psycopg2` is synchronous; using it with `asyncio` requires thread pool workarounds (`run_in_executor`) that add latency and complexity
- `psycopg3` (async version of psycopg2) is also valid but `asyncpg` has a longer async track record and is the SQLAlchemy async docs' primary example

---

## ADR-003 — ORM: SQLAlchemy 2.0 (async)

**Decision:** SQLAlchemy 2.0 with async engine and `async_sessionmaker`

**Alternatives considered:**

| Option | Pros | Cons |
|---|---|---|
| **SQLAlchemy 2.0 async** | Mature ORM, Alembic migrations, type-safe queries | Verbose for simple queries |
| Raw `asyncpg` | Fastest, minimal abstraction | No ORM, no migrations, SQL strings everywhere |
| Tortoise ORM | Django-like, simpler | Less mature, less migration support |
| SQLModel | Pydantic + SQLAlchemy unified | Still maturing, less ecosystem |

**Why SQLAlchemy wins:**
- Alembic for migrations is the industry standard — essential for a production-grade submission
- Type-safe query building via `select()` — panelists can read the queries
- `expire_on_commit=False` lets us return ORM objects to Pydantic serialisers after `session.commit()` without triggering extra DB roundtrips

---

## ADR-004 — Hash Algorithm: SHA-256

**Decision:** SHA-256 (Python standard library `hashlib.sha256`)

**Alternatives considered:**

| Option | Security | Status | Notes |
|---|---|---|---|
| MD5 | Broken | Compromised 2004 | Practical collision attacks exist — attacker can forge a tampered record with the same MD5 hash, defeating tamper detection entirely |
| SHA-1 | Weak | Deprecated | Google SHAttered attack (2017) demonstrated practical collision |
| **SHA-256** | Strong | NIST-approved | Industry standard for integrity / tamper detection |
| SHA-512 | Stronger | NIST-approved | 512-bit output doubles storage per row (CHAR(128) vs CHAR(64)); no meaningful security gain at this threat model |
| SHA3-256 | Strong | NIST-approved | Different internal design but equivalent security; SHA-256 has broader ecosystem support and recognisability |

**Why SHA-256:**
- 128-bit collision resistance (birthday bound) — computationally infeasible to attack
- NIST FIPS 180-4 approved
- Used in Bitcoin block hashing, Google Certificate Transparency logs, TLS certificates — regulators and auditors recognise it
- Available in Python stdlib — zero additional dependency

---

## ADR-005 — Pagination: Cursor-based (sequence_number) not offset

**Decision:** `GET /audit/events?after_sequence=<int>` cursor pagination

**Why not `?page=2&limit=50` (offset)?**

Offset pagination has a specific problem for audit logs:
- While you are paginating, a new event is written at sequence 51
- Your `LIMIT 50 OFFSET 50` query now skips sequence 51 and returns 52–101
- You silently miss an event — unacceptable for a compliance log

Cursor pagination using `WHERE sequence_number > :after_sequence ORDER BY sequence_number` is:
- Stable: new writes don't shift page boundaries
- Performant: uses the `ix_audit_sequence` B-tree index directly
- Chain-consistent: records are always returned in chain order

---

## ADR-006 — Redaction Strategy: payload_hash separation

**Decision:** `entry_hash` is computed over `payload_hash` (SHA-256 of original payload), not the raw payload JSON.

**The problem:** If `entry_hash` were computed over the raw payload, redacting any field would change the payload, invalidate `entry_hash`, and break every subsequent `chain_hash`. The chain could never be verified after any redaction.

**Solution:** At write time, `payload_hash = SHA256(canonical_payload)` is stored permanently. `entry_hash` is computed over `payload_hash` (not the payload). When a field is redacted, `payload` changes but `payload_hash` does not — so `entry_hash` and `chain_hash` remain valid.

**Per-field hashing:** Each redacted field also stores `{field, hash: SHA256(field_name:value)}` in `redacted_fields`. This lets a compliance officer prove what a redacted field contained (by providing the original value and recomputing the hash) without storing the value in the audit log.

**Precedent:** This is the same separation used in Certificate Transparency logs and Merkle tree audit proofs.

---

## ADR-007 — Single writer pattern for chain append

**Decision:** `ChainService.append` uses `SELECT pg_advisory_xact_lock(1)` (PostgreSQL session-level mutex) before reading the tail row and inserting.

**Why not `SELECT ... FOR UPDATE`:**  
`FOR UPDATE` prevents two writers from *updating or deleting* the locked row, but it does **not** prevent them from each reading the same last row and both inserting a new row claiming the same `sequence_number`. In practice: T1 and T2 both lock the seed row, both compute `sequence_number = last + 1 = 2`, T1 inserts and commits, T2 wakes up — it still holds its own read of row 1 and tries to insert another row with `sequence_number = 2`, causing a `UniqueViolationError`. This race was confirmed by `test_pg_concurrent_appends_no_chain_fork`.

**Why `pg_advisory_xact_lock(1)`:**  
Advisory locks are PostgreSQL session mutexes. Only one transaction can hold advisory lock key `1` at a time; the second transaction blocks at the `SELECT pg_advisory_xact_lock(1)` call until the first commits. This guarantees strictly monotonic, gap-free sequence numbers under any concurrency level. The lock is automatically released on commit or rollback — no manual cleanup.

**SQLite compatibility:** The advisory lock call is gated on `conn.dialect.name == "postgresql"` so test runs against aiosqlite continue to work unchanged. SQLite is single-threaded and never exercises the concurrent-write path.

**Tradeoff:** This limits write throughput to ~sequential appends. For this assignment's scope (compliance audit log, not a high-frequency trading feed), this is acceptable. At higher scale, a write queue (Kafka / SQS) or a dedicated sequence table with `RETURNING` would be considered.

---

## ADR-008 — Framework: FastAPI

**Decision:** FastAPI 0.115

**Alternatives considered:**

| Option | Pros | Cons |
|---|---|---|
| **FastAPI** | Async-native, auto OpenAPI docs, Pydantic v2, type hints | Newer ecosystem |
| Flask | Widely known, simple | Sync by default, no built-in validation |
| Django REST Framework | Batteries included | Heavy, ORM coupling, sync by default |

**Why FastAPI:**
- Async-native — matches the async SQLAlchemy engine without thread pool hacks
- Pydantic v2 validation is built-in — request bodies are validated automatically
- Auto-generates OpenAPI docs at `/docs` — makes it easy for panelists to explore the API live
- Dependency injection via `Depends(get_db)` keeps session lifecycle clean

---

## ADR-009 — Test database: SQLite (aiosqlite) for unit/integration tests

**Decision:** Use `aiosqlite` (SQLite async) in tests, PostgreSQL in production.

**Why:**
- SQLite runs in-memory — tests are fast and require no external services
- `aiosqlite` works with the same SQLAlchemy async engine interface — no mocking needed
- Unit tests for hashing are pure Python — no DB required at all
- The one known limitation: SQLite does not support `SELECT ... FOR UPDATE`, so the chain lock test is skipped in SQLite mode and covered by an explicit PostgreSQL integration test

---

## ADR-010 — Archival: soft-delete, never physical delete

**Decision:** `POST /audit/events/{id}/archive` sets `is_archived = true` and `archived_at = now(UTC)` on the row. The row — including the payload and all four hash columns — stays in `audit_entries` unchanged.

**Why no physical delete:**
- Deleting a row breaks the chain: the next row's `previous_chain_hash` references a hash that no longer exists, so `GET /audit/verify` reports a broken chain
- Keeping the row with all hash fields intact means chain verification walks end-to-end without modification
- Archived rows remain visible to `GET /audit/events` and are included in exports and chain verification by design — the archive flag signals "this record is no longer operationally active" while preserving it for legal / compliance purposes

**Why no payload truncation / cold storage pointer:**
Truncating the payload or replacing it with a cold-storage reference would also change `payload` without updating `payload_hash` — creating a permanently inconsistent row. The payload stays in-row unless a redaction is applied through the normal `PATCH /redact` endpoint.

**Operation is idempotent:** calling archive on an already-archived entry returns 200 and preserves the original `archived_at` timestamp.

---

## ADR-011 — Export: async generator streaming, not buffered response

**Decision:** `GET /audit/export` uses async generator functions (`export_json`, `export_csv`) fed to FastAPI's `StreamingResponse`, with internal cursor-based batching (500 rows per DB round-trip).

**Alternatives considered:**

| Option | Memory use | Latency to first byte | Complexity |
|---|---|---|---|
| **Async generator + StreamingResponse** | O(batch) | Low — bytes flow immediately | Medium |
| Load all rows → return list | O(n) — blows up on large tables | High — client waits for full query | Low |
| Server-Sent Events / WebSocket | O(batch) | Low | High |

**Why streaming:**
- An audit log can contain millions of rows; buffering them all into a Python list before writing the response would exhaust server memory
- Cursor pagination inside the generator (`WHERE sequence_number > last_seq`) uses the existing B-tree index and keeps DB round-trips bounded regardless of table size
- `StreamingResponse` in FastAPI forwards chunks to the HTTP client as they arrive — no extra buffering layer

**Filter params:** `actor_id`, `resource_type`, `resource_id`, and `include_archived` are accepted as query parameters on `GET /audit/export`, matching the filter set on `GET /audit/events`. A DRY `_build_base_query()` helper in `export_service.py` applies these filters for both JSON and CSV generators.

**Format decisions:**
- JSON: top-level array with one object per line — valid JSON and line-delimited, easy to `jq`-pipe
- CSV: RFC 4180; `payload` and `redacted_fields` columns are JSON-encoded strings so the schema stays flat
- Both formats include `prev_chain_hash` in every exported row — see ADR-014

---

## ADR-012 — Archived entries block field redaction (409 Conflict)

**Decision:** `PATCH /audit/events/{id}/redact` returns **409 Conflict** when the entry is already archived.

**Rationale:**
- Archival signals that an entry has completed its operational lifecycle and is being retained for compliance purposes; allowing further mutation after archival undermines the semantic of "this record is closed"
- The order matters: an operator should redact sensitive fields first, then archive — the 409 enforces that ordering rather than silently allowing post-archive redaction
- Redaction itself is always chain-safe (hashes never change), but the business rule that archived records are immutable is the right guardrail for an audit system

**Alternative considered:** allow redaction on archived entries — rejected because it makes the archive state meaningless as an immutability signal.

---

## ADR-014 — Export includes prev_chain_hash anchor for offline bundle verification

**Decision:** Every record in `GET /audit/export` (JSON and CSV) includes a `prev_chain_hash` field: the `chain_hash` of the immediately preceding record in the full chain (or `GENESIS_HASH = "0" * 64` for the first record).

**The problem:** A filtered export (e.g. `?actor_id=alice`) contains only a subset of chain records. Each record's `chain_hash = SHA256(entry_hash + prev_chain_hash)` where `prev_chain_hash` is the chain_hash of `sequence_number - 1` in the *full* chain — a record the recipient almost certainly did not receive. Without this anchor, a recipient can verify each record's `entry_hash` independently, but cannot verify chain continuity without calling the server.

**Solution:** At export time, after fetching each page of filtered rows, the service queries the full (unfiltered) chain for `chain_hash` values at `sequence_number - 1` for every row in the page. These are embedded as `prev_chain_hash` in the exported record. A recipient can then verify:

```python
SHA256(entry_hash || prev_chain_hash) == chain_hash
```

for every record in the file, using only the bundle — no server access, no knowledge of records they did not receive.

**Cost:** One additional DB round-trip per 500-row export batch, fetching only `(sequence_number, chain_hash)` for the predecessor set. For an export endpoint that is already doing sequential I/O, this is negligible.

**Precedent:** This is the same principle as a Merkle proof in Certificate Transparency, where each log leaf is accompanied by sibling hashes sufficient to reconstruct the root — a verifier needs only the leaf and the proof, not the entire log.

---

## ADR-013 — Legacy Column() for Phase 4 nullable ORM columns (Python 3.14 compat)

**Decision:** The three Phase 4 columns (`redacted_fields`, `is_archived`, `archived_at`) are defined using the SQLAlchemy legacy `Column()` form rather than the `Mapped[Optional[X]] = mapped_column(…)` form used for all Phase 1–3 columns.

**Root cause:** Python 3.14 changed the internal contract of `typing.Union.__getitem__` — it now requires the receiver to be a `typing.Union` instance, not the bare class. SQLAlchemy's internal `make_union_type()` utility calls `Union.__getitem__(types_tuple)` directly, which raises `TypeError` in 3.14 for any `Mapped[X | None]` or `Mapped[Optional[X]]` annotation.

**Fix:** `Column()` definitions carry no Python type annotation on the class, so SQLAlchemy's annotation scanner never invokes `make_union_type()` for them. The runtime behaviour — nullability, column type, default value — is identical.

**Scope:** Only nullable columns need this workaround. Non-nullable `Mapped[str]`, `Mapped[int]`, `Mapped[dict]`, `Mapped[bool]`, `Mapped[datetime]` are not union types and are unaffected.

**Future:** Once SQLAlchemy releases a version with Python 3.14 compatibility (expected in a 2.0.x patch), these three columns can be migrated back to the `Mapped[Optional[X]]` form without any data migration.
