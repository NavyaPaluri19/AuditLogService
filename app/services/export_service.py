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
  - prev_chain_hash: the chain_hash of the immediately preceding record in
    the FULL chain (sequence_number - 1), or GENESIS_HASH for the first
    record. Included in every exported row so a recipient can verify
    SHA256(entry_hash || prev_chain_hash) == chain_hash entirely offline,
    without calling GET /audit/verify. This makes filtered bundles
    (actor_id or resource_type/resource_id exports) truly self-contained.
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

from app.core.hashing import GENESIS_HASH
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
    "prev_chain_hash",
    "created_at",
    "redacted_fields",
    "is_archived",
    "archived_at",
]

_PAGE_SIZE = 500  # rows fetched per DB round-trip


def _build_base_query(
    *,
    include_archived: bool,
    actor_id: str | None,
    resource_type: str | None,
    resource_id: str | None,
):
    """
    Build the base SELECT query with optional filters applied.
    Both export_json and export_csv use this to avoid duplicating filter logic.
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


async def _build_prev_hash_map(
    session: AsyncSession, rows: list[AuditEntry]
) -> dict[int, str]:
    """
    Return a mapping of {sequence_number: prev_chain_hash} for each row.

    prev_chain_hash is the chain_hash of the record at (sequence_number - 1)
    in the FULL chain (regardless of export filters), or GENESIS_HASH when
    the row is the first record (sequence_number == 1).

    We query only the predecessor sequence numbers we actually need, so this
    is at most one extra round-trip per _PAGE_SIZE batch.
    """
    prev_seqs = [row.sequence_number - 1 for row in rows if row.sequence_number > 1]

    prev_map: dict[int, str] = {}
    if prev_seqs:
        # Query the FULL chain (no filters) for just sequence_number + chain_hash
        result = await session.execute(
            select(AuditEntry.sequence_number, AuditEntry.chain_hash).where(
                AuditEntry.sequence_number.in_(prev_seqs)
            )
        )
        for seq, ch in result:
            prev_map[seq] = ch

    out: dict[int, str] = {}
    for row in rows:
        if row.sequence_number == 1:
            out[row.sequence_number] = GENESIS_HASH
        else:
            # prev_map[seq - 1] should always exist; fall back to empty string
            # only if the chain is somehow broken (verify endpoint will catch it)
            out[row.sequence_number] = prev_map.get(row.sequence_number - 1, "")

    return out


def _entry_to_dict(entry: AuditEntry, prev_chain_hash: str) -> dict[str, Any]:
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
        "prev_chain_hash": prev_chain_hash,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "redacted_fields": entry.redacted_fields,
        "is_archived": entry.is_archived,
        "archived_at": entry.archived_at.isoformat() if entry.archived_at else None,
    }


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

    Each object includes ``prev_chain_hash`` so the recipient can verify
    SHA256(entry_hash || prev_chain_hash) == chain_hash without calling
    GET /audit/verify.
    """
    first = True
    last_seq: int | None = None
    yield "[\n"

    while True:
        q = _build_base_query(
            include_archived=include_archived,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        if last_seq is not None:
            q = q.where(AuditEntry.sequence_number > last_seq)
        q = q.limit(_PAGE_SIZE)

        result = await session.execute(q)
        rows = list(result.scalars().all())
        if not rows:
            break

        prev_hash_map = await _build_prev_hash_map(session, rows)

        for row in rows:
            prefix = "" if first else ",\n"
            yield prefix + json.dumps(
                _entry_to_dict(row, prev_hash_map[row.sequence_number]), default=str
            )
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

    Each row includes ``prev_chain_hash`` so the recipient can verify
    SHA256(entry_hash || prev_chain_hash) == chain_hash without calling
    GET /audit/verify.
    """
    # Yield header row
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_EXPORT_FIELDS, lineterminator="\r\n")
    writer.writeheader()
    yield buf.getvalue()

    last_seq: int | None = None

    while True:
        q = _build_base_query(
            include_archived=include_archived,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        if last_seq is not None:
            q = q.where(AuditEntry.sequence_number > last_seq)
        q = q.limit(_PAGE_SIZE)

        result = await session.execute(q)
        rows = list(result.scalars().all())
        if not rows:
            break

        prev_hash_map = await _build_prev_hash_map(session, rows)

        for row in rows:
            d = _entry_to_dict(row, prev_hash_map[row.sequence_number])
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
