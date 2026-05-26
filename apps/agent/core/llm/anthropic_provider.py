"""Anthropic provider — extracted from the old agent.py.

Keeps native prompt caching: the system prompt is sent as a single text block
with `cache_control: ephemeral` so it's cached across turns. Tools are sent with
Anthropic's `input_schema`. Tool-call requests are parsed from `tool_use` blocks.
"""

from __future__ import annotations

from typing import Any

import anthropic
from loguru import logger

from core.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall


class AnthropicProvider(LLMProvider):
    """LLMProvider backed by Anthropic's Messages API (with prompt caching)."""

    def __init__(self, api_key: str, model: str):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

    # ── Translation helpers ──────────────────────────────────────────────

    @staticmethod
    def _tools_to_anthropic(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Neutral {name, description, parameters} → Anthropic {..., input_schema}."""
        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
            }
            for t in tools
        ]

    @staticmethod
    def _messages_to_anthropic(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Neutral messages → Anthropic content blocks.

        - assistant turn with tool_calls → assistant message of tool_use blocks
          (text block first if any).
        - tool result → user message with a tool_result block.
        - plain text → role + string content.
        """
        out: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role")
            if role == "tool":
                out.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m["tool_call_id"],
                        "content": m.get("content", ""),
                    }],
                })
            elif role == "assistant" and m.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m["tool_calls"]:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.input,
                    })
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append({"role": role, "content": m.get("content", "")})
        return out

    @staticmethod
    def _parse(response: Any) -> LLMResponse:
        """Anthropic content blocks → neutral LLMResponse."""
        text = next((b.text for b in response.content if b.type == "text"), "")
        tool_calls = [
            ToolCall(id=b.id, name=b.name, input=b.input or {})
            for b in response.content
            if b.type == "tool_use"
        ]
        return LLMResponse(text=text, tool_calls=tool_calls, raw=response)

    # ── Interface ────────────────────────────────────────────────────────

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
        # Prompt caching: cache the system prompt across turns.
        system_blocks = [{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }]

        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "system": system_blocks,
            "messages": self._messages_to_anthropic(messages),
            "tools": self._tools_to_anthropic(tools),
        }
        if force_tool:
            kwargs["tool_choice"] = {"type": "tool", "name": force_tool}

        try:
            response = await self._client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            logger.opt(exception=True).warning("Anthropic call failed")
            raise LLMError(str(exc)) from exc

        return self._parse(response)
