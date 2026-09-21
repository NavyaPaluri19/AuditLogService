"""
Field-level redaction service.

Design contract (chain-safety):
  - Only ``payload`` column values are changed — fields are set to None.
  - ``payload_hash``, ``entry_hash``, and ``chain_hash`` are NEVER touched.
  - A record of what was redacted is written to ``redacted_fields`` as
    {field_name: SHA-256(field_name:original_value)}, letting verifiers prove
    what the original value was without revealing it.
  - The chain remains valid end-to-end after any number of redactions
    because GET /audit/verify re-derives ``entry_hash`` from ``payload_hash``
    (the immutable original hash), not from the live payload.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.hashing import hash_field_value
from app.models.audit_entry import AuditEntry


async def redact_fields(
    session: AsyncSession,
    event_id: uuid.UUID,
    fields: list[str],
) -> AuditEntry | None:
    """
    Redact the named payload fields on the given audit entry.

    Returns the updated entry, or None if event_id is not found.

    Raises ValueError if the entry is already archived (archived rows are
    immutable as a policy guard — callers should surface a 409 Conflict).

    Fields that don't exist in the payload, or whose current value is already
    None, are silently skipped (idempotent).
    """
    result = await session.execute(
        select(AuditEntry).where(AuditEntry.id == event_id)
    )
    entry = result.scalars().first()
    if entry is None:
        return None

    if entry.is_archived:
        raise ValueError("Cannot redact fields on an archived entry")

    payload: dict = dict(entry.payload)           # mutable copy
    redacted: dict = dict(entry.redacted_fields or {})  # existing map

    redacted_any = False
    for field_name in fields:
        if field_name not in payload:
            continue                               # field doesn't exist — skip
        original_value = payload[field_name]
        if original_value is None:
            continue                               # already null — idempotent skip
        redacted[field_name] = hash_field_value(field_name, original_value)
        payload[field_name] = None
        redacted_any = True

    if not redacted_any:
        # Nothing changed — return entry unchanged so the route can 200 as-is
        return entry

    # Persist — only payload and redacted_fields are written; hashes untouched
    entry.payload = payload
    entry.redacted_fields = redacted

    await session.flush()
    await session.refresh(entry)
    return entry
