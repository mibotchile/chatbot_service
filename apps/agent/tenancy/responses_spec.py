"""ResponsesSpec — tenant-specific canned-responses configuration.

Extracted from core/responses.py so tenancy/ can declare the spec type without
depending on the full responses engine (breaks cycle: tenant_loader → responses engine).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


@dataclass
class ResponsesSpec:
    """Parsed ``responses.json`` for a tenant. Empty when the tenant has none."""

    intents: dict[str, dict] = field(default_factory=dict)
    response_mode: str = "llm"
    # Tenant identifier — set by from_dir() so engine code can read tenant_id
    # from the spec without a separate lookup.
    _tenant_id: str = field(default="", repr=False)
    # Data-driven SENDABLE info types (envío de info bajo demanda). Keyed by tipo
    # (e.g. estado_cuenta), each with per-channel copy (correo/whatsapp). Lives in
    # responses.json under the reserved ``_deliverables`` key (ignored as an
    # intent). Empty for tenants that don't ship it. See docs/deliverables-format.md.
    deliverables: dict[str, dict] = field(default_factory=dict)
    # Data-driven quick-reply CHIPS by conversation state. Keyed by state name
    # (e.g. ``cold`` = unidentified, ``identified`` = verified). Lives in
    # responses.json under the reserved ``_chips`` key. Per-intent chips live on
    # each intent under ``chips``. Empty → no tenant chips (LLM/heuristic chips,
    # backward compatible). See docs/responses-format.md.
    chips: dict[str, list[str]] = field(default_factory=dict)

    @property
    def has_chips(self) -> bool:
        """True when the tenant declares chips (per-state or per-intent).

        When True, the BACKEND owns the quick-replies (data-driven, zero LLM
        hallucination) and any LLM-suggested chips are ignored. When False, the
        tenant keeps the legacy LLM/heuristic chip behavior (no break)."""
        if self.chips:
            return True
        return any((cfg or {}).get("chips") for cfg in self.intents.values())

    @property
    def enabled(self) -> bool:
        """True when canned responses are active (any mode but plain ``llm``)."""
        return self.response_mode in ("scripted", "hybrid") and bool(self.intents)

    def has_intent(self, intent: str) -> bool:
        return intent in self.intents

    @property
    def vencido_only_intents(self) -> frozenset[str]:
        """Set of intent keys that carry ``"vencido_only": true`` in responses.json.

        Used by ``handle_vencido_only_intent`` to guard intents that only make
        sense for overdue borrowers. Derived from the spec so no tenant intent
        names are hardcoded in the engine.
        """
        return frozenset(
            key
            for key, cfg in self.intents.items()
            if (cfg or {}).get("vencido_only") is True
        )

    @classmethod
    def from_dir(cls, tenant_dir: str | Path, response_mode: str = "llm") -> ResponsesSpec:
        """Load ``responses.json`` from a tenant directory. Missing → empty spec.

        A missing file is the normal "tenant uses pure LLM" case — never an
        error. A malformed file logs a warning and degrades to empty (LLM).

        Sets ``_tenant_id`` from the directory name so engine code can reference
        the tenant without a separate lookup.
        """
        tenant_dir = Path(tenant_dir)
        tenant_id = tenant_dir.name  # e.g. "prestamype" from ".../tenants/prestamype"
        path = tenant_dir / "responses.json"
        if not path.exists():
            return cls(intents={}, response_mode=response_mode or "llm", _tenant_id=tenant_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("responses.json malformed for {}; falling back to llm", tenant_dir)
            return cls(intents={}, response_mode=response_mode or "llm", _tenant_id=tenant_id)
        # Allow an in-file ``response_mode`` override; the tenant.config flag wins
        # when provided (passed in), else the file's own, else llm.
        file_mode = data.pop("_response_mode", None)
        deliverables = data.get("_deliverables") or {}
        chips = data.get("_chips") or {}
        intents = {k: v for k, v in data.items() if not k.startswith("_")}
        return cls(
            intents=intents,
            response_mode=(response_mode or file_mode or "llm"),
            deliverables=deliverables if isinstance(deliverables, dict) else {},
            chips=chips if isinstance(chips, dict) else {},
            _tenant_id=tenant_id,
        )
