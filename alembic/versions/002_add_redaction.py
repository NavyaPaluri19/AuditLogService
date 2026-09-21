"""Add redacted_fields column

Revision ID: 002
Revises: 001
Create Date: 2026-09-21

Adds the redacted_fields JSON column introduced in Phase 4.

redacted_fields stores a mapping of { field_name: SHA-256(field_name:original_value) }
for every payload field that has been redacted.  The column is NULL until at least
one field is redacted.

The payload column is updated when fields are redacted (individual values set to None),
but payload_hash, entry_hash, and chain_hash are NEVER touched — the chain remains
valid and fully verifiable after any redaction.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_entries",
        sa.Column(
            "redacted_fields",
            sa.JSON(),
            nullable=True,
            comment=(
                "field_name → SHA-256(field_name:original_value) "
                "for each redacted payload field. NULL until first redaction."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("audit_entries", "redacted_fields")
