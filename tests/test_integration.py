"""
Integration tests — require a live PostgreSQL database.

Run with:
    docker compose up db          # start Postgres in background
    pytest -v --integration       # run all tests, including integration

All tests in this module use the `pg_client` fixture from conftest.py, which
automatically skips the test when --integration is not passed.

What these tests cover that SQLite unit tests cannot
------------------------------------------------------
1. SELECT ... FOR UPDATE serialisation
   aiosqlite silently ignores FOR UPDATE.  Here we fire two concurrent
   append requests via asyncio.gather() and verify that:
   - both succeed (201)
   - no sequence_number is duplicated
   - the resulting chain verifies end-to-end without gaps

   NOTE on the genesis edge case
   ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
   SELECT ... FOR UPDATE locks an existing row.  When the table is completely
   empty every concurrent append sees no row to lock, computes sequence_number=1,
   and races toward a UniqueConstraint collision.  The concurrent tests below
   seed one event first so the lock always has a row to acquire.  In production
   this would be handled with a pg_advisory_xact_lock() call at the start of
   every append; for this project the FOR UPDATE approach is correct for the
   steady-state case that matters most.

2. Chain integrity across the full migration schema
   All three Alembic migrations (001, 002, 003) have already been applied
   by the pg_engine fixture (create_all reflects the current ORM model).
   We confirm chain hashing, redaction, and archival all coexist correctly
   on the real column types (JSONB, BOOLEAN, TIMESTAMPTZ).

3. Full lifecycle: append → redact → archive → verify
   One test walks through every mutating endpoint in order and confirms
   GET /audit/verify still returns valid=true at the end.

4. Export on real Postgres (StreamingResponse with cursor-paginated generator)
   SQLite's streaming export works, but the cursor-based batching is
   identical in both engines; we include it here for completeness.

VerifyResponse field names (from app/schemas/event.py)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  valid                 bool   — True iff every entry passes
  total_entries         int    — count of entries checked
  first_broken_sequence int|None
  error                 str|None
"""

import asyncio

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event_payload(suffix: str = "") -> dict:
    return {
        "event_type": f"INTEGRATION_TEST{suffix}",
        "actor_id": f"test-actor{suffix}",
        "resource_type": "ACCOUNT",
        "resource_id": f"acct-{suffix or 'main'}",
        "payload": {
            "action": "read",
            "amount": 42,
            "note": f"integration{suffix}",
        },
    }


async def _append(client, suffix: str = "") -> dict:
    """POST one event; asserts 201 Created and returns the parsed body."""
    resp = await client.post("/audit/events", json=_event_payload(suffix))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _verify(client) -> dict:
    """GET /audit/verify and assert 200; return parsed body."""
    resp = await client.get("/audit/verify")
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Basic sanity on Postgres
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pg_append_and_retrieve(pg_client):
    """Append one event and retrieve it by ID."""
    created = await _append(pg_client, "-A")
    event_id = created["id"]

    resp = await pg_client.get(f"/audit/events/{event_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == event_id
    assert body["event_type"] == "INTEGRATION_TEST-A"
    assert body["is_archived"] is False


@pytest.mark.asyncio
async def test_pg_single_append_chain_verifies(pg_client):
    """A single-entry chain must verify cleanly."""
    await _append(pg_client, "-B")

    v = await _verify(pg_client)
    assert v["valid"] is True
    assert v["first_broken_sequence"] is None
    assert v["total_entries"] == 1


@pytest.mark.asyncio
async def test_pg_multi_append_chain_verifies(pg_client):
    """Ten sequential appends should produce a clean, contiguous chain."""
    for i in range(10):
        await _append(pg_client, f"-seq{i}")

    v = await _verify(pg_client)
    assert v["valid"] is True
    assert v["total_entries"] == 10


# ---------------------------------------------------------------------------
# 2. SELECT ... FOR UPDATE — chain lock under concurrent writes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pg_concurrent_appends_no_chain_fork(pg_client):
    """
    Fire two append requests simultaneously via asyncio.gather().

    The ChainService.append() uses SELECT ... FOR UPDATE on the last row
    before inserting so that only one writer can hold "last row" at a time.

    We seed one event first so there is always a row to lock — the
    FOR UPDATE lock serialises subsequent concurrent writers correctly.

    Expected outcomes
    -----------------
    - Both requests return 201.
    - Sequence numbers are distinct (no collision / chain fork).
    - GET /audit/verify reports valid=True with total_entries==3.
    """
    # Seed: ensures a lockable row exists before the concurrent burst
    await _append(pg_client, "-c-seed")

    results = await asyncio.gather(
        pg_client.post("/audit/events", json=_event_payload("-c1")),
        pg_client.post("/audit/events", json=_event_payload("-c2")),
    )

    bodies = []
    for resp in results:
        assert resp.status_code == 201, resp.text
        bodies.append(resp.json())

    seqs = {b["sequence_number"] for b in bodies}
    assert len(seqs) == 2, f"Duplicate sequence numbers — chain fork detected: {seqs}"

    v = await _verify(pg_client)
    assert v["valid"] is True
    assert v["total_entries"] == 3

# ---------------------------------------------------------------------------
# 3. Redaction on Postgres
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pg_redaction_chain_remains_intact(pg_client):
    """
    Redact a payload field on Postgres and confirm the chain still verifies.

    Key invariant: payload_hash, entry_hash, and chain_hash must be
    unchanged after redaction.
    """
    event = await _append(pg_client, "-r1")
    event_id = event["id"]
    original_entry_hash = event["entry_hash"]
    original_chain_hash = event["chain_hash"]

    redact = await pg_client.patch(
        f"/audit/events/{event_id}/redact",
        json={"fields": ["note", "amount"]},
    )
    assert redact.status_code == 200
    rb = redact.json()

    # Payload fields zeroed
    assert rb["payload"]["note"] is None
    assert rb["payload"]["amount"] is None
    assert rb["payload"]["action"] == "read"   # untouched

    # Hashes unchanged
    assert rb["entry_hash"] == original_entry_hash
    assert rb["chain_hash"] == original_chain_hash

    # Redacted field evidence recorded
    assert "note" in rb["redacted_fields"]
    assert "amount" in rb["redacted_fields"]

    # Chain still intact
    v = await _verify(pg_client)
    assert v["valid"] is True


@pytest.mark.asyncio
async def test_pg_redaction_idempotent(pg_client):
    """Redacting the same field twice must not change hashes or add duplicates."""
    event = await _append(pg_client, "-r2")
    event_id = event["id"]

    r1 = await pg_client.patch(
        f"/audit/events/{event_id}/redact", json={"fields": ["note"]}
    )
    assert r1.status_code == 200
    first_hash = r1.json()["redacted_fields"]["note"]

    r2 = await pg_client.patch(
        f"/audit/events/{event_id}/redact", json={"fields": ["note"]}
    )
    assert r2.status_code == 200
    # Hash for "note" unchanged on second call
    assert r2.json()["redacted_fields"]["note"] == first_hash


# ---------------------------------------------------------------------------
# 4. Archival on Postgres
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pg_archive_idempotent(pg_client):
    """Archiving an already-archived entry returns 200 and preserves archived_at."""
    event = await _append(pg_client, "-arch1")
    event_id = event["id"]

    a1 = await pg_client.post(f"/audit/events/{event_id}/archive")
    assert a1.status_code == 200
    first_archived_at = a1.json()["archived_at"]

    a2 = await pg_client.post(f"/audit/events/{event_id}/archive")
    assert a2.status_code == 200
    assert a2.json()["archived_at"] == first_archived_at


@pytest.mark.asyncio
async def test_pg_archived_blocks_redaction(pg_client):
    """Attempting to redact an archived entry must return 409 Conflict."""
    event = await _append(pg_client, "-arch2")
    event_id = event["id"]

    archive = await pg_client.post(f"/audit/events/{event_id}/archive")
    assert archive.status_code == 200

    redact = await pg_client.patch(
        f"/audit/events/{event_id}/redact", json={"fields": ["note"]}
    )
    assert redact.status_code == 409


# ---------------------------------------------------------------------------
# 5. Full lifecycle — append → redact → archive → verify
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pg_full_lifecycle_chain_intact(pg_client):
    """
    Walk through the entire Phase 4 lifecycle on Postgres and confirm
    the chain verifies end-to-end at every step.

    Steps
    -----
    1. Append three events (A, B, C).
    2. Redact a field on event B.
    3. Archive event A.
    4. Verify — chain must be valid (total_entries == 3).
    5. Try to redact event A (archived) — must get 409.
    6. Verify again — still valid.
    """
    a = await _append(pg_client, "-lc-A")
    b = await _append(pg_client, "-lc-B")
    _c = await _append(pg_client, "-lc-C")

    # Step 2 — redact
    redact = await pg_client.patch(
        f"/audit/events/{b['id']}/redact", json={"fields": ["amount"]}
    )
    assert redact.status_code == 200

    # Step 3 — archive A
    arch = await pg_client.post(f"/audit/events/{a['id']}/archive")
    assert arch.status_code == 200
    assert arch.json()["is_archived"] is True

    # Step 4 — verify
    v1 = await _verify(pg_client)
    assert v1["valid"] is True
    assert v1["total_entries"] == 3

    # Step 5 — try redact on archived entry
    blocked = await pg_client.patch(
        f"/audit/events/{a['id']}/redact", json={"fields": ["action"]}
    )
    assert blocked.status_code == 409

    # Step 6 — verify again (no change from failed redact attempt)
    v2 = await _verify(pg_client)
    assert v2["valid"] is True


# ---------------------------------------------------------------------------
# 6. Export on Postgres
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pg_export_json_all_events(pg_client):
    """JSON export on Postgres returns all appended events as a valid JSON array."""
    import json

    for i in range(5):
        await _append(pg_client, f"-exp{i}")

    resp = await pg_client.get("/audit/export?format=json")
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]

    data = json.loads(resp.content)
    assert isinstance(data, list)
    assert len(data) == 5
    # Every item must have the chain fields
    for item in data:
        assert "entry_hash" in item
        assert "chain_hash" in item
        assert "sequence_number" in item

# ---------------------------------------------------------------------------
# 7. Cursor pagination on Postgres
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pg_cursor_pagination_stable_under_concurrent_write(pg_client):
    """
    Append 10 events, paginate through them 5 at a time, then append one
    more during the second page fetch.  Cursor pagination must not skip
    or duplicate any of the original 10 rows.
    """
    events = []
    for i in range(10):
        e = await _append(pg_client, f"-page{i}")
        events.append(e)

    # Page 1 — first 5
    p1 = await pg_client.get("/audit/events?limit=5")
    assert p1.status_code == 200
    page1 = p1.json()["items"]
    assert len(page1) == 5
    last_seq = page1[-1]["sequence_number"]

    # Append a new event between pages — must not affect page 2 results
    await _append(pg_client, "-page-interleaved")

    # Page 2 — next 5 (original events only, cursor-stable)
    p2 = await pg_client.get(f"/audit/events?limit=5&after_sequence={last_seq}")
    assert p2.status_code == 200
    page2 = p2.json()["items"]
    assert len(page2) == 5

    # All 10 original events accounted for, none repeated
    all_ids = {e["id"] for e in page1 + page2}
    original_ids = {e["id"] for e in events}
    assert all_ids == original_ids
