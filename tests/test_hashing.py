"""
Tests for app/core/hashing.py

Run these first — everything in the service depends on these being correct.
  docker compose exec app pytest tests/test_hashing.py -v
  # or locally:
  pytest tests/test_hashing.py -v
"""

from datetime import datetime, timezone

import pytest

from app.core.hashing import (
    GENESIS_HASH,
    canonical_payload,
    compute_chain_hash,
    compute_entry_hash,
    hash_field_value,
    hash_payload,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TIMESTAMP = datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc)

SAMPLE_PAYLOAD = {
    "action": "view_balance",
    "account_number": "1234567890",
    "ip": "192.168.1.42",
}


# ---------------------------------------------------------------------------
# canonical_payload
# ---------------------------------------------------------------------------

def test_canonical_payload_is_sorted():
    """Keys must always be in sorted order regardless of input order."""
    p1 = canonical_payload({"b": 2, "a": 1})
    p2 = canonical_payload({"a": 1, "b": 2})
    assert p1 == p2


def test_canonical_payload_no_whitespace():
    result = canonical_payload({"key": "value"})
    assert " " not in result


def test_canonical_payload_nested():
    """Nested dicts should also be sorted."""
    result = canonical_payload({"z": {"b": 2, "a": 1}, "a": 0})
    assert result.index('"a"') < result.index('"z"')


# ---------------------------------------------------------------------------
# hash_payload
# ---------------------------------------------------------------------------

def test_hash_payload_deterministic():
    """Same payload must always produce the same hash."""
    h1 = hash_payload(SAMPLE_PAYLOAD)
    h2 = hash_payload(SAMPLE_PAYLOAD)
    assert h1 == h2


def test_hash_payload_is_64_chars():
    """SHA-256 hex digest is always 64 characters."""
    assert len(hash_payload(SAMPLE_PAYLOAD)) == 64


def test_hash_payload_key_order_independent():
    """Key insertion order must not affect the hash."""
    p1 = {"b": 2, "a": 1}
    p2 = {"a": 1, "b": 2}
    assert hash_payload(p1) == hash_payload(p2)


def test_hash_payload_sensitive_to_value_changes():
    """Changing any value must change the hash."""
    h1 = hash_payload({"field": "value1"})
    h2 = hash_payload({"field": "value2"})
    assert h1 != h2


def test_hash_payload_handles_unicode():
    """Non-ASCII characters must not silently produce wrong hashes."""
    h = hash_payload({"name": "José"})
    assert len(h) == 64


def test_hash_payload_empty_dict():
    h = hash_payload({})
    assert len(h) == 64


# ---------------------------------------------------------------------------
# compute_entry_hash
# ---------------------------------------------------------------------------

def test_entry_hash_deterministic():
    h1 = compute_entry_hash(1, "LOGIN", "user:u1", "account", "acct:A1", "abc123", SAMPLE_TIMESTAMP)
    h2 = compute_entry_hash(1, "LOGIN", "user:u1", "account", "acct:A1", "abc123", SAMPLE_TIMESTAMP)
    assert h1 == h2


def test_entry_hash_is_64_chars():
    h = compute_entry_hash(1, "LOGIN", "user:u1", "account", "acct:A1", "abc123", SAMPLE_TIMESTAMP)
    assert len(h) == 64


def test_entry_hash_sensitive_to_sequence_number():
    h1 = compute_entry_hash(1, "LOGIN", "user:u1", "account", "acct:A1", "abc123", SAMPLE_TIMESTAMP)
    h2 = compute_entry_hash(2, "LOGIN", "user:u1", "account", "acct:A1", "abc123", SAMPLE_TIMESTAMP)
    assert h1 != h2


def test_entry_hash_sensitive_to_payload_hash():
    """This is the critical test: changing payload_hash must change entry_hash."""
    h1 = compute_entry_hash(1, "LOGIN", "user:u1", "account", "acct:A1", "hash_original", SAMPLE_TIMESTAMP)
    h2 = compute_entry_hash(1, "LOGIN", "user:u1", "account", "acct:A1", "hash_tampered", SAMPLE_TIMESTAMP)
    assert h1 != h2


def test_entry_hash_sensitive_to_actor():
    h1 = compute_entry_hash(1, "LOGIN", "user:u1", "account", "acct:A1", "abc", SAMPLE_TIMESTAMP)
    h2 = compute_entry_hash(1, "LOGIN", "user:u2", "account", "acct:A1", "abc", SAMPLE_TIMESTAMP)
    assert h1 != h2


# ---------------------------------------------------------------------------
# compute_chain_hash
# ---------------------------------------------------------------------------

def test_chain_hash_deterministic():
    h = compute_chain_hash("entry_abc", "prev_xyz")
    assert h == compute_chain_hash("entry_abc", "prev_xyz")


def test_chain_hash_is_64_chars():
    assert len(compute_chain_hash("a" * 64, "b" * 64)) == 64


def test_chain_hash_sensitive_to_previous():
    h1 = compute_chain_hash("same_entry", "prev_A")
    h2 = compute_chain_hash("same_entry", "prev_B")
    assert h1 != h2


def test_genesis_hash_is_64_zeros():
    assert GENESIS_HASH == "0" * 64
    assert len(GENESIS_HASH) == 64


# ---------------------------------------------------------------------------
# hash_field_value (redaction support)
# ---------------------------------------------------------------------------

def test_field_hash_deterministic():
    h1 = hash_field_value("account_number", "1234567890")
    h2 = hash_field_value("account_number", "1234567890")
    assert h1 == h2


def test_field_hash_sensitive_to_field_name():
    """Same value under different field names must produce different hashes."""
    h1 = hash_field_value("field_a", "same_value")
    h2 = hash_field_value("field_b", "same_value")
    assert h1 != h2


def test_field_hash_sensitive_to_value():
    h1 = hash_field_value("account_number", "1111")
    h2 = hash_field_value("account_number", "2222")
    assert h1 != h2


def test_field_hash_is_64_chars():
    assert len(hash_field_value("field", "value")) == 64
