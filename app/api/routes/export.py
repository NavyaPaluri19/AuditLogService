"""
Export route — Phase 4.

  GET /audit/export?format=json|csv&include_archived=true|false
                   &actor_id=<str>&resource_type=<str>&resource_id=<str>

Streams audit entries without buffering the full result set in memory.
Large exports go directly to the response as they are fetched from the DB.

Optional actor_id / resource_type / resource_id filters let callers export
a self-contained, verifiable bundle for a specific actor or resource
(Scenario B requirement).
"""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import export_service

router = APIRouter()


@router.get(
    "/export",
    summary="Export audit entries",
    description=(
        "Stream all audit entries as JSON (default) or CSV. "
        "Set `format=csv` for a CSV download. "
        "Archived entries are included by default; pass "
        "`include_archived=false` to exclude them. "
        "Optionally filter by actor_id, resource_type, or resource_id to "
        "produce a self-contained verifiable bundle for a specific actor or resource."
    ),
    responses={
        200: {
            "content": {
                "application/json": {},
                "text/csv": {},
            },
            "description": "Streamed export of audit entries",
        }
    },
)
async def export_events(
    format: str = Query(
        default="json",
        pattern="^(json|csv)$",
        description="Output format: 'json' (default) or 'csv'",
    ),
    include_archived: bool = Query(
        default=True,
        description="Whether to include archived entries (default: true)",
    ),
    actor_id: str | None = Query(
        default=None,
        description="Filter export to entries for this actor_id only",
    ),
    resource_type: str | None = Query(
        default=None,
        description="Filter export to entries for this resource_type only",
    ),
    resource_id: str | None = Query(
        default=None,
        description="Filter export to entries for this resource_id only",
    ),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    if format == "csv":
        return StreamingResponse(
            export_service.export_csv(
                db,
                include_archived=include_archived,
                actor_id=actor_id,
                resource_type=resource_type,
                resource_id=resource_id,
            ),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=audit_export.csv"
            },
        )

    return StreamingResponse(
        export_service.export_json(
            db,
            include_archived=include_archived,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
        ),
        media_type="application/json",
        headers={
            "Content-Disposition": "attachment; filename=audit_export.json"
        },
    )
