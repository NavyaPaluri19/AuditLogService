"""
Soft-archive (retention) service.

Why soft-archive instead of DELETE?
  Physical deletion would break the hash chain: the next row's
  ``previous_hash`` points to a hash that no longer exists, so
  GET /audit/verify would report the chain as broken.

  Soft-archival keeps the row — and all its hash fields — intact.
  The ``is_archived`` flag and ``archived_at`` timestamp are the only
  columns that change; the chain continues to verify end-to-end.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_entry import AuditEntry


async def archive_entry(
    session: AsyncSession,
    event_id: uuid.UUID,
) -> AuditEntry | None:
    """
    Soft-archive an audit entry.

    Sets ``is_archived = True`` and ``archived_at = now(UTC)`` on the entry.
    The operation is idempotent: calling it on an already-archived entry
    returns the entry unchanged (same ``archived_at`` timestamp preserved).

    Returns the updated entry, or None if event_id is not found.
    """
    result = await session.execute(
        select(AuditEntry).where(AuditEntry.id == event_id)
    )
    entry = result.scalars().first()
    if entry is None:
        return None

    if entry.is_archived:
        # Idempotent — already archived; return without modifying
        return entry

    entry.is_archived = True
    entry.archived_at = datetime.now(tz=timezone.utc)

    await session.flush()
    await session.refresh(entry)
    return entry
