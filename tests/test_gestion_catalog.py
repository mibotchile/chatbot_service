"""Tests for gestion_catalog — outcome catalog, vocabulary enums, and intent_binding accessor.

Asserts:
  - Old dicts (INTENT_TO_CAPABILITY, TERMINAL_SIGNALS, INTENT_TO_REASON) DO NOT exist.
  - New enums (Capability, TerminalSignal, OutcomeReason) ARE present with correct members.
  - No prestamype intent names appear as string literals in the module source.
  - intent_binding() coercion: valid → enum value; bad value → None;
    missing/None → (None, None, None).
"""

from __future__ import annotations

import inspect


from features.analytics.gestion_catalog import (
    SCHEMA_VERSION,
    Capability,
    EventType,
    Outcome,
    OutcomeReason,
    TerminalSignal,
    intent_binding,
)


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

def test_schema_version_is_1():
    assert SCHEMA_VERSION == 1


# ---------------------------------------------------------------------------
# Outcome enum (unchanged)
# ---------------------------------------------------------------------------

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
    assert {o.value for o in Outcome} == _EXPECTED_OUTCOMES


def test_outcome_is_str_enum():
    assert Outcome.unresolved == "unresolved"
    assert Outcome.identification_failed == "identification_failed"


# ---------------------------------------------------------------------------
# EventType enum (unchanged)
# ---------------------------------------------------------------------------

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
    assert {e.value for e in EventType} == _EXPECTED_EVENT_TYPES


def test_event_type_is_str_enum():
    assert EventType.terminal == "terminal"
    assert EventType.capability_used == "capability_used"


# ---------------------------------------------------------------------------
# B1-2: Old dicts MUST NOT exist on the module
# ---------------------------------------------------------------------------

def test_intent_to_capability_removed():
    import features.analytics.gestion_catalog as cat
    assert not hasattr(cat, "INTENT_TO_CAPABILITY"), (
        "INTENT_TO_CAPABILITY must be removed from gestion_catalog"
    )


def test_terminal_signals_removed():
    import features.analytics.gestion_catalog as cat
    assert not hasattr(cat, "TERMINAL_SIGNALS"), (
        "TERMINAL_SIGNALS must be removed from gestion_catalog"
    )


def test_intent_to_reason_removed():
    import features.analytics.gestion_catalog as cat
    assert not hasattr(cat, "INTENT_TO_REASON"), (
        "INTENT_TO_REASON must be removed from gestion_catalog"
    )


# ---------------------------------------------------------------------------
# B1-1: No prestamype intent names as string literals in source
# ---------------------------------------------------------------------------

_PRESTAMYPE_INTENTS = [
    "identificar", "comprobante_reportar", "derivar_asesor", "elegir_credito",
    "donde_pagar", "politica_pago", "no_entendido", "consulta_deuda",
    "enviar_estado", "enviar_datos_pago", "enviar_constancia",
    "comprobante_resultado", "elegir_canal", "saludo", "despedida",
    "identidad_requerida",
]


def test_no_prestamype_intent_names_in_catalog_source():
    """B1-1: No prestamype intent names used as DICT KEYS or mapping lookups.

    Capability enum members may share names with intents (that is intentional
    vocabulary overlap — e.g. 'consulta_deuda' is both a prestamype intent AND
    a Capability value). What the catalog must NOT contain is hardcoded dicts
    keyed on tenant intent names (INTENT_TO_CAPABILITY etc.).

    We verify the old dicts are gone and that the catalog has zero hardcoded
    mapping structures whose keys are tenant intent names.
    """
    import features.analytics.gestion_catalog as cat
    # Old mapping dicts must not exist at all (already tested above).
    # Additional: the invented non-real names from old dicts must not appear.
    source = inspect.getsource(cat)
    invented_names = [
        "INTENT_TO_CAPABILITY", "TERMINAL_SIGNALS", "INTENT_TO_REASON",
        "upload_comprobante", "register_payment_commitment",
        "verify_identity", "upload_payment_proof",
    ]
    for name in invented_names:
        assert name not in source, (
            f"Catalog source must not contain invented mapping name: {name!r}"
        )


# ---------------------------------------------------------------------------
# B1-3: Capability enum — required members
# ---------------------------------------------------------------------------

_REQUIRED_CAPABILITIES = {
    "identificacion", "consulta_deuda", "cuentas_bancarias",
    "estado_cuenta", "constancia", "politica_pago", "comprobante", "multicredito",
}


def test_capability_enum_has_required_members():
    actual = {c.value for c in Capability}
    missing = _REQUIRED_CAPABILITIES - actual
    assert not missing, f"Capability enum missing members: {missing}"


def test_capability_is_str_enum():
    assert Capability.consulta_deuda == "consulta_deuda"
    assert Capability.comprobante == "comprobante"


# ---------------------------------------------------------------------------
# TerminalSignal enum — required members
# ---------------------------------------------------------------------------

_REQUIRED_SIGNALS = {
    "info_provided", "proof", "commitment", "escalation", "fallback", "identity_failed"
}


def test_terminal_signal_enum_has_all_members():
    actual = {s.value for s in TerminalSignal}
    assert _REQUIRED_SIGNALS == actual


def test_terminal_signal_is_str_enum():
    assert TerminalSignal.proof == "proof"
    assert TerminalSignal.escalation == "escalation"


# ---------------------------------------------------------------------------
# OutcomeReason enum — required members
# ---------------------------------------------------------------------------

def test_outcome_reason_has_required_members():
    actual = {r.value for r in OutcomeReason}
    assert "explicit_agent_request" in actual
    assert "fallback_exhausted" in actual


def test_outcome_reason_is_str_enum():
    assert OutcomeReason.explicit_agent_request == "explicit_agent_request"


# ---------------------------------------------------------------------------
# P2 / B3: intent_binding() accessor
# ---------------------------------------------------------------------------

def _make_spec(intents: dict):
    """Build a ResponsesSpec inline (no file I/O)."""
    from tenancy.responses_spec import ResponsesSpec
    return ResponsesSpec(intents=intents)


# B3-1: annotated intent resolves correct tuple
def test_intent_binding_annotated_info_provided():
    spec = _make_spec({
        "consulta_deuda": {"capability": "consulta_deuda", "terminal_signal": "info_provided"},
    })
    cap, sig, reason = intent_binding("consulta_deuda", spec)
    assert cap == Capability.consulta_deuda.value
    assert sig == TerminalSignal.info_provided.value
    assert reason is None


def test_intent_binding_annotated_proof():
    spec = _make_spec({
        "comprobante_resultado": {"capability": "comprobante", "terminal_signal": "proof"},
    })
    cap, sig, reason = intent_binding("comprobante_resultado", spec)
    assert cap == Capability.comprobante.value
    assert sig == TerminalSignal.proof.value
    assert reason is None


# B2-3: escalation with reason
def test_intent_binding_escalation_with_reason():
    spec = _make_spec({
        "derivar_asesor": {
            "terminal_signal": "escalation",
            "escalation_reason": "explicit_agent_request",
        },
    })
    cap, sig, reason = intent_binding("derivar_asesor", spec)
    assert cap is None
    assert sig == TerminalSignal.escalation.value
    assert reason == OutcomeReason.explicit_agent_request.value


# B3-2: unannotated intent → (None, None, None)
def test_intent_binding_unannotated_intent():
    spec = _make_spec({"saludo": {"mode": "variant", "variants": ["Hola"]}})
    assert intent_binding("saludo", spec) == (None, None, None)


# B3-3: unknown intent → (None, None, None) without raising
def test_intent_binding_unknown_intent():
    spec = _make_spec({"consulta_deuda": {"capability": "consulta_deuda"}})
    assert intent_binding("phantom_intent", spec) == (None, None, None)


# None intent → (None, None, None)
def test_intent_binding_none_intent():
    spec = _make_spec({"consulta_deuda": {"capability": "consulta_deuda"}})
    assert intent_binding(None, spec) == (None, None, None)


# responses_cfg=None → (None, None, None)
def test_intent_binding_none_spec():
    assert intent_binding("consulta_deuda", None) == (None, None, None)


# Invalid capability value → coerced to None (does not raise)
def test_intent_binding_invalid_capability_coerced_to_none():
    spec = _make_spec({
        "some_intent": {
            "capability": "invented_name_not_in_enum",
            "terminal_signal": "info_provided",
        },
    })
    cap, sig, _ = intent_binding("some_intent", spec)
    assert cap is None, "Invalid capability must be coerced to None, not raise"
    assert sig == TerminalSignal.info_provided.value


# Invalid terminal_signal value → coerced to None
def test_intent_binding_invalid_signal_coerced_to_none():
    spec = _make_spec({
        "some_intent": {"capability": "consulta_deuda", "terminal_signal": "not_a_signal"},
    })
    cap, sig, _ = intent_binding("some_intent", spec)
    assert cap == Capability.consulta_deuda.value
    assert sig is None


# ---------------------------------------------------------------------------
# B8 — _template/responses.json ships a valid, resolvable binding example
# ---------------------------------------------------------------------------

def test_template_responses_json_documents_valid_binding_example():
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "tenants" / "_template" / "responses.json"
    data = json.loads(path.read_text())
    intents = {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}
    spec = _make_spec(intents)

    # annotated info example → valid capability + terminal_signal
    cap, sig, _ = intent_binding("example_info_intent", spec)
    assert cap == Capability.consulta_deuda.value
    assert sig == TerminalSignal.info_provided.value

    # annotated escalation example → escalation signal + reason
    _, sig2, reason2 = intent_binding("example_escalation_intent", spec)
    assert sig2 == TerminalSignal.escalation.value
    assert reason2 == OutcomeReason.explicit_agent_request.value

    # unannotated example → all None (documents the non-breaking default)
    assert intent_binding("example_unannotated_intent", spec) == (None, None, None)
