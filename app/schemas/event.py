"""
Pydantic v2 schemas for audit event endpoints.

Schema grows incrementally alongside migrations:
  Phase 2  — EventCreate, EventResponse, EventListResponse, VerifyResponse
  Phase 4  — add redacted_fields to EventResponse; add ArchiveResponse

Naming convention:
  *Create   — inbound (POST body)
  *Response — outbound (single item)
  *List     — outbound (paginated collection)
  *Verify   — outbound (chain integrity report)
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------

class EventCreate(BaseModel):
    """Body for POST /audit/events."""

    event_type: str = Field(
        ...,
        max_length=128,
        examples=["USER_LOGIN", "RECORD_ACCESS", "CONFIG_CHANGE"],
    )
    actor_id: str = Field(
        ...,
        max_length=256,
        description="Identity of the principal who triggered the event",
        examples=["user:42", "service:payment-gateway"],
    )
    resource_type: str = Field(
        ...,
        max_length=128,
        examples=["ACCOUNT", "TRADE", "USER"],
    )
    resource_id: str = Field(
        ...,
        max_length=256,
        description="Specific resource identifier within resource_type",
        examples=["acct-7890", "trade-001"],
    )
    payload: dict[str, Any] = Field(
        ...,
        description="Arbitrary JSON payload for the event",
    )


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class EventResponse(BaseModel):
    """Single audit entry — returned by POST and GET /audit/events/{id}."""

    id: uuid.UUID
    sequence_number: int
    event_type: str
    actor_id: str
    resource_type: str
    resource_id: str
    payload: dict[str, Any]
    payload_hash: str = Field(
        description="SHA-256 of original payload — unchanged even after redaction (Phase 4)"
    )
    entry_hash: str = Field(
        description="SHA-256 over all immutable event fields"
    )
    chain_hash: str = Field(
        description="SHA-256 of entry_hash || previous chain_hash"
    )
    created_at: datetime

    model_config = {"from_attributes": True}


class EventListResponse(BaseModel):
    """Paginated list of audit entries."""

    items: list[EventResponse]
    next_cursor: int | None = Field(
        default=None,
        description=(
            "Pass as ?after_sequence=<n> to fetch the next page. "
            "Null when this is the last page."
        ),
    )
    total_returned: int


# ---------------------------------------------------------------------------
# Verify schema
# ---------------------------------------------------------------------------

class VerifyResponse(BaseModel):
    """Result of GET /audit/verify — end-to-end chain integrity check."""

    valid: bool = Field(
        description="True only when every entry in the chain passes verification"
    )
    total_entries: int
    first_broken_sequence: int | None = Field(
        default=None,
        description="sequence_number of the first entry that failed, if any",
    )
    error: str | None = Field(
        default=None,
        description="Human-readable description of the first failure",
    )
