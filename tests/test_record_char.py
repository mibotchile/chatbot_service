"""Characterization tests: Record(COBRANZA_SPEC) behavior — capture machine contract.

These tests were originally written to prove behavioral identity between Record and
the DebtorState shim (S1). After S8 deletes the shim, they become the standalone
behavioral contract for Record(COBRANZA_SPEC). Coverage is identical — same 14 cases,
same assertions — just without the shim comparison side.

Contract locked:
- Same level thresholds (CONTACT_FIELDS, INTEREST_FIELDS>=2, ENRICHMENT_FIELDS>=2)
- Same level strings: DEBTOR / DEBTOR_VERIFIED / PRE_DEBTOR / VISITOR
- Same get_status() structure: {level, collected, missing}
- Same update() behaviour: None values are dropped
- Same transition callback protocol: (prev_level, new_level, collected_dict)
- Same to_dict() / from_dict() round-trip
"""

from __future__ import annotations

import pytest

from features.cobranza.debtor import (
    COBRANZA_SPEC,
    CONTACT_FIELDS,
    ENRICHMENT_FIELDS,
    INTEREST_FIELDS,
)
from features.conversation.record import Record


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _contact_data() -> dict:
    return {f: f"value_{f}" for f in CONTACT_FIELDS}


def _interest_data() -> dict:
    fields = sorted(INTEREST_FIELDS)[:2]
    return {f: f"value_{f}" for f in fields}


def _enrichment_data() -> dict:
    fields = sorted(ENRICHMENT_FIELDS)[:2]
    return {f: f"value_{f}" for f in fields}


# ---------------------------------------------------------------------------
# Case 1: empty state → VISITOR
# ---------------------------------------------------------------------------

def test_char_empty_state_visitor():
    r = Record(spec=COBRANZA_SPEC)
    assert r.level == "VISITOR"


# ---------------------------------------------------------------------------
# Case 2: partial contact → VISITOR
# ---------------------------------------------------------------------------

def test_char_partial_contact_visitor():
    data = {"name": "Ana"}
    r = Record(spec=COBRANZA_SPEC, initial_data=data)
    assert r.level == "VISITOR"


# ---------------------------------------------------------------------------
# Case 3: two interest fields → PRE_DEBTOR
# ---------------------------------------------------------------------------

def test_char_two_interest_pre_debtor():
    data = _interest_data()
    r = Record(spec=COBRANZA_SPEC, initial_data=data)
    assert r.level == "PRE_DEBTOR"


# ---------------------------------------------------------------------------
# Case 4: only one interest field → VISITOR (threshold=2)
# ---------------------------------------------------------------------------

def test_char_one_interest_field_visitor():
    data = {list(INTEREST_FIELDS)[0]: "v"}
    r = Record(spec=COBRANZA_SPEC, initial_data=data)
    assert r.level == "VISITOR"


# ---------------------------------------------------------------------------
# Case 5: full contact → DEBTOR
# ---------------------------------------------------------------------------

def test_char_full_contact_debtor():
    data = _contact_data()
    r = Record(spec=COBRANZA_SPEC, initial_data=data)
    assert r.level == "DEBTOR"


# ---------------------------------------------------------------------------
# Case 6: full contact + 1 enrichment → DEBTOR (threshold not met)
# ---------------------------------------------------------------------------

def test_char_contact_plus_one_enrichment_debtor():
    data = {**_contact_data(), list(ENRICHMENT_FIELDS)[0]: "v"}
    r = Record(spec=COBRANZA_SPEC, initial_data=data)
    assert r.level == "DEBTOR"


# ---------------------------------------------------------------------------
# Case 7: full contact + 2 enrichment → DEBTOR_VERIFIED
# ---------------------------------------------------------------------------

def test_char_contact_and_enrichment_debtor_verified():
    data = {**_contact_data(), **_enrichment_data()}
    r = Record(spec=COBRANZA_SPEC, initial_data=data)
    assert r.level == "DEBTOR_VERIFIED"


# ---------------------------------------------------------------------------
# Case 8: interest + contact (both) → DEBTOR (contact wins)
# ---------------------------------------------------------------------------

def test_char_interest_and_contact_contact_wins():
    data = {**_interest_data(), **_contact_data()}
    r = Record(spec=COBRANZA_SPEC, initial_data=data)
    assert r.level == "DEBTOR"


# ---------------------------------------------------------------------------
# Case 9: update() with None values does not overwrite existing
# ---------------------------------------------------------------------------

def test_char_update_none_values_dropped():
    initial = {"name": "Pedro"}
    r = Record(spec=COBRANZA_SPEC, initial_data=initial)
    r.update({"name": None, "email": "x@y.com"})
    assert r.collected["name"] == "Pedro"
    assert r.collected["email"] == "x@y.com"
    assert r.level == "VISITOR"


# ---------------------------------------------------------------------------
# Case 10: transition callback protocol
# ---------------------------------------------------------------------------

def test_char_transition_callback_protocol():
    record_transitions: list[tuple[str, str]] = []

    def r_cb(prev: str, new: str, _data: dict) -> None:
        record_transitions.append((prev, new))

    r = Record(spec=COBRANZA_SPEC, on_transition=r_cb)

    r.update(_interest_data())
    r.update(_contact_data())

    assert len(record_transitions) == 2
    assert record_transitions[0] == ("VISITOR", "PRE_DEBTOR")
    assert record_transitions[1] == ("PRE_DEBTOR", "DEBTOR")


# ---------------------------------------------------------------------------
# Case 11: get_status() structure
# ---------------------------------------------------------------------------

def test_char_get_status_structure():
    data = _contact_data()
    r = Record(spec=COBRANZA_SPEC, initial_data=data)
    rs = r.get_status()
    assert rs["level"] == "DEBTOR"
    assert rs["collected"] == r.collected
    assert "missing" in rs
    assert isinstance(rs["missing"], list)


# ---------------------------------------------------------------------------
# Case 12: to_dict / from_dict round-trip
# ---------------------------------------------------------------------------

def test_char_to_dict_from_dict_roundtrip():
    data = {**_contact_data(), **_enrichment_data()}
    r = Record(spec=COBRANZA_SPEC, initial_data=data)
    d = r.to_dict()
    assert d["level"] == "DEBTOR_VERIFIED"
    r2 = Record.from_dict(spec=COBRANZA_SPEC, data=d)
    assert r2.level == r.level
    assert r2.collected == r.collected


# ---------------------------------------------------------------------------
# Case 13: DEBTOR_VERIFIED in _CONTACT_LEVELS set (webhook chain guard)
# ---------------------------------------------------------------------------

def test_char_debtor_verified_in_contact_levels():
    _CONTACT_LEVELS = {"DEBTOR", "DEBTOR_VERIFIED"}
    data = {**_contact_data(), **_enrichment_data()}
    r = Record(spec=COBRANZA_SPEC, initial_data=data)
    assert r.level in _CONTACT_LEVELS


# ---------------------------------------------------------------------------
# Case 14: all-fields populated → DEBTOR_VERIFIED (full saturation)
# ---------------------------------------------------------------------------

def test_char_all_fields_debtor_verified():
    all_fields = CONTACT_FIELDS | INTEREST_FIELDS | ENRICHMENT_FIELDS
    data = {f: f"v_{f}" for f in all_fields}
    r = Record(spec=COBRANZA_SPEC, initial_data=data)
    assert r.level == "DEBTOR_VERIFIED"
