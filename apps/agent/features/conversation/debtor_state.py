"""Lead state machine with progressive qualification and extraction opportunities."""

from typing import Callable

CONTACT_FIELDS = {"name", "phone", "email"}
# Cobranza interest signals — what the debtor's situation tells us.
INTEREST_FIELDS = {"debt_amount", "days_overdue", "account_id", "payment_intent", "dispute_reason"}
ENRICHMENT_FIELDS = {"income", "document_number", "document_type", "employer"}

# TODO Fase 1/2: refine cobranza excuses; identity must gate debt disclosure.
EXTRACTION_EXCUSES = {
    "email": "Le envio el comprobante y el detalle a su correo",
    "phone": "Le confirmo el plan por WhatsApp, a que numero?",
    "document_number": "Para validar su cuenta necesito su numero de documento",
    "name": "Con quien tengo el gusto?",
    "account_id": "Cual es el numero de cuenta o referencia de su aviso?",
    "payment_intent": "Que fecha le acomoda para comprometer el pago?",
}

# Type alias for transition callbacks: (previous_level, new_level, collected_data)
TransitionCallback = Callable[[str, str, dict], None]


class LeadMachine:
    """Progressive lead qualification state machine."""

    def __init__(
        self,
        initial_data: dict | None = None,
        on_transition: TransitionCallback | None = None,
    ):
        self.collected: dict = dict(initial_data) if initial_data else {}
        self._on_transition = on_transition

    @property
    def level(self) -> str:
        has_contact = CONTACT_FIELDS.issubset(self.collected.keys())
        has_interest = len(INTEREST_FIELDS & self.collected.keys()) >= 2
        has_enrichment = len(ENRICHMENT_FIELDS & self.collected.keys()) >= 2

        if has_contact and has_enrichment:
            return "LEAD_ENRICHED"
        if has_contact:
            return "LEAD"
        if has_interest:
            return "PRE_LEAD"
        return "VISITOR"

    def update(self, data: dict) -> None:
        previous_level = self.level
        for key, value in data.items():
            if value is not None:
                self.collected[key] = value
        new_level = self.level
        if new_level != previous_level and self._on_transition:
            self._on_transition(previous_level, new_level, dict(self.collected))

    def get_status(self) -> dict:
        all_fields = CONTACT_FIELDS | INTEREST_FIELDS | ENRICHMENT_FIELDS
        missing = [f for f in all_fields if f not in self.collected]

        return {
            "level": self.level,
            "collected": dict(self.collected),
            "missing": missing,
        }

    def to_dict(self) -> dict:
        """Serialize state for persistence."""
        return {"collected": dict(self.collected), "level": self.level}

    @classmethod
    def from_dict(cls, data: dict) -> "LeadMachine":
        """Restore from persisted dict."""
        return cls(initial_data=data.get("collected"))
