"""DebtorState — transient compatibility shim over Record.

All production callers still import DebtorState, CONTACT_FIELDS, INTEREST_FIELDS,
ENRICHMENT_FIELDS and EXTRACTION_EXCUSES from this module. The shim re-exports
all of them so zero callers need touching until the shim is deleted in S8.

DebtorState subclasses Record with COBRANZA_SPEC injected. Behaviour is 100%
identical to the original — characterization tests in test_record_char.py and
test_debtor_state_level.py enforce this at every slice.

BOUNDED LIFETIME: this shim is deleted in Slice 8. The transient
features/conversation → features/cobranza import is the ONE allowed exception
to the dependency rule during the migration window.
"""

from __future__ import annotations

from features.cobranza.debtor import (
    COBRANZA_SPEC,
    CONTACT_FIELDS,
    ENRICHMENT_FIELDS,
    INTEREST_FIELDS,
)
from features.conversation.record import Record, TransitionCallback

# ---------------------------------------------------------------------------
# Re-export field sets so all existing import sites keep working.
# CONTACT_FIELDS, INTEREST_FIELDS, ENRICHMENT_FIELDS are imported above from
# features.cobranza.debtor and are available as module-level names for any
# `from features.conversation.debtor_state import X` caller.
# ---------------------------------------------------------------------------

# Cobranza extraction excuses — unchanged, still belongs here during migration.
EXTRACTION_EXCUSES = {
    "email": "Le envio el comprobante y el detalle a su correo",
    "phone": "Le confirmo el plan por WhatsApp, a que numero?",
    "document_number": "Para validar su cuenta necesito su numero de documento",
    "name": "Con quien tengo el gusto?",
    "account_id": "Cual es el numero de cuenta o referencia de su aviso?",
    "payment_intent": "Que fecha le acomoda para comprometer el pago?",
}


class DebtorState(Record):
    """Compat shim: DebtorState with COBRANZA_SPEC pre-injected.

    Subclasses Record so isinstance(DebtorState(), Record) is True and all
    type-checks pass. The only difference from the original is that the
    captured field sets and level strings come from COBRANZA_SPEC via Record
    instead of being defined in this module directly.

    Deleted in Slice 8 once all callers are migrated to Record(COBRANZA_SPEC).
    """

    def __init__(
        self,
        initial_data: dict | None = None,
        on_transition: TransitionCallback | None = None,
    ) -> None:
        super().__init__(
            spec=COBRANZA_SPEC,
            initial_data=initial_data,
            on_transition=on_transition,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "DebtorState":  # type: ignore[override]
        """Restore from persisted dict — keeps the original class signature."""
        instance = cls.__new__(cls)
        # Bypass __init__; initialise the Record base directly
        Record.__init__(instance, spec=COBRANZA_SPEC, initial_data=data.get("collected"))
        return instance
