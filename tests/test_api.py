"""
API endpoint tests — Phase 3.

Covers:
  POST /audit/events        append a new event
  GET  /audit/events        list with cursor pagination
  GET  /audit/events/{id}   single-entry lookup
  GET  /audit/verify        chain integrity check
  GET  /health              sanity

All tests use the in-memory SQLite database provided by the `client`
fixture in conftest.py.  No running PostgreSQL required.
"""

import uuid

import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

SAMPLE_EVENT = {
    "event_type": "USER_LOGIN",
    "actor_id": "user:42",
    "resource_type": "SESSION",
    "resource_id": "sess-001",
    "payload": {"ip": "10.0.0.1", "user_agent": "Mozilla/5.0"},
}

SECOND_EVENT = {
    "event_type": "RECORD_ACCESS",
    "actor_id": "service:payment-gw",
    "resource_type": "ACCOUNT",
    "resource_id": "acct-7890",
    "payload": {"action": "view_balance", "account_number": "1234567890"},
}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealth:
    async def test_health_returns_ok(self, client: AsyncClient):
        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "version" in body


# ---------------------------------------------------------------------------
# POST /audit/events
# ---------------------------------------------------------------------------

class TestAppendEvent:
    async def test_creates_entry_with_201(self, client: AsyncClient):
        resp = await client.post("/audit/events", json=SAMPLE_EVENT)
        assert resp.status_code == 201

    async def test_response_has_required_fields(self, client: AsyncClient):
        data = (await client.post("/audit/events", json=SAMPLE_EVENT)).json()
        for field in ("id", "sequence_number", "payload_hash", "entry_hash", "chain_hash", "created_at"):
            assert field in data, f"Missing field: {field}"

    async def test_first_event_gets_sequence_1(self, client: AsyncClient):
        data = (await client.post("/audit/events", json=SAMPLE_EVENT)).json()
        assert data["sequence_number"] == 1

    async def test_sequence_increments_monotonically(self, client: AsyncClient):
        r1 = (await client.post("/audit/events", json=SAMPLE_EVENT)).json()
        r2 = (await client.post("/audit/events", json=SECOND_EVENT)).json()
        assert r1["sequence_number"] == 1
        assert r2["sequence_number"] == 2

    async def test_hashes_are_64_hex_chars(self, client: AsyncClient):
        data = (await client.post("/audit/events", json=SAMPLE_EVENT)).json()
        for field in ("payload_hash", "entry_hash", "chain_hash"):
            assert len(data[field]) == 64, f"{field} should be 64 hex chars"

    async def test_payload_stored_correctly(self, client: AsyncClient):
        data = (await client.post("/audit/events", json=SAMPLE_EVENT)).json()
        assert data["payload"] == SAMPLE_EVENT["payload"]

    async def test_event_fields_echoed(self, client: AsyncClient):
        data = (await client.post("/audit/events", json=SAMPLE_EVENT)).json()
        assert data["event_type"] == SAMPLE_EVENT["event_type"]
        assert data["actor_id"] == SAMPLE_EVENT["actor_id"]
        assert data["resource_type"] == SAMPLE_EVENT["resource_type"]
        assert data["resource_id"] == SAMPLE_EVENT["resource_id"]

    async def test_missing_required_field_is_422(self, client: AsyncClient):
        """Pydantic v2 must reject a body missing actor_id."""
        incomplete = {k: v for k, v in SAMPLE_EVENT.items() if k != "actor_id"}
        resp = await client.post("/audit/events", json=incomplete)
        assert resp.status_code == 422

    async def test_extra_fields_in_payload_accepted(self, client: AsyncClient):
        """payload is a free-form dict — any keys are accepted."""
        event = {**SAMPLE_EVENT, "payload": {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}}
        resp = await client.post("/audit/events", json=event)
        assert resp.status_code == 201

    async def test_chain_hashes_differ_between_entries(self, client: AsyncClient):
        """Each entry must have a distinct chain_hash."""
        d1 = (await client.post("/audit/events", json=SAMPLE_EVENT)).json()
        d2 = (await client.post("/audit/events", json=SAMPLE_EVENT)).json()
        assert d1["chain_hash"] != d2["chain_hash"]


# ---------------------------------------------------------------------------
# GET /audit/events/{id}
# ---------------------------------------------------------------------------

class TestGetEventById:
    async def test_returns_created_entry(self, client: AsyncClient):
        created = (await client.post("/audit/events", json=SAMPLE_EVENT)).json()
        resp = await client.get(f"/audit/events/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    async def test_all_hashes_match_create_response(self, client: AsyncClient):
        created = (await client.post("/audit/events", json=SAMPLE_EVENT)).json()
        fetched = (await client.get(f"/audit/events/{created['id']}")).json()
        for field in ("payload_hash", "entry_hash", "chain_hash"):
            assert fetched[field] == created[field], f"{field} changed between POST and GET"

    async def test_unknown_id_returns_404(self, client: AsyncClient):
        resp = await client.get(f"/audit/events/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_invalid_uuid_returns_422(self, client: AsyncClient):
        resp = await client.get("/audit/events/not-a-uuid")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /audit/events
# ---------------------------------------------------------------------------

class TestListEvents:
    async def test_empty_database_returns_empty_list(self, client: AsyncClient):
        resp = await client.get("/audit/events")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["next_cursor"] is None
        assert body["total_returned"] == 0

    async def test_returns_all_entries_when_below_limit(self, client: AsyncClient):
        for _ in range(3):
            await client.post("/audit/events", json=SAMPLE_EVENT)
        body = (await client.get("/audit/events")).json()
        assert body["total_returned"] == 3
        assert len(body["items"]) == 3

    async def test_entries_returned_in_ascending_order(self, client: AsyncClient):
        for _ in range(4):
            await client.post("/audit/events", json=SAMPLE_EVENT)
        items = (await client.get("/audit/events")).json()["items"]
        seqs = [item["sequence_number"] for item in items]
        assert seqs == sorted(seqs)

    async def test_next_cursor_null_on_last_page(self, client: AsyncClient):
        await client.post("/audit/events", json=SAMPLE_EVENT)
        body = (await client.get("/audit/events?limit=10")).json()
        assert body["next_cursor"] is None

    async def test_cursor_pagination_no_overlap(self, client: AsyncClient):
        """Two pages must cover all 5 entries with no overlap."""
        for _ in range(5):
            await client.post("/audit/events", json=SAMPLE_EVENT)

        page1 = (await client.get("/audit/events?limit=3")).json()
        assert len(page1["items"]) == 3
        assert page1["next_cursor"] == 3   # sequence_number of last on page 1

        page2 = (await client.get(
            f"/audit/events?after_sequence={page1['next_cursor']}&limit=3"
        )).json()
        assert len(page2["items"]) == 2
        assert page2["next_cursor"] is None

        ids1 = {item["id"] for item in page1["items"]}
        ids2 = {item["id"] for item in page2["items"]}
        assert ids1.isdisjoint(ids2), "Pages must not overlap"
        assert len(ids1 | ids2) == 5, "All 5 entries must appear across both pages"

    async def test_after_sequence_excludes_earlier_entries(self, client: AsyncClient):
        for _ in range(4):
            await client.post("/audit/events", json=SAMPLE_EVENT)
        body = (await client.get("/audit/events?after_sequence=2")).json()
        seqs = [item["sequence_number"] for item in body["items"]]
        assert all(s > 2 for s in seqs)
        assert len(seqs) == 2

    async def test_limit_respected(self, client: AsyncClient):
        for _ in range(10):
            await client.post("/audit/events", json=SAMPLE_EVENT)
        body = (await client.get("/audit/events?limit=4")).json()
        assert body["total_returned"] == 4
        assert len(body["items"]) == 4

    async def test_limit_above_500_rejected(self, client: AsyncClient):
        resp = await client.get("/audit/events?limit=501")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /audit/verify
# ---------------------------------------------------------------------------

class TestVerifyChain:
    async def test_empty_chain_is_valid(self, client: AsyncClient):
        resp = await client.get("/audit/verify")
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["total_entries"] == 0
        assert body["first_broken_sequence"] is None
        assert body["error"] is None

    async def test_single_entry_chain_is_valid(self, client: AsyncClient):
        await client.post("/audit/events", json=SAMPLE_EVENT)
        body = (await client.get("/audit/verify")).json()
        assert body["valid"] is True
        assert body["total_entries"] == 1

    async def test_multi_entry_chain_is_valid(self, client: AsyncClient):
        for _ in range(5):
            await client.post("/audit/events", json=SAMPLE_EVENT)
        body = (await client.get("/audit/verify")).json()
        assert body["valid"] is True
        assert body["total_entries"] == 5
        assert body["first_broken_sequence"] is None

    async def test_verify_is_idempotent(self, client: AsyncClient):
        """Calling verify twice must return the same result."""
        await client.post("/audit/events", json=SAMPLE_EVENT)
        r1 = (await client.get("/audit/verify")).json()
        r2 = (await client.get("/audit/verify")).json()
        assert r1 == r2
