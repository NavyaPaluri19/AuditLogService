# Final Engineering Summary

## What was built

A tamper-evident, append-only audit log service that satisfies all four required scenarios:

| Scenario | Requirement | Implementation |
|---|---|---|
| A | Append events to a tamper-evident chain | SHA-256 chain: `entry_hash` over `(sequence_number, actor_id, resource_type, resource_id, payload_hash, prev_chain_hash)`, each row's `chain_hash = SHA256(entry_hash + prev_chain_hash)` |
| B | Export a self-contained verifiable bundle for a specific actor or resource | `GET /audit/export?actor_id=…` and `GET /audit/export?resource_type=…&resource_id=…` stream a filtered JSON or CSV export |
| C | Regulators audit access to client account data (ambiguous) | Clarified via explicit assumptions (see `SCENARIO_C.md`); implemented as `resource_type` filtering on list and export endpoints |
| D | Field-level redaction without breaking the chain | `PATCH /audit/events/{id}/redact` zeroes payload fields and records `{field, hash}` in `redacted_fields`; `entry_hash` uses `payload_hash` (hash of original payload), not the payload itself, so redaction never invalidates the chain |

---

## Plan and rationale

Development was structured in four phases matching the assignment brief:

**Phase 1 — Core foundations**  
Scaffold (FastAPI + SQLAlchemy 2.0 async + asyncpg + Pydantic v2), hashing module, Docker Compose (Postgres 16), health endpoint, and exhaustive unit tests for all hash functions. Getting the hash logic right before writing any DB code meant no retrofitting.

**Phase 2 — Persistence**  
ORM model (`audit_entries` table) and Alembic migration 001. `ChainService.append()` reads the tail row, computes the next sequence number and all four hashes in a single transaction, then inserts. The transaction guarantees the hash computation and write are atomic.

**Phase 3 — Query API**  
`GET /audit/events` (cursor-paginated, filterable), `GET /audit/events/{id}`, `GET /audit/verify` (full chain walk). Cursor pagination (`WHERE sequence_number > :cursor`) rather than offset pagination, because offset silently skips records written between pages — unacceptable for compliance.

**Phase 4 — Compliance features**  
Alembic migrations 002 (`redacted_fields`, `payload_hash` columns) and 003 (`is_archived`, `archived_at`). Redaction endpoint, archival endpoint, and streaming export (JSON + CSV via `StreamingResponse` with async generator batching).

---

## Artifacts

| File | Purpose |
|---|---|
| `app/core/hashing.py` | SHA-256 chain functions — foundation of tamper detection |
| `app/models/audit_entry.py` | SQLAlchemy ORM model; all chain and compliance columns |
| `app/services/chain_service.py` | `append()` with `pg_advisory_xact_lock` serialisation, `verify_chain()` |
| `app/services/query_service.py` | Cursor-based paginated listing with full filter set |
| `app/services/export_service.py` | Async generator streaming export (JSON + CSV) with filter support |
| `app/services/redaction_service.py` | Chain-safe field-level redaction |
| `app/services/retention_service.py` | Soft-delete archival |
| `app/api/routes/events.py` | POST/GET events routes with full filter params |
| `app/api/routes/export.py` | Streaming export route with actor/resource filter params |
| `alembic/versions/001_initial_schema.py` | Core chain columns |
| `alembic/versions/002_add_redaction_columns.py` | `redacted_fields`, `payload_hash` |
| `alembic/versions/003_add_archival_columns.py` | `is_archived`, `archived_at` |
| `tests/` | 88 tests: unit (hashing), integration (SQLite), PostgreSQL integration |
| `DECISIONS.md` | 13 Architecture Decision Records |
| `SCENARIO_C.md` | Ambiguous requirement clarification write-up |

---

## Key design decisions and rationale

**`entry_hash` over `payload_hash`, not raw payload**  
The most important design choice. If `entry_hash` were computed over the raw payload, any redaction would change the payload, invalidate `entry_hash`, and cascade-invalidate every subsequent `chain_hash`. Computing `entry_hash` over `payload_hash` (the SHA-256 of the original payload stored permanently at write time) means redaction changes the payload column but never touches `payload_hash` — the chain continues to verify end-to-end after any redaction.

**`pg_advisory_xact_lock(1)` for concurrent append serialisation**  
`SELECT FOR UPDATE` on the last row prevents two writers from updating the same existing row, but it does not prevent two writers from simultaneously reading the same last row and each inserting a new row claiming the same `sequence_number`. `pg_advisory_xact_lock(1)` is a true PostgreSQL session mutex: only one transaction can hold it at a time; the second transaction blocks at the `SELECT pg_advisory_xact_lock(1)` call until the first commits. This guarantees strictly monotonic, gap-free sequence numbers under any concurrency. The lock is dialect-gated (`conn.dialect.name == "postgresql"`) so SQLite tests continue to work.

**Cursor pagination over offset**  
`LIMIT n OFFSET k` silently skips records written between page 1 and page 2. For a compliance audit log, silent omission is unacceptable. `WHERE sequence_number > :last_seen ORDER BY sequence_number` is stable against concurrent writes.

**Soft-delete archival, never physical delete**  
Deleting a row breaks the chain: the next row's `chain_hash` references a predecessor that no longer exists. Archived rows stay in the table with all hash fields intact. The archive flag signals "operationally retired" while preserving the entry for legal compliance.

---

## Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Advisory lock becomes a throughput bottleneck | Low (audit log, not OLTP) | Medium | At scale, replace with a write queue (Kafka / SQS) or a dedicated sequence table with `RETURNING` — documented in ADR-007 |
| Hash chain breaks on partial migration | Low | High | Three separate Alembic migrations with `downgrade()` paths; `GET /audit/verify` surfaces breaks immediately |
| Export OOM on very large tables | Low | High | Async generator with 500-row cursor batches — server memory use is O(batch size), not O(table size) |
| SQLite / PostgreSQL test divergence | Medium | Medium | Explicit `pytest.mark.integration` + `--integration` flag; advisory lock is skipped on SQLite and verified by the Postgres-specific `test_pg_concurrent_appends` test |
| `payload_hash` / `chain_hash` column added mid-chain | Low | High | Migration 002 backfills `payload_hash` for existing rows using the same `SHA256(canonical_json(payload))` formula — no existing `entry_hash` or `chain_hash` values change |

---

## Trade-offs

| Decision | What we got | What we gave up |
|---|---|---|
| Single `pg_advisory_xact_lock` for all appends | Simple, correct serialisation; no distributed state | Write throughput is ~sequential; fine for a compliance log, wrong for a high-frequency feed |
| SHA-256 (not SHA-512 or SHA3-256) | FIPS 180-4 approved, recognisable to regulators, stdlib | Slightly smaller security margin than SHA-512 (still computationally infeasible to attack) |
| In-row payload storage | Simple, all data in one query | Very large payloads (>1 MB) would inflate row size; a TOAST pointer or object-store reference would be needed |
| Soft-delete archival | Chain stays intact; archived entries visible to verify | Storage grows monotonically; a physical purge after a statutory retention window would require rebuilding the chain tail |
| SQLite for test isolation | Fast, zero-dependency test runs | Two features (advisory lock, JSONB operators) must be integration-tested against real Postgres |

---

## Assumptions

1. `payload` is a JSON object supplied by the caller; the server treats it as opaque and does not validate its schema.
2. `actor_id` and `resource_id` are string identifiers (UUIDs, usernames, etc.) managed by the caller's identity system.
3. The service is not responsible for authentication or authorisation — it trusts that the caller has already verified identity before calling `POST /audit/events`.
4. "Client account data" (Scenario C) is represented by `resource_type=account` or similar — the caller controls the vocabulary.
5. All timestamps are UTC; the service never normalises caller-supplied times (there are none — `created_at` is set server-side).
6. The advisory lock key `1` is sufficient because this service is the only writer on its database; a shared-database deployment would need a named or namespaced lock.

---

## Limitations

1. **Single-node only.** The advisory lock serialises appends on one Postgres instance. A multi-primary setup would need a distributed lock (e.g., Zookeeper, Redis SETNX) or a message queue in front of a single writer.
2. **No built-in authentication.** The API is open by design for this assignment. Production deployment would add an API-key middleware or an OAuth2 layer in front of FastAPI.
3. **No payload schema validation.** The service accepts any JSON object as `payload`. A production system would register event schemas and validate incoming payloads against them.
4. **No compaction / cold storage.** The audit table grows indefinitely. A production system would tier old entries to object storage (S3 Glacier) after the statutory retention window while keeping the hash columns in Postgres for chain continuity.
5. **Export has no pagination cursor.** `GET /audit/export` streams the entire filtered result set. For very selective filters that still return millions of rows, a paginated export API (returning a cursor per chunk) would be safer for the client.
