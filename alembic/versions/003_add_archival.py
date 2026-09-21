"""Add archival columns

Revision ID: 003
Revises: 002
Create Date: 2026-09-21

Adds is_archived and archived_at columns for soft-delete retention (Phase 4).

Physical deletion of a row would break the chain because the next row's
previous_hash would point to a row that no longer exists.  Soft-archival
keeps the row — and all its hash fields — intact so GET /audit/verify
continues to walk end-to-end and report valid.

is_archived:  boolean flag, default false
archived_at:  UTC timestamp set when is_archived transitions to true (nullable)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_entries",
        sa.Column(
            "is_archived",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="True once POST /archive has been called on this entry",
        ),
    )
    op.add_column(
        "audit_entries",
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="UTC timestamp when is_archived was set to true",
        ),
    )


def downgrade() -> None:
    op.drop_column("audit_entries", "archived_at")
    op.drop_column("audit_entries", "is_archived")
