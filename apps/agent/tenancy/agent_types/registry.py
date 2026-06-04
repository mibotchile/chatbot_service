"""InCodeAgentTypeRegistry — in-code implementation of AgentTypeRegistry.

Placement: tenancy/agent_types/ (tenancy layer — may import shared/ports).
Wiring of domain entries (e.g. COBRANZA_AGENT_TYPE) is done at the composition
root (api/main.py). This module MUST NOT import from features/ directly.

Swappability: consumers depend on shared/ports/agent_type_registry.AgentTypeRegistry
(Protocol). Swap InCodeAgentTypeRegistry for a DB-backed impl at api/ composition
root with zero consumer change.
"""

from __future__ import annotations

from shared.ports.agent_type_registry import (
    AgentTypeNotFoundError,
    AgentTypeRegistry,  # noqa: F401 (re-export for convenience)
    AgentTypeSpec,
)


class InCodeAgentTypeRegistry:
    """In-memory registry backed by a plain dict.

    Satisfies the AgentTypeRegistry Protocol.
    Entries are provided at construction time — no features/ import here.
    """

    def __init__(self, entries: dict[str, AgentTypeSpec]) -> None:
        self._entries = dict(entries)

    def get(self, agent_type: str) -> AgentTypeSpec:
        """Return the AgentTypeSpec for the given agent_type.

        Raises:
            AgentTypeNotFoundError: when agent_type is not registered.
        """
        try:
            return self._entries[agent_type]
        except KeyError:
            raise AgentTypeNotFoundError(agent_type)


def default_registry() -> InCodeAgentTypeRegistry:
    """Build the default registry with all registered agent types.

    This is the ONLY place that imports from features/cobranza/agent_type,
    keeping the composition-root pattern: tenancy layer wires domain entries
    via this factory, called from api/main.py lifespan.
    """
    from features.cobranza.agent_type import COBRANZA_AGENT_TYPE

    return InCodeAgentTypeRegistry({"cobranza": COBRANZA_AGENT_TYPE})
