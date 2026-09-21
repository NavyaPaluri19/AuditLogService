"""
QueryService — read-side operations against the audit_entries table.

Two public functions:
  list_events()      — cursor-based paginated listing
  get_event_by_id()  — single-entry lookup by UUID

Pagination design
-----------------
We use keyset / cursor pagination, not offset pagination.

  GET /audit/events?after_sequence=50&limit=25

Returns entries with sequence_number > 50, ordered ascending, up to 25 rows.
To detect whether a next page exists, we always fetch `limit + 1` rows and
strip the extra one if it arrives.  The next_cursor returned is the last
sequence_number in the page — the caller passes it as ?after_sequence=<n>
to get the next page.

Why not offset?
  LIMIT 50 OFFSET 50 skips records written between page 1 and page 2.
  For a compliance audit log this is unacceptable — records must never be
  silently omitted from a scan.  Cursor pagination is stable against writes.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_entry import AuditEntry


async def list_events(
    session: AsyncSession,
    after_sequence: int | None,
    limit: int,
) -> tuple[list[AuditEntry], int | None]:
    """
    Return a page of audit entries plus a cursor for the next page.

    Parameters
    ----------
    session:         Active database session.
    after_sequence:  If set, return only entries with sequence_number > this value.
                     None means start from the beginning.
    limit:           Maximum entries to return.

    Returns
    -------
    (entries, next_cursor)
      next_cursor is None when this is the last page.
    """
    query = select(AuditEntry).order_by(AuditEntry.sequence_number.asc())

    if after_sequence is not None:
        query = query.where(AuditEntry.sequence_number > after_sequence)

    # Fetch one extra row so we can tell if a next page exists
    query = query.limit(limit + 1)

    result = await session.execute(query)
    entries = list(result.scalars().all())

    if len(entries) > limit:
        entries = entries[:limit]
        next_cursor: int | None = entries[-1].sequence_number
    else:
        next_cursor = None

    return entries, next_cursor


async def get_event_by_id(
    session: AsyncSession,
    event_id: uuid.UUID,
) -> AuditEntry | None:
    """
    Fetch a single audit entry by its UUID primary key.

    Returns None when no entry with that id exists.
    """
    result = await session.execute(
        select(AuditEntry).where(AuditEntry.id == event_id)
    )
    return result.scalars().first()
