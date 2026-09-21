"""
Tests for Phase 4 redaction and archival endpoints.

  PATCH /audit/events/{id}/redact
  POST  /audit/events/{id}/archive

All tests use the in-memory SQLite fixture from conftest.py.
"""

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_event(client, payload=None) -> dict:
    """POST a single event and return its JSON body."""
    body = {
        "event_type": "TRADE_EXECUTED",
        "actor_id": "user:99",
        "resource_type": "TRADE",
        "resource_id": "trade-001",
        "payload": payload or {"amount": 1000, "ssn": "123-45-6789", "note": "test"},
    }
    resp = await client.post("/audit/events", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# PATCH /audit/events/{id}/redact
# ---------------------------------------------------------------------------

class TestRedactFields:
    async def test_redact_single_field(self, client):
        event = await _create_event(client)
        event_id = event["id"]

        resp = await client.patch(
            f"/audit/events/{event_id}/redact",
            json={"fields": ["ssn"]},
        )
        assert resp.status_code == 200
        body = resp.json()

        # Payload field is now null
        assert body["payload"]["ssn"] is None
        # Other payload fields untouched
        assert body["payload"]["amount"] == 1000
        # redacted_fields records the hash
        assert body["redacted_fields"] is not None
        assert "ssn" in body["redacted_fields"]
        assert len(body["redacted_fields"]["ssn"]) == 64  # SHA-256 hex

    async def test_redact_multiple_fields(self, client):
        event = await _create_event(client)
        event_id = event["id"]

        resp = await client.patch(
            f"/audit/events/{event_id}/redact",
            json={"fields": ["ssn", "note"]},
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["payload"]["ssn"] is None
        assert body["payload"]["note"] is None
        assert body["payload"]["amount"] == 1000
        assert "ssn" in body["redacted_fields"]
        assert "note" in body["redacted_fields"]

    async def test_chain_hashes_unchanged_after_redaction(self, client):
        """payload_hash, entry_hash, chain_hash must never change."""
        event = await _create_event(client)
        event_id = event["id"]
        original_payload_hash = event["payload_hash"]
        original_entry_hash   = event["entry_hash"]
        original_chain_hash   = event["chain_hash"]

        await client.patch(
            f"/audit/events/{event_id}/redact",
            json={"fields": ["ssn"]},
        )
        # Fetch fresh copy
        fetch = await client.get(f"/audit/events/{event_id}")
        assert fetch.status_code == 200
        body = fetch.json()

        assert body["payload_hash"] == original_payload_hash
        assert body["entry_hash"]   == original_entry_hash
        assert body["chain_hash"]   == original_chain_hash

    async def test_verify_still_valid_after_redaction(self, client):
        """Chain verification must pass end-to-end even after redaction."""
        event = await _create_event(client)
        await client.patch(
            f"/audit/events/{event['id']}/redact",
            json={"fields": ["ssn"]},
        )
        verify = await client.get("/audit/verify")
        assert verify.status_code == 200
        assert verify.json()["valid"] is True

    async def test_redact_nonexistent_field_is_noop(self, client):
        """Redacting a field that doesn't exist in the payload is ignored."""
        event = await _create_event(client)
        event_id = event["id"]

        resp = await client.patch(
            f"/audit/events/{event_id}/redact",
            json={"fields": ["nonexistent_field"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        # redacted_fields stays null — nothing was changed
        assert body["redacted_fields"] is None

    async def test_redact_idempotent(self, client):
        """Redacting an already-null field twice should not error."""
        event = await _create_event(client)
        event_id = event["id"]

        await client.patch(
            f"/audit/events/{event_id}/redact",
            json={"fields": ["ssn"]},
        )
        resp2 = await client.patch(
            f"/audit/events/{event_id}/redact",
            json={"fields": ["ssn"]},
        )
        assert resp2.status_code == 200
        body = resp2.json()
        # Still null, still one entry in redacted_fields
        assert body["payload"]["ssn"] is None
        assert "ssn" in body["redacted_fields"]

    async def test_redact_not_found(self, client):
        import uuid
        resp = await client.patch(
            f"/audit/events/{uuid.uuid4()}/redact",
            json={"fields": ["ssn"]},
        )
        assert resp.status_code == 404

    async def test_redact_archived_entry_returns_409(self, client):
        event = await _create_event(client)
        event_id = event["id"]

        await client.post(f"/audit/events/{event_id}/archive")
        resp = await client.patch(
            f"/audit/events/{event_id}/redact",
            json={"fields": ["ssn"]},
        )
        assert resp.status_code == 409

    async def test_redact_requires_fields_list(self, client):
        event = await _create_event(client)
        # Empty fields list — FastAPI/Pydantic should reject (min_length=1)
        resp = await client.patch(
            f"/audit/events/{event['id']}/redact",
            json={"fields": []},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /audit/events/{id}/archive
# ---------------------------------------------------------------------------

class TestArchiveEntry:
    async def test_archive_sets_flag(self, client):
        event = await _create_event(client)
        event_id = event["id"]

        resp = await client.post(f"/audit/events/{event_id}/archive")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == event_id
        assert body["is_archived"] is True
        assert body["archived_at"] is not None

    async def test_archived_flag_persists(self, client):
        event = await _create_event(client)
        event_id = event["id"]

        await client.post(f"/audit/events/{event_id}/archive")
        fetch = await client.get(f"/audit/events/{event_id}")
        assert fetch.status_code == 200
        assert fetch.json()["is_archived"] is True

    async def test_archive_idempotent(self, client):
        """Archiving twice should succeed and return same archived_at."""
        event = await _create_event(client)
        event_id = event["id"]

        r1 = await client.post(f"/audit/events/{event_id}/archive")
        r2 = await client.post(f"/audit/events/{event_id}/archive")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["archived_at"] == r2.json()["archived_at"]

    async def test_archive_not_found(self, client):
        import uuid
        resp = await client.post(f"/audit/events/{uuid.uuid4()}/archive")
        assert resp.status_code == 404

    async def test_chain_still_valid_after_archive(self, client):
        """Archived rows stay in the chain and must not break verification."""
        event = await _create_event(client)
        await client.post(f"/audit/events/{event['id']}/archive")

        # Append another entry after the archived one
        await _create_event(client, payload={"key": "value"})

        verify = await client.get("/audit/verify")
        assert verify.status_code == 200
        assert verify.json()["valid"] is True
