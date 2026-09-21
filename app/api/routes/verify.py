"""
Verify route — end-to-end chain integrity check.

GET /audit/verify

Walks every entry in ascending sequence order and recomputes both
entry_hash and chain_hash from stored immutable fields.  Reports the
first entry that fails and the total entries checked.

This route is intentionally read-only and performs no writes.  It is
safe to call repeatedly and is idempotent.

Performance note: the full scan is O(n) in the number of entries.
For very large chains a background integrity checker writing results
to a separate table would be preferred (see DECISIONS.md for rationale
on keeping this simple for Phase 3).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.event import VerifyResponse
from app.services import chain_service

router = APIRouter()


@router.get(
    "/verify",
    response_model=VerifyResponse,
    summary="Verify end-to-end chain integrity",
    description=(
        "Recomputes entry_hash and chain_hash for every entry in "
        "ascending sequence order. Returns valid=true only when all "
        "entries pass. On failure, first_broken_sequence identifies "
        "where the chain breaks."
    ),
)
async def verify_chain(
    db: AsyncSession = Depends(get_db),
) -> VerifyResponse:
    result = await chain_service.verify_chain(db)
    return VerifyResponse(**result)
