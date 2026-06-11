"""Pure outcome derivation for Layer-3 gestion tracking.

No IO. Fully unit-testable. All string values sourced from gestion_catalog.
Signal-driven: derive_outcome receives the pre-resolved terminal_signal from
intent_binding() and maps it to the correct Outcome. Priority order matches
spec B5 and design §4.
"""

from __future__ import annotations

from features.analytics.gestion_catalog import Outcome, OutcomeReason


def derive_outcome(
    *,
    session_state: dict,
    resolved_intent: str | None,
    terminal_signal: str | None,
    was_escalated: bool,
    identity_failed: bool = False,
    escalation_reason: str | None = None,
) -> tuple[str, str | None]:
    """Return (outcome, outcome_reason). Priority-ordered; first match wins.

    Priority:
        1. identity_failed (flag or identity_failed signal) → identification_failed
        2. terminal_signal == commitment      → payment_commitment_registered
        3. terminal_signal == proof           → payment_proof_submitted
        4. was_escalated or signal==escalation → escalated_to_agent / escalation_reason
        5. terminal_signal == fallback        → not_understood / fallback_exhausted
        6. terminal_signal == info_provided   → info_provided
        7. default                            → unresolved
    """
    # Priority 1: identity gate (session flag or explicit signal)
    if identity_failed or terminal_signal == "identity_failed":
        return (Outcome.identification_failed, OutcomeReason.max_identification_retries.value)

    # Priority 2: commitment
    if terminal_signal == "commitment":
        return (Outcome.payment_commitment_registered, None)

    # Priority 3: proof
    if terminal_signal == "proof":
        return (Outcome.payment_proof_submitted, None)

    # Priority 4: escalation (flag or signal)
    if was_escalated or terminal_signal == "escalation":
        return (Outcome.escalated_to_agent, escalation_reason)

    # Priority 5: fallback
    if terminal_signal == "fallback":
        return (Outcome.not_understood, OutcomeReason.fallback_exhausted.value)

    # Priority 6: info delivered
    if terminal_signal == "info_provided":
        return (Outcome.info_provided, None)

    return (Outcome.unresolved, None)
