"""Tests for gestion_derivation — signal-driven outcome derivation (Phase 3).

New signature: derive_outcome(*, session_state, resolved_intent, terminal_signal,
                              was_escalated, identity_failed, escalation_reason)

Parametrizes all 6 TerminalSignal values → expected Outcome.
Asserts identity_failed takes priority over any terminal_signal (B5-5).
Asserts None signal → Outcome.unresolved (B4-1).
No reference to TERMINAL_SIGNALS / INTENT_TO_REASON allowed.
"""

from __future__ import annotations

import pytest

from features.analytics.gestion_catalog import Outcome, TerminalSignal, OutcomeReason
from features.analytics.gestion_derivation import derive_outcome


# ---------------------------------------------------------------------------
# Helper: call with sensible defaults
# ---------------------------------------------------------------------------

def _derive(**overrides):
    defaults = dict(
        session_state={},
        resolved_intent=None,
        terminal_signal=None,
        was_escalated=False,
        identity_failed=False,
        escalation_reason=None,
    )
    defaults.update(overrides)
    return derive_outcome(**defaults)


# ---------------------------------------------------------------------------
# B4-1: None signal → Outcome.unresolved
# ---------------------------------------------------------------------------

def test_no_signal_yields_unresolved():
    outcome, reason = _derive()
    assert outcome == Outcome.unresolved
    assert reason is None


# ---------------------------------------------------------------------------
# B5-1: info_provided signal → Outcome.info_provided
# ---------------------------------------------------------------------------

def test_signal_info_provided():
    outcome, reason = _derive(terminal_signal=TerminalSignal.info_provided.value)
    assert outcome == Outcome.info_provided
    assert reason is None


# ---------------------------------------------------------------------------
# B5-2: proof signal → Outcome.payment_proof_submitted
# ---------------------------------------------------------------------------

def test_signal_proof():
    outcome, reason = _derive(terminal_signal=TerminalSignal.proof.value)
    assert outcome == Outcome.payment_proof_submitted
    assert reason is None


# ---------------------------------------------------------------------------
# commitment signal → Outcome.payment_commitment_registered
# ---------------------------------------------------------------------------

def test_signal_commitment():
    outcome, reason = _derive(terminal_signal=TerminalSignal.commitment.value)
    assert outcome == Outcome.payment_commitment_registered
    assert reason is None


# ---------------------------------------------------------------------------
# B5-3: escalation signal → Outcome.escalated_to_agent + reason
# ---------------------------------------------------------------------------

def test_signal_escalation_with_reason():
    outcome, reason = _derive(
        terminal_signal=TerminalSignal.escalation.value,
        escalation_reason=OutcomeReason.explicit_agent_request.value,
    )
    assert outcome == Outcome.escalated_to_agent
    assert reason == OutcomeReason.explicit_agent_request.value


def test_signal_escalation_no_reason():
    outcome, reason = _derive(terminal_signal=TerminalSignal.escalation.value)
    assert outcome == Outcome.escalated_to_agent
    assert reason is None


# was_escalated=True also triggers escalation even without signal
def test_was_escalated_flag_triggers_escalation():
    outcome, reason = _derive(was_escalated=True)
    assert outcome == Outcome.escalated_to_agent


# ---------------------------------------------------------------------------
# B5-4: fallback signal → Outcome.not_understood
# ---------------------------------------------------------------------------

def test_signal_fallback():
    outcome, reason = _derive(terminal_signal=TerminalSignal.fallback.value)
    assert outcome == Outcome.not_understood
    assert reason == OutcomeReason.fallback_exhausted.value


# ---------------------------------------------------------------------------
# identity_failed signal (TerminalSignal member) via terminal_signal param
# ---------------------------------------------------------------------------

def test_signal_identity_failed_via_terminal_signal():
    outcome, reason = _derive(terminal_signal=TerminalSignal.identity_failed.value)
    assert outcome == Outcome.identification_failed


# ---------------------------------------------------------------------------
# B5-5: identity_failed flag takes priority over any terminal_signal
# ---------------------------------------------------------------------------

def test_identity_failed_flag_beats_info_provided_signal():
    outcome, reason = _derive(
        identity_failed=True,
        terminal_signal=TerminalSignal.info_provided.value,
    )
    assert outcome == Outcome.identification_failed


def test_identity_failed_flag_beats_proof_signal():
    outcome, reason = _derive(
        identity_failed=True,
        terminal_signal=TerminalSignal.proof.value,
    )
    assert outcome == Outcome.identification_failed


def test_identity_failed_flag_beats_escalation():
    outcome, reason = _derive(
        identity_failed=True,
        was_escalated=True,
        terminal_signal=TerminalSignal.escalation.value,
    )
    assert outcome == Outcome.identification_failed


# ---------------------------------------------------------------------------
# Return type is always tuple[str, str | None]
# ---------------------------------------------------------------------------

def test_derive_outcome_returns_str_values():
    outcome, reason = _derive(terminal_signal=TerminalSignal.proof.value)
    assert isinstance(outcome, str)
    outcome2, reason2 = _derive(identity_failed=True)
    assert isinstance(outcome2, str)


# ---------------------------------------------------------------------------
# No reference to old dicts in derivation module source
# ---------------------------------------------------------------------------

def test_derivation_has_no_old_dict_references():
    import inspect
    import features.analytics.gestion_derivation as mod
    source = inspect.getsource(mod)
    for name in ("TERMINAL_SIGNALS", "INTENT_TO_REASON", "INTENT_TO_CAPABILITY"):
        assert name not in source, (
            f"gestion_derivation must not reference old dict: {name!r}"
        )


# ---------------------------------------------------------------------------
# Parametrize over all TerminalSignal values → confirm no crash + valid outcome
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("signal", [s.value for s in TerminalSignal])
def test_all_terminal_signals_produce_valid_outcome(signal):
    outcome, _ = _derive(terminal_signal=signal)
    assert outcome in {o.value for o in Outcome}, (
        f"signal {signal!r} produced unknown outcome: {outcome!r}"
    )
