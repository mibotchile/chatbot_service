"""Behavioral tests for DebtorState.level — runtime vocabulary contract.

These tests instantiate DebtorState with real data and assert the emitted
level strings match the post-rename vocabulary (DEBTOR, DEBTOR_VERIFIED,
PRE_DEBTOR, VISITOR).  They will FAIL if debtor_state.py still returns
the old vocabulary (LEAD, LEAD_ENRICHED, PRE_LEAD).

This is the lock the source-inspection tests in test_storage_migration.py
could not provide — they exercise actual runtime behavior.
"""

from __future__ import annotations

import pytest

from features.conversation.debtor_state import (
    CONTACT_FIELDS,
    ENRICHMENT_FIELDS,
    INTEREST_FIELDS,
    DebtorState,
)

# ---------------------------------------------------------------------------
# Helpers — build collected dicts that reach specific levels
# ---------------------------------------------------------------------------

def _contact_data() -> dict:
    """Minimal data that satisfies all CONTACT_FIELDS."""
    return {f: f"value_{f}" for f in CONTACT_FIELDS}


def _interest_data() -> dict:
    """Two INTEREST_FIELDS — enough for PRE_DEBTOR."""
    fields = list(INTEREST_FIELDS)[:2]
    return {f: f"value_{f}" for f in fields}


def _enrichment_data() -> dict:
    """Two ENRICHMENT_FIELDS — enough to reach DEBTOR_VERIFIED when contact also set."""
    fields = list(ENRICHMENT_FIELDS)[:2]
    return {f: f"value_{f}" for f in fields}


# ---------------------------------------------------------------------------
# VISITOR — no qualifying data
# ---------------------------------------------------------------------------

def test_level_visitor_with_no_data():
    """Empty state must return 'VISITOR'."""
    state = DebtorState()
    assert state.level == "VISITOR"


def test_level_visitor_with_partial_contact():
    """Partial contact fields (< full set) must still return 'VISITOR' when no interest."""
    state = DebtorState(initial_data={"name": "Juan"})
    assert state.level == "VISITOR"


# ---------------------------------------------------------------------------
# PRE_DEBTOR — interest signals without contact
# ---------------------------------------------------------------------------

def test_level_pre_debtor_with_interest_fields():
    """Two interest fields but no contact → PRE_DEBTOR (was PRE_LEAD)."""
    state = DebtorState(initial_data=_interest_data())
    assert state.level == "PRE_DEBTOR", (
        f"Expected PRE_DEBTOR, got {state.level!r} — "
        "debtor_state.py must return PRE_DEBTOR not PRE_LEAD"
    )
    # Must NOT be the old vocabulary
    assert state.level != "PRE_LEAD"


# ---------------------------------------------------------------------------
# DEBTOR — contact complete, no enrichment
# ---------------------------------------------------------------------------

def test_level_debtor_with_full_contact():
    """Full contact fields (name+phone+email) → DEBTOR (was LEAD)."""
    state = DebtorState(initial_data=_contact_data())
    assert state.level == "DEBTOR", (
        f"Expected DEBTOR, got {state.level!r} — "
        "debtor_state.py must return DEBTOR not LEAD"
    )
    assert state.level != "LEAD"


def test_debtor_level_in_contact_levels_set():
    """DEBTOR must be in _CONTACT_LEVELS — this locks the capture webhook chain."""
    _CONTACT_LEVELS = {"DEBTOR", "DEBTOR_VERIFIED"}
    state = DebtorState(initial_data=_contact_data())
    assert state.level in _CONTACT_LEVELS, (
        f"state.level={state.level!r} not in _CONTACT_LEVELS={_CONTACT_LEVELS} — "
        "on_lead_captured webhook would never fire"
    )


# ---------------------------------------------------------------------------
# DEBTOR_VERIFIED — contact + enrichment
# ---------------------------------------------------------------------------

def test_level_debtor_verified_with_contact_and_enrichment():
    """Full contact + two enrichment fields → DEBTOR_VERIFIED (was LEAD_ENRICHED)."""
    data = {**_contact_data(), **_enrichment_data()}
    state = DebtorState(initial_data=data)
    assert state.level == "DEBTOR_VERIFIED", (
        f"Expected DEBTOR_VERIFIED, got {state.level!r} — "
        "debtor_state.py must return DEBTOR_VERIFIED not LEAD_ENRICHED"
    )
    assert state.level != "LEAD_ENRICHED"


def test_debtor_verified_in_contact_levels_set():
    """DEBTOR_VERIFIED must be in _CONTACT_LEVELS."""
    _CONTACT_LEVELS = {"DEBTOR", "DEBTOR_VERIFIED"}
    data = {**_contact_data(), **_enrichment_data()}
    state = DebtorState(initial_data=data)
    assert state.level in _CONTACT_LEVELS


# ---------------------------------------------------------------------------
# Transition callback uses new vocabulary
# ---------------------------------------------------------------------------

def test_transition_callback_receives_new_vocabulary():
    """on_transition callback must receive new-vocabulary level strings."""
    transitions: list[tuple[str, str]] = []

    def _capture(prev: str, new: str, _data: dict) -> None:
        transitions.append((prev, new))

    state = DebtorState(on_transition=_capture)

    # VISITOR → PRE_DEBTOR
    state.update(_interest_data())
    # PRE_DEBTOR → DEBTOR (add contact)
    state.update(_contact_data())

    assert len(transitions) == 2

    prev0, new0 = transitions[0]
    assert prev0 == "VISITOR"
    assert new0 == "PRE_DEBTOR", f"Expected PRE_DEBTOR, got {new0!r}"

    prev1, new1 = transitions[1]
    assert prev1 == "PRE_DEBTOR", f"Expected PRE_DEBTOR, got {prev1!r}"
    assert new1 == "DEBTOR", f"Expected DEBTOR, got {new1!r}"


# ---------------------------------------------------------------------------
# get_status returns new vocabulary
# ---------------------------------------------------------------------------

def test_get_status_level_uses_new_vocabulary():
    """get_status()['level'] must use the new vocabulary."""
    state = DebtorState(initial_data=_contact_data())
    status = state.get_status()
    assert status["level"] == "DEBTOR"
    assert status["level"] != "LEAD"


def test_to_dict_level_uses_new_vocabulary():
    """to_dict()['level'] must use the new vocabulary."""
    data = {**_contact_data(), **_enrichment_data()}
    state = DebtorState(initial_data=data)
    d = state.to_dict()
    assert d["level"] == "DEBTOR_VERIFIED"
    assert d["level"] != "LEAD_ENRICHED"
