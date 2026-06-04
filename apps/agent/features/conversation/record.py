"""Record — neutral progressive-capture state machine parametrized by CaptureSpec.

Record is the common interlocutor state across all agent types: it tracks
identity/contact fields and the capture level. Domain-specific projections
(Debtor, Applicant, Lead) wrap Record by composition — they never inherit it.

Logic is verbatim from DebtorState; field sets and level strings come from the
injected CaptureSpec so Record carries no cobranza-specific knowledge.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    pass

from shared.ports.capture_spec import CaptureSpec

# Type alias for transition callbacks: (previous_level, new_level, collected_data)
TransitionCallback = Callable[[str, str, dict], None]


class Record:
    """Progressive-capture state machine parametrized by a CaptureSpec.

    Behaviour is identical to DebtorState when constructed with COBRANZA_SPEC.
    Any other CaptureSpec yields a Record for a different agent type without
    touching cobranza code.
    """

    def __init__(
        self,
        spec: CaptureSpec,
        initial_data: dict | None = None,
        on_transition: TransitionCallback | None = None,
    ) -> None:
        self._spec = spec
        self.collected: dict = dict(initial_data) if initial_data else {}
        self._on_transition = on_transition

    # ------------------------------------------------------------------
    # Level computation — mirrors DebtorState.level with spec-driven sets
    # ------------------------------------------------------------------

    @property
    def level(self) -> str:
        keys = self.collected.keys()
        has_contact = self._spec.contact_fields.issubset(keys)
        has_interest = len(self._spec.interest_fields & keys) >= self._spec.interest_threshold
        has_enrichment = len(self._spec.enrichment_fields & keys) >= self._spec.enrichment_threshold

        if has_contact and has_enrichment:
            return self._spec.level_contact_enriched
        if has_contact:
            return self._spec.level_contact
        if has_interest:
            return self._spec.level_pre_contact
        return self._spec.level_visitor

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def update(self, data: dict) -> None:
        """Merge data into collected; None values are ignored (not overwritten)."""
        previous_level = self.level
        for key, value in data.items():
            if value is not None:
                self.collected[key] = value
        new_level = self.level
        if new_level != previous_level and self._on_transition:
            self._on_transition(previous_level, new_level, dict(self.collected))

    # ------------------------------------------------------------------
    # Status / serialization
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        all_fields = (
            self._spec.contact_fields
            | self._spec.interest_fields
            | self._spec.enrichment_fields
        )
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
    def from_dict(cls, spec: CaptureSpec, data: dict) -> "Record":
        """Restore from persisted dict."""
        return cls(spec=spec, initial_data=data.get("collected"))
