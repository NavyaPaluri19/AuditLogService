"""
ChainService — the heart of the tamper-evident append-only log.

Two public functions:
  append()       — write a new entry, holding a FOR UPDATE lock on the tail
  verify_chain() — walk every entry in order and recompute both hashes

Design notes
------------
SELECT ... FOR UPDATE on the most-recent row serialises all concurrent writers:
  - Only one transaction can hold the lock at a time.
  - The next writer must wait until the first commits, then re-reads the new tail.
  - This prevents chain forks and sequence gaps even under heavy concurrency.

SQLite (used in tests) does not support FOR UPDATE; the lock is silently
ignored there. That's acceptable because test code is single-threaded and the
test that exercises locking behaviour is marked with pytest.mark.integration
and runs against live Postgres only.

Datetime normalisation
----------------------
PostgreSQL returns timezone-aware UTC datetimes via asyncpg.
SQLite / aiosqlite returns naive datetimes (no tzinfo).
Both must produce the same .isoformat() string so that chain hashes are
reproducible across environments.  We normalise every timestamp to UTC
before passing it to compute_entry_hash().
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.hashing import (
    GENESIS_HASH,
    compute_chain_hash,
    compute_entry_hash,
    hash_payload,
)
from app.models.audit_entry import AuditEntry
from app.schemas.event import EventCreate


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _to_utc(dt: datetime) -> datetime:
    """
    Ensure a datetime is UTC-aware.

    PostgreSQL/asyncpg returns UTC-aware datetimes.
    SQLite/aiosqlite returns naive datetimes — we treat those as UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def append(session: AsyncSession, data: EventCreate) -> AuditEntry:
    """
    Append a new event to the audit chain.

    Steps
    -----
    1. Lock the current tail row with SELECT ... FOR UPDATE.
    2. Derive the next sequence_number and previous_hash from it.
    3. Compute payload_hash, entry_hash, chain_hash.
    4. Insert the new row and commit.

    The lock in step 1 means only one writer proceeds at a time —
    the chain is always a single, linear, gap-free sequence.
    """
    # -- 1. Lock the tail ---------------------------------------------------
    result = await session.execute(
        select(AuditEntry)
        .order_by(AuditEntry.sequence_number.desc())
        .limit(1)
        .with_for_update()          # blocks concurrent appends until we commit
    )
    last: AuditEntry | None = result.scalars().first()

    if last is None:
        # Genesis entry — no previous row exists
        previous_hash = GENESIS_HASH
        sequence_number = 1
    else:
        previous_hash = last.chain_hash
        sequence_number = last.sequence_number + 1

    # -- 2. Capture timestamp -----------------------------------------------
    now = datetime.now(timezone.utc)    # always UTC-aware

    # -- 3. Compute hashes --------------------------------------------------
    p_hash = hash_payload(data.payload)

    e_hash = compute_entry_hash(
        sequence_number=sequence_number,
        event_type=data.event_type,
        actor_id=data.actor_id,
        resource_type=data.resource_type,
        resource_id=data.resource_id,
        payload_hash=p_hash,
        timestamp=now,              # UTC-aware datetime
    )

    c_hash = compute_chain_hash(e_hash, previous_hash)

    # -- 4. Persist ---------------------------------------------------------
    entry = AuditEntry(
        sequence_number=sequence_number,
        event_type=data.event_type,
        actor_id=data.actor_id,
        resource_type=data.resource_type,
        resource_id=data.resource_id,
        payload=data.payload,
        payload_hash=p_hash,
        entry_hash=e_hash,
        chain_hash=c_hash,
        created_at=now,
    )

    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


async def verify_chain(session: AsyncSession) -> dict:
    """
    Walk every entry in ascending sequence order and verify the hash chain.

    For each entry:
      a. Recompute entry_hash from stored immutable fields (uses payload_hash,
         not the current payload, so it stays valid after redaction).
      b. Recompute chain_hash from entry_hash + previous chain_hash.
      c. Compare both against stored values.

    Returns a dict that maps directly to VerifyResponse:
      {
        "valid": bool,
        "total_entries": int,
        "first_broken_sequence": int | None,
        "error": str | None,
      }
    """
    result = await session.execute(
        select(AuditEntry).order_by(AuditEntry.sequence_number.asc())
    )
    entries: list[AuditEntry] = list(result.scalars().all())

    if not entries:
        return {
            "valid": True,
            "total_entries": 0,
            "first_broken_sequence": None,
            "error": None,
        }

    previous_hash = GENESIS_HASH

    for entry in entries:
        # Normalise timestamp — handles naive datetimes from SQLite
        ts = _to_utc(entry.created_at)

        # a. Verify entry_hash
        expected_entry_hash = compute_entry_hash(
            sequence_number=entry.sequence_number,
            event_type=entry.event_type,
            actor_id=entry.actor_id,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            payload_hash=entry.payload_hash,    # original hash, unchanged by redaction
            timestamp=ts,
        )
        if entry.entry_hash != expected_entry_hash:
            return {
                "valid": False,
                "total_entries": len(entries),
                "first_broken_sequence": entry.sequence_number,
                "error": (
                    f"entry_hash mismatch at sequence {entry.sequence_number}: "
                    f"stored={entry.entry_hash[:16]}… "
                    f"expected={expected_entry_hash[:16]}…"
                ),
            }

        # b. Verify chain_hash
        expected_chain_hash = compute_chain_hash(entry.entry_hash, previous_hash)
        if entry.chain_hash != expected_chain_hash:
            return {
                "valid": False,
                "total_entries": len(entries),
                "first_broken_sequence": entry.sequence_number,
                "error": (
                    f"chain_hash mismatch at sequence {entry.sequence_number}: "
                    f"previous_hash={previous_hash[:16]}… "
                    f"stored={entry.chain_hash[:16]}… "
                    f"expected={expected_chain_hash[:16]}…"
                ),
            }

        # Advance the chain
        previous_hash = entry.chain_hash

    return {
        "valid": True,
        "total_entries": len(entries),
        "first_broken_sequence": None,
        "error": None,
    }
