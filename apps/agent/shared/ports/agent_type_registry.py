"""AgentTypeRegistry — swappable port (Protocol) for agent-type resolution.

Placement: shared/ports/ (pure — no features/ or tenancy/ imports).
Consumers depend on this port; the concrete impl lives in tenancy/agent_types/.

Swappability contract:
  - Any object implementing AgentTypeRegistry.get(agent_type) is a valid source.
  - Swap the impl at the composition root (api/) without touching any consumer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from shared.ports.capture_spec import CaptureSpec


class AgentTypeNotFoundError(KeyError):
    """Raised when an agent_type is not registered in the registry.

    Subclasses KeyError for isinstance compatibility, but carries a descriptive
    message so callers can diagnose missing configuration immediately.
    """

    def __init__(self, agent_type: str) -> None:
        self.agent_type = agent_type
        super().__init__(
            f"Agent type '{agent_type}' is not registered. "
            "Register it in the AgentTypeRegistry at the composition root."
        )

    def __str__(self) -> str:
        return self.args[0]


@dataclass(frozen=True)
class AgentTypeSpec:
    """Descriptor for a registered agent type.

    Fields:
        capture_spec: The CaptureSpec parametrizing the Record capture machine.
        tools: Ordered tuple of tool names available for this agent type.
        skills: Optional list of skill names (None = no overrides).
        gate_model: Identifier for the gate behaviour (e.g. 'hard_dni').
        projection_table: Per-type DB table name (None = no projection table).
    """

    capture_spec: CaptureSpec
    tools: tuple[str, ...]
    skills: list[str] | None
    gate_model: str
    projection_table: str | None


@runtime_checkable
class AgentTypeRegistry(Protocol):
    """Protocol for agent-type registries.

    Implementations:
        - InCodeAgentTypeRegistry (tenancy/agent_types/registry.py) — current
        - Future: DbAgentTypeRegistry — swap at api/ composition root, zero
          consumer change required.
    """

    def get(self, agent_type: str) -> AgentTypeSpec:
        """Return the AgentTypeSpec for the given agent_type.

        Raises:
            AgentTypeNotFoundError: when agent_type is not registered.
        """
        ...
