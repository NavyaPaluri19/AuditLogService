"""
Tests for Phase 4 export endpoint.

  GET /audit/export?format=json|csv&include_archived=true|false

All tests use the in-memory SQLite fixture from conftest.py.
The streaming responses are buffered fully in tests because the payloads
are small; production use benefits from the chunked streaming.
"""

import csv
import io
import json

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _seed_events(client, count: int = 3) -> list[dict]:
    """Create `count` audit events and return their JSON bodies."""
    events = []
    for i in range(count):
        resp = await client.post(
            "/audit/events",
            json={
                "event_type": "DATA_ACCESS",
                "actor_id": f"user:{i}",
                "resource_type": "RECORD",
                "resource_id": f"rec-{i}",
                "payload": {"index": i, "value": f"val-{i}"},
            },
        )
        assert resp.status_code == 201, resp.text
        events.append(resp.json())
    return events


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------

class TestExportJson:
    async def test_empty_export(self, client):
        resp = await client.get("/audit/export?format=json")
        assert resp.status_code == 200
        data = resp.json()
        assert data == []

    async def test_export_returns_all_events(self, client):
        events = await _seed_events(client, 3)
        resp = await client.get("/audit/export?format=json")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3

    async def test_export_json_fields(self, client):
        await _seed_events(client, 1)
        resp = await client.get("/audit/export?format=json")
        data = resp.json()
        row = data[0]
        for field in [
            "id", "sequence_number", "event_type", "actor_id",
            "resource_type", "resource_id", "payload",
            "payload_hash", "entry_hash", "chain_hash",
            "created_at", "redacted_fields", "is_archived", "archived_at",
        ]:
            assert field in row, f"Missing field: {field}"

    async def test_export_excludes_archived_when_requested(self, client):
        events = await _seed_events(client, 3)
        # Archive the first one
        await client.post(f"/audit/events/{events[0]['id']}/archive")

        resp = await client.get("/audit/export?format=json&include_archived=false")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        ids = [row["id"] for row in data]
        assert events[0]["id"] not in ids

    async def test_export_includes_archived_by_default(self, client):
        events = await _seed_events(client, 2)
        await client.post(f"/audit/events/{events[0]['id']}/archive")

        resp = await client.get("/audit/export?format=json")
        data = resp.json()
        assert len(data) == 2

    async def test_export_includes_redacted_fields_map(self, client):
        events = await _seed_events(client, 1)
        event_id = events[0]["id"]
        await client.patch(
            f"/audit/events/{event_id}/redact",
            json={"fields": ["value"]},
        )
        resp = await client.get("/audit/export?format=json")
        data = resp.json()
        row = data[0]
        assert row["redacted_fields"] is not None
        assert "value" in row["redacted_fields"]
        assert row["payload"]["value"] is None

    async def test_export_content_type(self, client):
        resp = await client.get("/audit/export?format=json")
        assert "application/json" in resp.headers["content-type"]

    async def test_export_invalid_format(self, client):
        resp = await client.get("/audit/export?format=xml")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

class TestExportCsv:
    async def test_csv_empty(self, client):
        resp = await client.get("/audit/export?format=csv")
        assert resp.status_code == 200
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert rows == []

    async def test_csv_returns_all_events(self, client):
        await _seed_events(client, 3)
        resp = await client.get("/audit/export?format=csv")
        assert resp.status_code == 200
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 3

    async def test_csv_header_fields(self, client):
        await _seed_events(client, 1)
        resp = await client.get("/audit/export?format=csv")
        reader = csv.DictReader(io.StringIO(resp.text))
        expected = {
            "id", "sequence_number", "event_type", "actor_id",
            "resource_type", "resource_id", "payload",
            "payload_hash", "entry_hash", "chain_hash",
            "created_at", "redacted_fields", "is_archived", "archived_at",
        }
        assert expected == set(reader.fieldnames)

    async def test_csv_payload_is_json_string(self, client):
        await _seed_events(client, 1)
        resp = await client.get("/audit/export?format=csv")
        reader = csv.DictReader(io.StringIO(resp.text))
        row = next(reader)
        payload = json.loads(row["payload"])
        assert "index" in payload

    async def test_csv_excludes_archived(self, client):
        events = await _seed_events(client, 3)
        await client.post(f"/audit/events/{events[1]['id']}/archive")

        resp = await client.get("/audit/export?format=csv&include_archived=false")
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 2
        ids = [r["id"] for r in rows]
        assert events[1]["id"] not in ids

    async def test_csv_content_type(self, client):
        resp = await client.get("/audit/export?format=csv")
        assert "text/csv" in resp.headers["content-type"]
