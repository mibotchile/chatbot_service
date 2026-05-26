"""Neutral LLM provider interface + value objects.

The agent works ONLY with these neutral types. Each provider maps them to its
own SDK. Tool definitions enter as neutral dicts: {name, description, parameters}
where `parameters` is a JSON Schema object.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """A single tool/function call requested by the model (neutral)."""

    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """Normalized model response. `raw` keeps the SDK object for debugging."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None


class LLMError(Exception):
    """Provider-agnostic error raised when an LLM call fails recoverably.

    main.py catches this (instead of anthropic.APIError) so the API layer stays
    decoupled from any specific SDK.
    """


class LLMProvider(ABC):
    """Strategy interface. One concrete subclass per backend."""

    #: Default model for this provider (used when `model` is not passed).
    model: str = ""

    @abstractmethod
    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int = 1024,
        force_tool: str | None = None,
    ) -> LLMResponse:
        """Run one completion with tool-calling support.

        Args:
            system: System prompt as plain text (providers add their own framing
                — e.g. Anthropic wraps it with cache_control; OpenAI prepends a
                role:"system" message).
            messages: Conversation in NEUTRAL shape. Each item is one of:
                {"role": "user"|"assistant", "content": "<text>"}
                {"role": "assistant", "tool_calls": [ToolCall, ...]}  (turn that
                    requested tools — the provider re-serializes it)
                {"role": "tool", "tool_call_id": "<id>", "content": "<json str>"}
            tools: Neutral tool defs [{name, description, parameters}].
            model: Override the provider default.
            max_tokens: Output cap.
            force_tool: If set, force the model to call exactly this tool
                (used for the mandatory suggest_quick_replies pass).

        Returns:
            LLMResponse with normalized `.text` and `.tool_calls`.
        """
        raise NotImplementedError
