"""
Event routes — core append-and-query operations.

POST /audit/events         Append a new event to the chain
GET  /audit/events         List events with cursor pagination and optional filters
GET  /audit/events/{id}    Fetch a single event by UUID
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.event import EventCreate, EventListResponse, EventResponse
from app.services import chain_service, query_service

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /audit/events
# ---------------------------------------------------------------------------

@router.post(
    "/events",
    response_model=EventResponse,
    status_code=201,
    summary="Append a new audit event",
    description=(
        "Appends an event to the tamper-evident chain. "
        "The server computes payload_hash, entry_hash, and chain_hash — "
        "these are not accepted from the caller."
    ),
)
async def append_event(
    body: EventCreate,
    db: AsyncSession = Depends(get_db),
) -> EventResponse:
    entry = await chain_service.append(db, body)
    return entry


# ---------------------------------------------------------------------------
# GET /audit/events
# ---------------------------------------------------------------------------

@router.get(
    "/events",
    response_model=EventListResponse,
    summary="List audit events (cursor-paginated, filterable)",
    description=(
        "Returns audit entries ordered by sequence_number ascending. "
        "All filter parameters are optional and combinable. "
        "Use ?after_sequence=<n> to continue from a previous page. "
        "next_cursor in the response is the sequence_number to pass next time."
    ),
)
async def list_events(
    # ---- Cursor / pagination ----
    after_sequence: int | None = Query(
        default=None,
        ge=1,
        description="Return entries with sequence_number > this value (cursor pagination)",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of entries to return (1–500)",
    ),
    # ---- Content filters ----
    actor_id: str | None = Query(
        default=None,
        description="Filter by actor_id (exact match)",
    ),
    resource_type: str | None = Query(
        default=None,
        description="Filter by resource_type (exact match)",
    ),
    resource_id: str | None = Query(
        default=None,
        description="Filter by resource_id (exact match)",
    ),
    event_type: str | None = Query(
        default=None,
        description="Filter by event_type (exact match)",
    ),
    # ---- Time range ----
    from_time: datetime | None = Query(
        default=None,
        description="Return entries with created_at >= this timestamp (ISO 8601)",
    ),
    to_time: datetime | None = Query(
        default=None,
        description="Return entries with created_at <= this timestamp (ISO 8601)",
    ),
    db: AsyncSession = Depends(get_db),
) -> EventListResponse:
    entries, next_cursor = await query_service.list_events(
        db,
        after_sequence,
        limit,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        event_type=event_type,
        from_time=from_time,
        to_time=to_time,
    )
    return EventListResponse(
        items=entries,
        next_cursor=next_cursor,
        total_returned=len(entries),
    )


# ---------------------------------------------------------------------------
# GET /audit/events/{event_id}
# ---------------------------------------------------------------------------

@router.get(
    "/events/{event_id}",
    response_model=EventResponse,
    summary="Get a single audit event by ID",
)
async def get_event(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> EventResponse:
    entry = await query_service.get_event_by_id(db, event_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return entry
