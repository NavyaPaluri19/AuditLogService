"""
Core hashing module — pure functions, no DB, no FastAPI dependencies.

All chain integrity rests on these functions being correct and deterministic.
Test this module exhaustively before touching anything else.
"""

import hashlib
import json
from datetime import datetime

# The hash value used as `previous_hash` for the very first record.
# A well-known constant so genesis detection is explicit, not implicit.
GENESIS_HASH = "0" * 64


# ---------------------------------------------------------------------------
# Payload hashing
# ---------------------------------------------------------------------------

def canonical_payload(payload: dict) -> str:
    """
    Deterministic JSON representation of a payload dict.

    Rules that guarantee determinism:
      - Keys sorted alphabetically (sort_keys=True)
      - No extra whitespace (separators without spaces)
      - Non-serialisable values (datetime, UUID) cast to str via default=str
      - UTF-8 encoded — explicitly handled in hash_payload()
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def hash_payload(payload: dict) -> str:
    """
    SHA-256 of the canonical payload.

    This value is stored on the row and NEVER changes — even after field
    redaction. The entry_hash and chain_hash are both derived from this,
    not from the raw payload, which is what makes redaction chain-safe.
    """
    return hashlib.sha256(
        canonical_payload(payload).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Entry hash
# ---------------------------------------------------------------------------

def compute_entry_hash(
    sequence_number: int,
    event_type: str,
    actor_id: str,
    resource_type: str,
    resource_id: str,
    payload_hash: str,      # <-- always the ORIGINAL payload_hash, never raw payload
    timestamp: datetime,
) -> str:
    """
    SHA-256 over all core immutable event fields joined by a pipe delimiter.

    Why pipe (|)?  It is not a valid character in ISO 8601 timestamps,
    UUIDs, or typical event_type / resource strings — so it cannot cause
    accidental collisions from concatenated field values.

    The timestamp is serialised as a full ISO 8601 string with timezone
    so the hash is stable regardless of the Python datetime object's tzinfo state.
    """
    parts = "|".join([
        str(sequence_number),
        event_type,
        actor_id,
        resource_type,
        resource_id,
        payload_hash,
        timestamp.isoformat(),
    ])
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Chain hash
# ---------------------------------------------------------------------------

def compute_chain_hash(entry_hash: str, previous_hash: str) -> str:
    """
    SHA-256 of the concatenation of entry_hash and previous_hash.

    Storing this separately from entry_hash means:
      - Chain verification can walk forward in one pass
      - Tampering with previous_hash is detected independently of entry_hash
    """
    combined = entry_hash + previous_hash
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Field-level hashing (used by redaction)
# ---------------------------------------------------------------------------

def hash_field_value(field_name: str, value) -> str:
    """
    SHA-256 of "field_name:canonical_value".

    Stored in redacted_fields so a verifier can prove a redacted field
    held a specific value without revealing the value itself — the
    claimant provides the original value, verifier recomputes this hash
    and compares to the stored one.
    """
    raw = f"{field_name}:{json.dumps(value, default=str)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
