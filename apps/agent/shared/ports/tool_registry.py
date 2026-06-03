"""Port (interface) for the tool registry.

This module is PURE — it imports nothing from features/ or api/.  Any module
that needs to depend on a tool registry (e.g. features/conversation/agent.py)
depends on this port, not on the concrete implementation in api/tool_registry.py.

The concrete ToolRegistry lives in api/tool_registry.py (api-layer module that
imports from features — that direction is allowed).  The Port + NullToolRegistry
pair allow the agent to run safely with no registry injected (e.g. unit tests
that do not exercise tool calls).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ToolRegistryPort(Protocol):
    """Minimal interface the agent loop depends on."""

    def has_tool(self, name: str) -> bool:
        """Return True if the registry knows a tool with this name."""
        ...

    async def execute(self, name: str, args: dict) -> dict:
        """Execute the named tool with the given arguments."""
        ...


class NullToolRegistry:
    """No-op tool registry — safe default when nothing is injected.

    has_tool always returns False so the agent never dispatches a tool call.
    execute returns a neutral error dict (the agent treats unknown tools as a
    soft error, so no exception is raised).
    """

    def has_tool(self, name: str) -> bool:
        return False

    async def execute(self, name: str, args: dict) -> dict:
        return {"error": "no tool registry configured"}
