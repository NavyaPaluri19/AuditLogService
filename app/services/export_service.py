"""
Export service — streams audit entries as JSON or CSV.

Design:
  - Queries are cursor-paginated in configurable page batches (default 500)
    so arbitrarily large exports don't blow up server memory.
  - The caller receives an async generator that yields str chunks, which
    FastAPI's StreamingResponse forwards directly to the HTTP client.
  - Archived entries are included by default (they remain part of the
    chain); callers can pass ``include_archived=False`` to skip them.
  - JSON format: a top-level array, one object per line (newline-delimited)
    so the file is both valid JSON and easy to stream.
  - CSV format: RFC 4180 compliant; payload is JSON-encoded into a single
    column; redacted_fields likewise.
  - Optional actor_id / resource_type / resource_id filters let callers
    export a self-contained, verifiable bundle for a specific actor or
    resource (Scenario B requirement).
"""

import csv
import io
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_entry import AuditEntry

# Columns exported in order; determines both JSON keys and CSV header
_EXPORT_FIELDS: list[str] = [
    "id",
    "sequence_number",
    "event_type",
    "actor_id",
    "resource_type",
    "resource_id",
    "payload",
    "payload_hash",
    "entry_hash",
    "chain_hash",
    "created_at",
    "redacted_fields",
    "is_archived",
    "archived_at",
]

_PAGE_SIZE = 500  # rows fetched per DB round-trip


def _entry_to_dict(entry: AuditEntry) -> dict[str, Any]:
    """Convert an ORM row to a plain dict using the canonical export field list."""
    return {
        "id": str(entry.id),
        "sequence_number": entry.sequence_number,
        "event_type": entry.event_type,
        "actor_id": entry.actor_id,
        "resource_type": entry.resource_type,
        "resource_id": entry.resource_id,
        "payload": entry.payload,
        "payload_hash": entry.payload_hash,
        "entry_hash": entry.entry_hash,
        "chain_hash": entry.chain_hash,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "redacted_fields": entry.redacted_fields,
        "is_archived": entry.is_archived,
        "archived_at": entry.archived_at.isoformat() if entry.archived_at else None,
    }


def _build_base_query(
    *,
    include_archived: bool,
    actor_id: str | None,
    resource_type: str | None,
    resource_id: str | None,
):
    """
    Build the base SELECT with all static filters applied.

    The cursor (sequence_number > last_seq) is added per-page inside the
    export loops rather than here so the same base query can be reused.
    """
    q = select(AuditEntry).order_by(AuditEntry.sequence_number.asc())

    if not include_archived:
        q = q.where(AuditEntry.is_archived.is_(False))
    if actor_id is not None:
        q = q.where(AuditEntry.actor_id == actor_id)
    if resource_type is not None:
        q = q.where(AuditEntry.resource_type == resource_type)
    if resource_id is not None:
        q = q.where(AuditEntry.resource_id == resource_id)

    return q


async def export_json(
    session: AsyncSession,
    *,
    include_archived: bool = True,
    actor_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Yield chunks of a JSON array of audit entries, suitable for
    FastAPI StreamingResponse with media_type='application/json'.

    Format: newline-delimited JSON objects wrapped in a top-level array,
    e.g.:
        [
        {"id": "...", ...},
        {"id": "...", ...}
        ]
    """
    first = True
    last_seq: int | None = None
    yield "[\n"

    base_q = _build_base_query(
        include_archived=include_archived,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )

    while True:
        q = base_q
        if last_seq is not None:
            q = q.where(AuditEntry.sequence_number > last_seq)
        q = q.limit(_PAGE_SIZE)

        result = await session.execute(q)
        rows = list(result.scalars().all())
        if not rows:
            break

        for row in rows:
            prefix = "" if first else ",\n"
            yield prefix + json.dumps(_entry_to_dict(row), default=str)
            first = False

        last_seq = rows[-1].sequence_number
        if len(rows) < _PAGE_SIZE:
            break

    # Explicitly end the implicit read transaction before yielding the
    # final chunk.  Without this, the session still holds an open
    # transaction (ACCESS SHARE lock on the table) when control returns
    # to the caller.  That lock blocks the next DROP TABLE in tests and
    # can delay connection cleanup in production pools.
    await session.commit()

    yield "\n]\n"


async def export_csv(
    session: AsyncSession,
    *,
    include_archived: bool = True,
    actor_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Yield chunks of CSV text suitable for StreamingResponse with
    media_type='text/csv'.

    The payload and redacted_fields columns are JSON-encoded strings
    so they fit in a single CSV cell.
    """
    # Yield header row
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_EXPORT_FIELDS, lineterminator="\r\n")
    writer.writeheader()
    yield buf.getvalue()

    last_seq: int | None = None
    base_q = _build_base_query(
        include_archived=include_archived,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )

    while True:
        q = base_q
        if last_seq is not None:
            q = q.where(AuditEntry.sequence_number > last_seq)
        q = q.limit(_PAGE_SIZE)

        result = await session.execute(q)
        rows = list(result.scalars().all())
        if not rows:
            break

        for row in rows:
            d = _entry_to_dict(row)
            # Encode dict columns as JSON strings for CSV
            d["payload"] = json.dumps(d["payload"], default=str)
            d["redacted_fields"] = (
                json.dumps(d["redacted_fields"], default=str)
                if d["redacted_fields"] is not None
                else ""
            )
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=_EXPORT_FIELDS, lineterminator="\r\n")
            writer.writerow(d)
            yield buf.getvalue()

        last_seq = rows[-1].sequence_number
        if len(rows) < _PAGE_SIZE:
            break

    # Explicitly end the implicit read transaction (same reason as export_json).
    await session.commit()
