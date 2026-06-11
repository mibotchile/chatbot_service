"""[RED] Tests for gestion_derivation — pure outcome derivation logic.

One test per priority branch (8 branches), precedence tests, and
_reason_for_intent table-driven tests. No IO, no fixtures required.
"""

from __future__ import annotations

import pytest

from features.analytics.gestion_derivation import _reason_for_intent, derive_outcome
from features.analytics.gestion_catalog import INTENT_TO_REASON, Outcome


# ── Helper: default no-flags call ────────────────────────────────────────────

def _derive(**overrides):
    """Call derive_outcome with all flags False by default."""
    defaults = dict(
        session_state={},
        resolved_intent=None,
        was_escalated=False,
        identity_failed=False,
        commitment_registered=False,
        proof_submitted=False,
        fallback_exhausted=False,
        identified=False,
        info_provided=False,
    )
    defaults.update(overrides)
    return derive_outcome(**defaults)


# ── Priority branch 1: identity_failed ───────────────────────────────────────

def test_priority_1_identity_failed():
    outcome, reason = _derive(identity_failed=True)
    assert outcome == Outcome.identification_failed
    assert reason == "max_identification_retries"


# ── Priority branch 2: commitment_registered ─────────────────────────────────

def test_priority_2_commitment_registered():
    outcome, reason = _derive(commitment_registered=True)
    assert outcome == Outcome.payment_commitment_registered
    assert reason is None


# ── Priority branch 3: proof_submitted ───────────────────────────────────────

def test_priority_3_proof_submitted():
    outcome, reason = _derive(proof_submitted=True)
    assert outcome == Outcome.payment_proof_submitted
    assert reason is None


# ── Priority branch 4: was_escalated ─────────────────────────────────────────

def test_priority_4_escalated_with_known_intent():
    outcome, reason = _derive(
        was_escalated=True,
        resolved_intent="commitment_beyond_window",
    )
    assert outcome == Outcome.escalated_to_agent
    assert reason == "commitment_beyond_window"


def test_priority_4_escalated_with_unknown_intent_returns_none_reason():
    outcome, reason = _derive(
        was_escalated=True,
        resolved_intent="some_unknown_intent",
    )
    assert outcome == Outcome.escalated_to_agent
    assert reason is None


def test_priority_4_escalated_with_no_intent():
    outcome, reason = _derive(was_escalated=True, resolved_intent=None)
    assert outcome == Outcome.escalated_to_agent
    assert reason is None


# ── Priority branch 5: fallback_exhausted ────────────────────────────────────

def test_priority_5_fallback_exhausted():
    outcome, reason = _derive(fallback_exhausted=True)
    assert outcome == Outcome.not_understood
    assert reason == "fallback_exhausted"


# ── Priority branch 6: info_provided ─────────────────────────────────────────

def test_priority_6_info_provided():
    outcome, reason = _derive(info_provided=True)
    assert outcome == Outcome.info_provided
    assert reason is None


# ── Priority branch 7: identified ────────────────────────────────────────────

def test_priority_7_identified():
    outcome, reason = _derive(identified=True)
    assert outcome == Outcome.identified
    assert reason is None


# ── Priority branch 8: default unresolved ────────────────────────────────────

def test_priority_8_default_unresolved():
    outcome, reason = _derive()
    assert outcome == Outcome.unresolved
    assert reason is None


# ── Return type is always tuple[str, str | None] ─────────────────────────────

def test_derive_outcome_returns_str_values():
    outcome, reason = _derive(commitment_registered=True)
    assert isinstance(outcome, str)
    # reason may be None for some outcomes — check the string case
    outcome2, reason2 = _derive(identity_failed=True)
    assert isinstance(reason2, str)


# ── Precedence tests ─────────────────────────────────────────────────────────

def test_precedence_identity_failed_beats_commitment():
    """Priority 1 beats priority 2."""
    outcome, _ = _derive(identity_failed=True, commitment_registered=True)
    assert outcome == Outcome.identification_failed


def test_precedence_identity_failed_beats_escalated():
    """Priority 1 beats priority 4."""
    outcome, _ = _derive(identity_failed=True, was_escalated=True)
    assert outcome == Outcome.identification_failed


def test_precedence_commitment_beats_info_provided():
    """Priority 2 beats priority 6 (R1-a scenario)."""
    outcome, reason = _derive(commitment_registered=True, info_provided=True)
    assert outcome == Outcome.payment_commitment_registered
    assert reason is None


def test_precedence_proof_beats_escalated():
    """Priority 3 beats priority 4."""
    outcome, _ = _derive(proof_submitted=True, was_escalated=True)
    assert outcome == Outcome.payment_proof_submitted


def test_precedence_escalated_beats_fallback():
    """Priority 4 beats priority 5."""
    outcome, _ = _derive(was_escalated=True, fallback_exhausted=True)
    assert outcome == Outcome.escalated_to_agent


def test_precedence_fallback_beats_info_provided():
    """Priority 5 beats priority 6."""
    outcome, _ = _derive(fallback_exhausted=True, info_provided=True)
    assert outcome == Outcome.not_understood


def test_precedence_info_provided_beats_identified():
    """Priority 6 beats priority 7."""
    outcome, _ = _derive(info_provided=True, identified=True)
    assert outcome == Outcome.info_provided


# ── _reason_for_intent table-driven ─────────────────────────────────────────

@pytest.mark.parametrize("intent,expected_reason", list(INTENT_TO_REASON.items()))
def test_reason_for_intent_known_intents(intent, expected_reason):
    assert _reason_for_intent(intent) == expected_reason


def test_reason_for_intent_unknown_returns_none():
    assert _reason_for_intent("totally_unknown_intent") is None


def test_reason_for_intent_none_input_returns_none():
    assert _reason_for_intent(None) is None
