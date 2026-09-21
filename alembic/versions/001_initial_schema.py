"""Initial schema — audit_entries core chain fields

Revision ID: 001
Revises:
Create Date: 2026-09-21

Creates the audit_entries table with only the fields needed for Phase 2:
core identity, event fields, payload + payload_hash, hash chain, and timestamp.

Redaction fields (redacted_fields) → migration 002
Archival fields (is_archived, archived_at) → migration 003
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_entries",

        # Identity
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "sequence_number",
            sa.BigInteger(),
            nullable=False,
            comment="Strictly increasing sequence — used for cursor-based pagination",
        ),

        # Core event fields (immutable)
        sa.Column("event_type",    sa.String(128), nullable=False),
        sa.Column("actor_id",      sa.String(256), nullable=False),
        sa.Column("resource_type", sa.String(128), nullable=False),
        sa.Column("resource_id",   sa.String(256), nullable=False),

        # Payload
        sa.Column("payload",      sa.JSON(), nullable=False),
        sa.Column(
            "payload_hash",
            sa.String(64),
            nullable=False,
            comment="SHA-256 of ORIGINAL payload — never updated after insert",
        ),

        # Hash chain (immutable)
        sa.Column("entry_hash", sa.String(64), nullable=False),
        sa.Column(
            "chain_hash",
            sa.String(64),
            nullable=False,
            unique=True,
            comment="SHA-256 of entry_hash || previous chain_hash",
        ),

        # Timestamp
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_unique_constraint(
        "uq_audit_entries_sequence_number",
        "audit_entries",
        ["sequence_number"],
    )

    op.create_index("ix_audit_entries_seq",        "audit_entries", ["sequence_number"])
    op.create_index("ix_audit_entries_event_type", "audit_entries", ["event_type"])
    op.create_index("ix_audit_entries_actor_id",   "audit_entries", ["actor_id"])
    op.create_index(
        "ix_audit_entries_resource",
        "audit_entries",
        ["resource_type", "resource_id"],
    )
    op.create_index("ix_audit_entries_created_at", "audit_entries", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_entries")
