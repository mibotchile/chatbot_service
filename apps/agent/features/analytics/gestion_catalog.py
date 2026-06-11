"""Versioned outcome catalog for Layer-3 gestion tracking.

This is the single source of truth for all outcome strings, event types,
and lookup tables used in outcome derivation. No client-specific or
tenant-specific vocabulary here.

SCHEMA_VERSION bumps when the catalog adds new values; consumers that handle
only known v1 values can safely ignore unknown ones.
"""

from __future__ import annotations

from enum import Enum

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

SCHEMA_VERSION: int = 1

# ---------------------------------------------------------------------------
# Outcome enum
# ---------------------------------------------------------------------------


class Outcome(str, Enum):
    """Terminal outcome of a cobranza conversation (8 values, v1)."""

    identified = "identified"
    identification_failed = "identification_failed"
    info_provided = "info_provided"
    payment_proof_submitted = "payment_proof_submitted"
    payment_commitment_registered = "payment_commitment_registered"
    escalated_to_agent = "escalated_to_agent"
    not_understood = "not_understood"
    unresolved = "unresolved"


# ---------------------------------------------------------------------------
# EventType enum
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    """Allowed event_type values for the gestion_events append-only journal."""

    capability_used = "capability_used"
    credit_state_set = "credit_state_set"
    terminal = "terminal"
    escalation = "escalation"
    commitment = "commitment"
    proof = "proof"


# ---------------------------------------------------------------------------
# TERMINAL_SIGNALS
# Data-driven detection table: signal name → tool names / session_state keys
# that indicate that signal fired this turn.
# ---------------------------------------------------------------------------

TERMINAL_SIGNALS: dict[str, list[str]] = {
    # Tool names whose presence in tool_pairs results indicates commitment
    "commitment_registered": [
        "register_payment_commitment",
        "commit_payment",
    ],
    # Tool names whose presence indicates proof submitted
    "proof_submitted": [
        "upload_payment_proof",
        "submit_comprobante",
        "upload_comprobante",
    ],
    # session_state flag keys that indicate identity gate exhausted
    "identity_failed": [
        "identity_gate_exhausted",
        "max_dni_retries_reached",
    ],
    # session_state flag keys that indicate 2nd-strike fallback exhausted
    "fallback_exhausted": [
        "fallback_count_exhausted",
        "second_strike_fallback",
    ],
    # Intents that signal info was delivered and conversation closed
    "info_provided_intents": [
        "consulta_deuda",
        "cronograma",
        "cuotas",
        "cuentas_bancarias",
        "fecha_vencimiento",
        "deuda_total",
    ],
    # Intents that signal identity verified with no further terminal action
    "identified_intents": [
        "identificacion",
        "verify_identity",
    ],
}

# ---------------------------------------------------------------------------
# INTENT_TO_CAPABILITY
# Maps resolved_intent / tool names → CAPABILITIES_USED axis value.
# Used to accumulate capabilities_used in the gestiones snapshot.
# ---------------------------------------------------------------------------

INTENT_TO_CAPABILITY: dict[str, str] = {
    "identificacion": "identificacion",
    "verify_identity": "identificacion",
    "consulta_deuda": "consulta_deuda",
    "deuda_total": "deuda_total",
    "cronograma": "cronograma",
    "cuotas": "cuotas",
    "cuentas_bancarias": "cuentas_bancarias",
    "fecha_vencimiento": "fecha_vencimiento",
    "upload_comprobante": "comprobante",
    "submit_comprobante": "comprobante",
    "upload_payment_proof": "comprobante",
    "comprobante": "comprobante",
    "register_payment_commitment": "compromiso",
    "commit_payment": "compromiso",
    "payment_commitment": "compromiso",
    "pago": "pago",
    "multicredito": "multicredito",
    "horario_feriado": "horario_feriado",
}

# ---------------------------------------------------------------------------
# INTENT_TO_REASON
# Maps escalation-triggering intents → outcome_reason enum value.
# Used by _reason_for_intent() in gestion_derivation.py.
# Unknown intents return None (caller's responsibility).
# ---------------------------------------------------------------------------

INTENT_TO_REASON: dict[str, str] = {
    "cannot_pay": "cannot_pay",
    "requested_alternatives": "requested_alternatives",
    "commitment_beyond_window": "commitment_beyond_window",
    "wants_full_payment": "wants_full_payment",
    "pay_installment": "pay_installment",
    "proof_other_installment": "proof_other_installment",
    "explicit_agent_request": "explicit_agent_request",
    "out_of_hours": "out_of_hours",
    "fallback_exhausted": "fallback_exhausted",
}
