"""OpenAI provider — chat.completions with function calling.

Translation from the neutral shape:
  - system prompt → first message {role:"system", content}
  - tools → {type:"function", function:{name, description, parameters}}
  - assistant tool-call turn → {role:"assistant", tool_calls:[{id, type:"function",
        function:{name, arguments=<json str>}}]}
  - tool result → {role:"tool", tool_call_id, content}
  - response.message.tool_calls → ToolCall (arguments is a JSON STRING → parse to dict)

No prompt caching here (OpenAI caches automatically server-side for long prompts;
nothing to wire). Keeps the agent loop identical to Anthropic via LLMResponse.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger
from openai import AsyncOpenAI, OpenAIError

from core.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall


class OpenAIProvider(LLMProvider):
    """LLMProvider backed by OpenAI's Chat Completions API."""

    name = "openai"

    def __init__(self, api_key: str, model: str):
        # AsyncOpenAI raises at construction if api_key is empty. Use a
        # placeholder so the provider builds without a key; a real call then
        # fails as a recoverable LLMError (caught by the API → fallback),
        # matching Anthropic's lazy-credential behavior.
        self._client = AsyncOpenAI(api_key=api_key or "missing-openai-key")
        self.model = model

    # ── Translation helpers ──────────────────────────────────────────────

    @staticmethod
    def _tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Neutral {name, description, parameters} → OpenAI function tools."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                },
            }
            for t in tools
        ]

    @staticmethod
    def _messages_to_openai(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Neutral messages → OpenAI messages, with system prepended."""
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            role = m.get("role")
            if role == "tool":
                out.append({
                    "role": "tool",
                    "tool_call_id": m["tool_call_id"],
                    "content": m.get("content", ""),
                })
            elif role == "assistant" and m.get("tool_calls"):
                out.append({
                    "role": "assistant",
                    "content": m.get("content") or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.input, ensure_ascii=False),
                            },
                        }
                        for tc in m["tool_calls"]
                    ],
                })
            else:
                out.append({"role": role, "content": m.get("content", "")})
        return out

    @staticmethod
    def _parse(response: Any) -> LLMResponse:
        """OpenAI choice.message → neutral LLMResponse.

        `tool_calls[].function.arguments` is a JSON STRING; parse to dict.
        """
        message = response.choices[0].message
        text = message.content or ""
        tool_calls: list[ToolCall] = []
        for tc in (message.tool_calls or []):
            raw_args = tc.function.arguments or "{}"
            try:
                args = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError):
                logger.warning("OpenAI tool args not valid JSON: {!r}", raw_args)
                args = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=args))
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
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "messages": self._messages_to_openai(system, messages),
            "tools": self._tools_to_openai(tools),
        }
        if force_tool:
            kwargs["tool_choice"] = {"type": "function", "function": {"name": force_tool}}

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except OpenAIError as exc:
            logger.opt(exception=True).warning("OpenAI call failed")
            raise LLMError(str(exc)) from exc

        return self._parse(response)
