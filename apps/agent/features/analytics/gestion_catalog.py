"""Versioned outcome catalog for Layer-3 gestion tracking.

This is the single source of truth for all outcome strings, event types,
and vocabulary enums used in outcome derivation. No tenant-specific intent
names, tool names, or hardcoded mappings here — those live in each tenant's
responses.json and are resolved at runtime via intent_binding().

SCHEMA_VERSION bumps when the catalog adds new values; consumers that handle
only known v1 values can safely ignore unknown ones.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tenancy.responses_spec import ResponsesSpec

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
# Capability enum
# Tenant-agnostic vocabulary of observable capabilities.
# Binding to a real intent lives in tenants/{tenant}/responses.json.
# ---------------------------------------------------------------------------


class Capability(str, Enum):
    """Observable capabilities a conversation can exercise (vocabulary only)."""

    identificacion = "identificacion"
    consulta_deuda = "consulta_deuda"
    cuentas_bancarias = "cuentas_bancarias"
    estado_cuenta = "estado_cuenta"
    constancia = "constancia"
    politica_pago = "politica_pago"
    comprobante = "comprobante"
    multicredito = "multicredito"
    # Reserved for flujos change:
    cronograma = "cronograma"
    cuotas = "cuotas"
    fecha_vencimiento = "fecha_vencimiento"
    compromiso = "compromiso"
    pago = "pago"
    deuda_total = "deuda_total"
    horario_feriado = "horario_feriado"


# ---------------------------------------------------------------------------
# TerminalSignal enum
# Each value names a terminal-turn signal a resolved intent can carry.
# ---------------------------------------------------------------------------


class TerminalSignal(str, Enum):
    """Signals that a resolved intent can carry to indicate terminal state."""

    info_provided = "info_provided"
    proof = "proof"
    commitment = "commitment"
    escalation = "escalation"
    fallback = "fallback"
    identity_failed = "identity_failed"


# ---------------------------------------------------------------------------
# OutcomeReason enum
# Supplemental reason attached to some outcomes (escalation, fallback, etc.).
# ---------------------------------------------------------------------------


class OutcomeReason(str, Enum):
    """Supplemental reason values for escalated_to_agent / not_understood."""

    explicit_agent_request = "explicit_agent_request"
    fallback_exhausted = "fallback_exhausted"
    cannot_pay = "cannot_pay"
    requested_alternatives = "requested_alternatives"
    commitment_beyond_window = "commitment_beyond_window"
    wants_full_payment = "wants_full_payment"
    pay_installment = "pay_installment"
    proof_other_installment = "proof_other_installment"
    out_of_hours = "out_of_hours"
    max_identification_retries = "max_identification_retries"


# ---------------------------------------------------------------------------
# intent_binding — single DRY accessor
# ---------------------------------------------------------------------------


def intent_binding(
    intent_name: str | None,
    responses_cfg: "ResponsesSpec | None",
) -> tuple[str | None, str | None, str | None]:
    """Return (capability, terminal_signal, escalation_reason) for a resolved intent.

    Each field is coerced against its catalog enum; any value not in the enum
    (or absent) is returned as None.  Never raises — the hook is fire-and-forget.

    Args:
        intent_name: The resolved intent string from the conversation turn.
        responses_cfg: A ResponsesSpec loaded for the tenant. None → all-None.

    Returns:
        A 3-tuple (capability, terminal_signal, escalation_reason), each a
        string enum value or None.
    """
    if not intent_name or responses_cfg is None:
        return (None, None, None)

    cfg: dict = responses_cfg.intents.get(intent_name) or {}

    def _ok(val: object, enum: type) -> str | None:
        """Coerce val to an enum member value, or None if invalid/absent."""
        try:
            return enum(val).value if val is not None else None
        except ValueError:
            return None

    return (
        _ok(cfg.get("capability"), Capability),
        _ok(cfg.get("terminal_signal"), TerminalSignal),
        _ok(cfg.get("escalation_reason"), OutcomeReason),
    )
