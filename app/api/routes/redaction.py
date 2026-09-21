"""
Redaction and archival routes — Phase 4.

  PATCH /audit/events/{id}/redact
      Redact one or more payload fields (chain-safe — hashes never change).

  POST  /audit/events/{id}/archive
      Soft-archive an entry (row never physically deleted).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.event import ArchiveResponse, EventResponse, RedactRequest
from app.services import redaction_service, retention_service

router = APIRouter()


@router.patch(
    "/events/{event_id}/redact",
    response_model=EventResponse,
    summary="Redact payload fields",
    description=(
        "Set the named payload field values to null and record a "
        "SHA-256 proof of each original value in `redacted_fields`. "
        "The hash chain is not affected — `payload_hash`, `entry_hash`, "
        "and `chain_hash` remain unchanged."
    ),
)
async def redact_event_fields(
    event_id: uuid.UUID,
    body: RedactRequest,
    db: AsyncSession = Depends(get_db),
) -> EventResponse:
    try:
        entry = await redaction_service.redact_fields(db, event_id, body.fields)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if entry is None:
        raise HTTPException(status_code=404, detail="Event not found")

    await db.commit()
    return EventResponse.model_validate(entry)


@router.post(
    "/events/{event_id}/archive",
    response_model=ArchiveResponse,
    status_code=200,
    summary="Soft-archive an audit entry",
    description=(
        "Mark the entry as archived. The row is never physically deleted — "
        "doing so would break the hash chain. Archived entries remain visible "
        "to GET and are included in GET /audit/verify."
    ),
)
async def archive_event(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ArchiveResponse:
    entry = await retention_service.archive_entry(db, event_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Event not found")

    await db.commit()
    return ArchiveResponse.model_validate(entry)
