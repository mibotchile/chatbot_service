"""Pure outcome derivation for Layer-3 gestion tracking.

No IO. Fully unit-testable. All string values sourced from gestion_catalog.
Priority order matches spec R1 and design §4.
"""

from __future__ import annotations

from features.analytics.gestion_catalog import INTENT_TO_REASON, Outcome


def derive_outcome(
    *,
    session_state: dict,
    resolved_intent: str | None,
    was_escalated: bool,
    identity_failed: bool = False,
    commitment_registered: bool = False,
    proof_submitted: bool = False,
    fallback_exhausted: bool = False,
    identified: bool = False,
    info_provided: bool = False,
) -> tuple[str, str | None]:
    """Return (outcome, outcome_reason). Priority-ordered; first match wins.

    Priority:
        1. identity_failed       → identification_failed / max_identification_retries
        2. commitment_registered → payment_commitment_registered / None
        3. proof_submitted       → payment_proof_submitted / None
        4. was_escalated         → escalated_to_agent / reason from intent
        5. fallback_exhausted    → not_understood / fallback_exhausted
        6. info_provided         → info_provided / None
        7. identified            → identified / None
        8. default               → unresolved / None
    """
    if identity_failed:
        return (Outcome.identification_failed, "max_identification_retries")

    if commitment_registered:
        return (Outcome.payment_commitment_registered, None)

    if proof_submitted:
        return (Outcome.payment_proof_submitted, None)

    if was_escalated:
        return (Outcome.escalated_to_agent, _reason_for_intent(resolved_intent))

    if fallback_exhausted:
        return (Outcome.not_understood, "fallback_exhausted")

    if info_provided:
        return (Outcome.info_provided, None)

    if identified:
        return (Outcome.identified, None)

    return (Outcome.unresolved, None)


def _reason_for_intent(intent: str | None) -> str | None:
    """Map an escalation-triggering intent to its outcome_reason.

    Returns None for unknown intents or None input.
    Lookup table lives in gestion_catalog.INTENT_TO_REASON.
    """
    if intent is None:
        return None
    return INTENT_TO_REASON.get(intent)
