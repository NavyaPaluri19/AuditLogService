"""
SQLAlchemy ORM model for audit log entries.

Schema evolves incrementally:
  001_initial_schema  — core chain fields (this file, Phase 2)
  002_add_redaction   — redacted_fields column (Phase 4)
  003_add_archival    — is_archived, archived_at columns (Phase 4)

Every field in this table is immutable after INSERT except columns added
in later migrations for redaction and archival.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.session import Base


class AuditEntry(Base):
    __tablename__ = "audit_entries"

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        comment="Stable external identifier (UUID v4)",
    )

    # Monotonic integer — assigned in the service layer under a FOR UPDATE
    # lock so it is gap-free and strictly increasing.
    sequence_number: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        unique=True,
        comment="Strictly increasing sequence — used for cursor-based pagination",
    )

    # ------------------------------------------------------------------
    # Core event fields (immutable — all included in entry_hash)
    # ------------------------------------------------------------------
    event_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="e.g. USER_LOGIN, RECORD_ACCESS, CONFIG_CHANGE",
    )
    actor_id: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        comment="Identity of the principal who triggered the event",
    )
    resource_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="Class of resource affected, e.g. ACCOUNT, TRADE, USER",
    )
    resource_id: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        comment="Specific resource identifier within resource_type",
    )

    # ------------------------------------------------------------------
    # Payload and its hash
    # ------------------------------------------------------------------
    payload: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        comment="Event payload — individual field values may be redacted to None in Phase 4",
    )
    payload_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="SHA-256 of the ORIGINAL payload — NEVER updated, even after redaction",
    )

    # ------------------------------------------------------------------
    # Hash chain (immutable after insert)
    # ------------------------------------------------------------------
    entry_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="SHA-256 over all core immutable fields + payload_hash",
    )
    chain_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        comment="SHA-256 of entry_hash || previous chain_hash — forms the tamper-evident chain",
    )

    # ------------------------------------------------------------------
    # Timestamp
    # ------------------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="UTC timestamp when the event was appended",
    )

    # ------------------------------------------------------------------
    # Phase 4 — redaction and archival (migrations 002 + 003)
    #
    # These three columns use the legacy Column() form rather than
    # Mapped[Optional[X]] to work around a Python 3.14 / SQLAlchemy
    # incompatibility: Union.__getitem__ changed in 3.14, breaking
    # SQLAlchemy's internal make_union_type() for any nullable annotation.
    # Column() bypasses annotation resolution entirely.
    # ------------------------------------------------------------------

    # Stores {field_name: SHA-256(field_name:original_value)} for every
    # payload field that has been redacted.  NULL until first redaction.
    redacted_fields = Column(
        JSON,
        nullable=True,
        default=None,
        comment=(
            "field_name → SHA-256(field_name:original_value) "
            "for each redacted payload field. NULL until first redaction."
        ),
    )

    # Soft-archive flag — row is never physically deleted.
    is_archived = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="True once POST /archive has been called on this entry",
    )

    # Set when is_archived transitions to True; NULL beforehand.
    archived_at = Column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="UTC timestamp when is_archived was set to true",
    )

    # ------------------------------------------------------------------
    # Indexes
    # ------------------------------------------------------------------
    __table_args__ = (
        Index("ix_audit_entries_seq",        "sequence_number"),
        Index("ix_audit_entries_event_type", "event_type"),
        Index("ix_audit_entries_actor_id",   "actor_id"),
        Index("ix_audit_entries_resource",   "resource_type", "resource_id"),
        Index("ix_audit_entries_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditEntry seq={self.sequence_number} "
            f"type={self.event_type} actor={self.actor_id}>"
        )
