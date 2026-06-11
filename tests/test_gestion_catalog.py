"""[RED] Tests for gestion_catalog — outcome catalog, versioning, and lookup tables.

These tests run without any IO or fixtures (pure module-level assertions).
"""

from __future__ import annotations

import pytest

from features.analytics.gestion_catalog import (
    SCHEMA_VERSION,
    EventType,
    INTENT_TO_CAPABILITY,
    INTENT_TO_REASON,
    Outcome,
    TERMINAL_SIGNALS,
)


# ── Schema version ───────────────────────────────────────────────────────────

def test_schema_version_is_1():
    assert SCHEMA_VERSION == 1


# ── Outcome enum ─────────────────────────────────────────────────────────────

_EXPECTED_OUTCOMES = {
    "identified",
    "identification_failed",
    "info_provided",
    "payment_proof_submitted",
    "payment_commitment_registered",
    "escalated_to_agent",
    "not_understood",
    "unresolved",
}


def test_outcome_has_exactly_8_values():
    assert len(Outcome) == 8


def test_outcome_contains_all_expected_values():
    actual = {o.value for o in Outcome}
    assert actual == _EXPECTED_OUTCOMES


def test_outcome_is_str_enum():
    # str Enum values compare equal to plain strings
    assert Outcome.unresolved == "unresolved"
    assert Outcome.identification_failed == "identification_failed"


# ── EventType enum ───────────────────────────────────────────────────────────

_EXPECTED_EVENT_TYPES = {
    "capability_used",
    "credit_state_set",
    "terminal",
    "escalation",
    "commitment",
    "proof",
}


def test_event_type_has_exactly_6_values():
    assert len(EventType) == 6


def test_event_type_contains_all_expected_values():
    actual = {e.value for e in EventType}
    assert actual == _EXPECTED_EVENT_TYPES


def test_event_type_is_str_enum():
    assert EventType.terminal == "terminal"
    assert EventType.capability_used == "capability_used"


# ── INTENT_TO_CAPABILITY ─────────────────────────────────────────────────────

def test_intent_to_capability_is_nonempty_dict():
    assert isinstance(INTENT_TO_CAPABILITY, dict)
    assert len(INTENT_TO_CAPABILITY) > 0


def test_intent_to_capability_all_values_are_strings():
    for k, v in INTENT_TO_CAPABILITY.items():
        assert isinstance(k, str), f"key {k!r} is not str"
        assert isinstance(v, str), f"value {v!r} for key {k!r} is not str"


# ── INTENT_TO_REASON ─────────────────────────────────────────────────────────

_ESCALATION_INTENTS = {
    "cannot_pay",
    "requested_alternatives",
    "commitment_beyond_window",
    "wants_full_payment",
    "pay_installment",
    "proof_other_installment",
    "explicit_agent_request",
    "out_of_hours",
    "fallback_exhausted",
}


def test_intent_to_reason_covers_escalation_intents():
    """All escalation-related intents from design §4 must have a reason entry."""
    for intent in _ESCALATION_INTENTS:
        assert intent in INTENT_TO_REASON, f"Missing escalation intent in INTENT_TO_REASON: {intent!r}"


def test_intent_to_reason_values_are_strings():
    for k, v in INTENT_TO_REASON.items():
        assert isinstance(k, str), f"key {k!r} is not str"
        assert isinstance(v, str), f"value {v!r} for key {k!r} is not str"


# ── TERMINAL_SIGNALS ─────────────────────────────────────────────────────────

def test_terminal_signals_is_nonempty_dict():
    assert isinstance(TERMINAL_SIGNALS, dict)
    assert len(TERMINAL_SIGNALS) > 0
