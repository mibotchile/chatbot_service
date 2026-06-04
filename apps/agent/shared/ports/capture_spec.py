"""CaptureSpec — neutral parametrization for the Record capture state machine.

Defines which fields govern each qualification level and the level label strings.
Placed in shared/ports/ so it can be imported by both features/conversation/ and
features/cobranza/ without violating the features→shared dependency direction.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CaptureSpec:
    """Immutable descriptor that parametrizes Record's progressive-capture logic.

    Field sets drive level transitions; level strings are the emitted vocabulary.
    All collections are frozensets so CaptureSpec is hashable and safe to share.

    Attributes:
        contact_fields: Full set of fields that must ALL be present for contact level.
        interest_fields: Fields where ≥ interest_threshold triggers pre-contact level.
        enrichment_fields: Fields where ≥ enrichment_threshold triggers enriched level.
        interest_threshold: Minimum interest fields needed to reach pre-contact level.
        enrichment_threshold: Minimum enrichment fields (alongside contact) for enriched.
        level_visitor: Level string when no qualifying data is present.
        level_pre_contact: Level string when interest threshold met but contact incomplete.
        level_contact: Level string when all contact fields are present.
        level_contact_enriched: Level string when contact + enrichment threshold met.
    """

    contact_fields: frozenset[str]
    interest_fields: frozenset[str]
    enrichment_fields: frozenset[str]

    interest_threshold: int = 2
    enrichment_threshold: int = 2

    level_visitor: str = "VISITOR"
    level_pre_contact: str = "PRE_CONTACT"
    level_contact: str = "CONTACT"
    level_contact_enriched: str = "CONTACT_ENRICHED"
