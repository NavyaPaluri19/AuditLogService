"""
ChainService — the heart of the tamper-evident append-only log.

Two public functions:
  append()       — write a new entry, using pg_advisory_xact_lock to serialise
  verify_chain() — walk every entry in order and recompute both hashes

Design notes
------------
pg_advisory_xact_lock(1) is used on PostgreSQL to serialise all concurrent
appenders.  It is a true session-level mutex:
  - Only one transaction holds it at a time.
  - It is released automatically when the transaction commits or rolls back.
  - All other appenders block until the current holder commits.

Why not SELECT ... FOR UPDATE?
  FOR UPDATE prevents UPDATE/DELETE on the locked row, but it does NOT prevent
  another transaction from INSERT-ing a new row.  Under concurrent appends:
  - T1 and T2 both lock row N (the seed / current tail) with FOR UPDATE.
  - T1 inserts row N+1 and commits, releasing the row-lock on row N.
  - T2 wakes up, re-reads row N (NOT the new tail N+1), computes seq N+1,
    and collides with T1's insert → UniqueConstraint violation.
  pg_advisory_xact_lock avoids this entirely.

SQLite (used in unit tests) is single-threaded and has no advisory locks;
we skip the lock there — concurrent inserts are not possible in that env.

Datetime normalisation
----------------------
PostgreSQL returns timezone-aware UTC datetimes via asyncpg.
SQLite / aiosqlite returns naive datetimes (no tzinfo).
Both must produce the same .isoformat() string so that chain hashes are
reproducible across environments.  We normalise every timestamp to UTC
before passing it to compute_entry_hash().
"""

from datetime import datetime, timezone

from sqlalchemy import select, text
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
    1. Acquire pg_advisory_xact_lock(1) on PostgreSQL — a true mutex that
       serialises all concurrent appenders.  Skipped on SQLite (single-
       threaded in tests, no concurrent inserts possible).
    2. Read the current tail row to get previous_hash and sequence_number.
    3. Compute payload_hash, entry_hash, chain_hash.
    4. Insert the new row and commit.  The advisory lock is released on commit.

    The advisory lock in step 1 means only one writer proceeds at a time —
    the chain is always a single, linear, gap-free sequence.
    """
    # -- 1. Serialise concurrent appenders ------------------------------------
    # pg_advisory_xact_lock is a PostgreSQL-native session mutex released
    # automatically on commit/rollback.  Only one appender holds it at a time,
    # so by the time we read the tail in step 2, no other appender can insert
    # between our read and our insert.
    #
    # SELECT ... FOR UPDATE cannot do this: it locks an existing row but does
    # NOT block INSERT of new rows, leading to UniqueConstraint collisions when
    # two transactions both read the same "last" row and both compute seq N+1.
    conn = await session.connection()
    if conn.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(1)"))

    # -- 2. Read the current tail -------------------------------------------
    result = await session.execute(
        select(AuditEntry)
        .order_by(AuditEntry.sequence_number.desc())
        .limit(1)
    )
    last: AuditEntry | None = result.scalars().first()

    if last is None:
        # Genesis entry — no previous row exists
        previous_hash = GENESIS_HASH
        sequence_number = 1
    else:
        previous_hash = last.chain_hash
        sequence_number = last.sequence_number + 1

    # -- 3. Capture timestamp -----------------------------------------------
    now = datetime.now(timezone.utc)    # always UTC-aware

    # -- 4. Compute hashes --------------------------------------------------
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

    # -- 5. Persist ---------------------------------------------------------
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
