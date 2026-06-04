"""Debtor — cobranza projection over Record, by composition.

COBRANZA_SPEC parametrizes the Record capture machine with the cobranza field
sets and level vocabulary. Debtor holds a Record instance and adds any
debt-domain fields on top. It does NOT inherit from Record.

Dependency direction: features/cobranza → features/conversation (Record) and
features/cobranza → shared/ports (CaptureSpec) are both allowed.
"""

from __future__ import annotations

from features.conversation.record import Record, TransitionCallback
from shared.ports.capture_spec import CaptureSpec

# ---------------------------------------------------------------------------
# Cobranza field sets — canonical definition lives here in the cobranza
# bounded context. debtor_state.py (shim) re-imports them from here.
# ---------------------------------------------------------------------------

CONTACT_FIELDS: frozenset[str] = frozenset({"name", "phone", "email"})
INTEREST_FIELDS: frozenset[str] = frozenset(
    {"debt_amount", "days_overdue", "account_id", "payment_intent", "dispute_reason"}
)
ENRICHMENT_FIELDS: frozenset[str] = frozenset(
    {"income", "document_number", "document_type", "employer"}
)

# ---------------------------------------------------------------------------
# COBRANZA_SPEC — the single source of truth for cobranza capture behaviour.
# Reproduces DebtorState behaviour exactly: same fields, same thresholds,
# same level strings.
# ---------------------------------------------------------------------------

COBRANZA_SPEC = CaptureSpec(
    contact_fields=CONTACT_FIELDS,
    interest_fields=INTEREST_FIELDS,
    enrichment_fields=ENRICHMENT_FIELDS,
    interest_threshold=2,
    enrichment_threshold=2,
    level_visitor="VISITOR",
    level_pre_contact="PRE_DEBTOR",
    level_contact="DEBTOR",
    level_contact_enriched="DEBTOR_VERIFIED",
)


class Debtor:
    """Cobranza interlocutor: identity + contact + capture level + debt fields.

    Holds a Record instance for progressive capture and adds debt-specific
    fields (debt_amount, days_overdue, commitment_date, etc.) on top.
    Composition, NOT inheritance — Record remains a neutral entity.
    """

    def __init__(
        self,
        initial_data: dict | None = None,
        on_transition: TransitionCallback | None = None,
    ) -> None:
        self.record = Record(
            spec=COBRANZA_SPEC,
            initial_data=initial_data,
            on_transition=on_transition,
        )
        # Debt-domain fields live here, not in Record.
        # (Extended in later slices as needed by cobranza tools.)

    # ------------------------------------------------------------------
    # Delegate capture-machine API to the underlying Record
    # ------------------------------------------------------------------

    @property
    def level(self) -> str:
        return self.record.level

    @property
    def collected(self) -> dict:
        return self.record.collected

    def update(self, data: dict) -> None:
        self.record.update(data)

    def get_status(self) -> dict:
        return self.record.get_status()

    def to_dict(self) -> dict:
        return self.record.to_dict()

    @classmethod
    def from_dict(cls, data: dict) -> "Debtor":
        debtor = cls.__new__(cls)
        debtor.record = Record.from_dict(spec=COBRANZA_SPEC, data=data)
        return debtor
