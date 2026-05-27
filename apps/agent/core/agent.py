"""Sorelia agent core — provider-agnostic LLM loop with function calling.

The agent knows ONLY the neutral LLM interface (core/llm). It builds the system
prompt + neutral message list + neutral tool defs, calls `provider.complete(...)`,
and works with the normalized `LLMResponse` (`.text`, `.tool_calls`). The same
loop runs unchanged on Anthropic (with prompt caching) or OpenAI.
"""

import json
import time
from typing import Any

from loguru import logger
from prompts.system import build_system_prompt
from config.tools_schema import TOOL_DEFINITIONS
from core.llm import LLMProvider, ToolCall
from core.llm.base import usage_from_raw
from tools import ToolRegistry
from core.response_builder import build_ui_actions
from core.prospect_profile import build_prospect_profile, truncate_history


class SoreliaAgent:
    """Core agent: context assembly → LLM with tools → tool execution → response."""

    def __init__(self, provider: LLMProvider, tool_registry: ToolRegistry | None = None, tenant=None):
        self.provider = provider
        self.tool_registry = tool_registry or ToolRegistry()
        self.tenant = tenant
        self.prompt_builder = _PromptBuilder(tenant=tenant)

    async def process_message(
        self,
        text: str,
        conversation_id: str,
        history: list[dict],
        lead_state: dict,
        page_context: dict,
        channel: str = "web",
    ) -> dict[str, Any]:
        """Process a user message through the agent loop."""
        system_prompt = self.prompt_builder.build(lead_state, page_context, history=history, channel=channel)

        # Build prospect profile for context compression
        profile = build_prospect_profile(lead_state, page_context, history)

        # Truncate history: profile replaces old messages, keep only recent ones
        recent = truncate_history(history)

        # Neutral message list ({"role", "content"} text turns).
        messages: list[dict[str, Any]] = []
        if len(history) > len(recent) and profile:
            messages.append({"role": "user", "content": f"[Contexto de la conversacion anterior]\n{profile}"})
            messages.append({"role": "assistant", "content": "Entendido, continuo la conversacion con ese contexto."})
        messages.extend(recent)
        messages.append({"role": "user", "content": text})

        token_savings = len(history) - len(recent)
        logger.info(f"Agent call | conv={conversation_id[:12]} | msg='{text[:50]}' | lead={lead_state.get('level', '?')} | history={len(history)} truncated={token_savings}")

        # Per-turn usage accumulator (summed across every LLM call this turn) and
        # LLM wall-clock latency, surfaced in the result for the analytics sink.
        usage_acc = {"input_tokens": 0, "output_tokens": 0}
        latency_acc = {"ms": 0.0}

        async def _complete(**kwargs):
            _t0 = time.perf_counter()
            _resp = await self.provider.complete(**kwargs)
            latency_acc["ms"] += (time.perf_counter() - _t0) * 1000.0
            _in, _out = usage_from_raw(_resp.raw)
            usage_acc["input_tokens"] += _in
            usage_acc["output_tokens"] += _out
            return _resp

        # Filter tools per tenant config (agent.excluded_tools in tenant.config.json)
        _excluded_tools = set()
        _tenant = getattr(self, "tenant", None)
        if _tenant and hasattr(_tenant, "excluded_tools"):
            _excluded_tools = set(_tenant.excluded_tools or [])
        tools = [t for t in TOOL_DEFINITIONS if t["name"] not in _excluded_tools] if _excluded_tools else TOOL_DEFINITIONS

        # First LLM call with tools (neutral request → neutral response)
        response = await _complete(
            system=system_prompt, messages=messages, tools=tools, max_tokens=1024,
        )

        ui_actions: dict = {}
        tool_pairs: list[tuple[str, dict]] = []
        suggested_replies = None

        # Separate suggest_quick_replies from data tools
        data_tools = [tc for tc in response.tool_calls if tc.name != "suggest_quick_replies"]
        chip_tools = [tc for tc in response.tool_calls if tc.name == "suggest_quick_replies"]

        if data_tools:
            logger.info(f"Tool calls: {[tc.name for tc in data_tools]}")
            tool_results = await self._execute_tools(data_tools)

            tool_pairs = [(tc.name, result) for tc, result in zip(data_tools, tool_results)]
            ui_actions = build_ui_actions(tool_pairs)

            # Re-add the assistant tool-call turn + tool results (neutral shape),
            # then call the model again for the final answer.
            all_calls = data_tools + chip_tools
            messages.append({
                "role": "assistant",
                "content": response.text,
                "tool_calls": all_calls,
            })
            for tc, result in zip(data_tools, tool_results):
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
            for tc in chip_tools:
                chip_result = await self.tool_registry.execute(tc.name, tc.input)
                suggested_replies = chip_result.get("options", [])
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(chip_result, ensure_ascii=False),
                })

            final = await _complete(
                system=system_prompt, messages=messages, tools=TOOL_DEFINITIONS, max_tokens=1024,
            )
            content = final.text
            for tc in final.tool_calls:
                if tc.name == "suggest_quick_replies":
                    chip_result = await self.tool_registry.execute(tc.name, tc.input)
                    suggested_replies = chip_result.get("options", [])
        else:
            content = response.text
            for tc in chip_tools:
                chip_result = await self.tool_registry.execute(tc.name, tc.input)
                suggested_replies = chip_result.get("options", [])

        # Force chip generation if the model didn't call suggest_quick_replies
        if not suggested_replies and content:
            suggested_replies = await self._force_chip_generation(
                content, lead_state, tool_pairs, _complete=_complete,
            )

        return {
            "content": content or "",
            "conversation_id": conversation_id,
            "response_id": f"sorelia_{id(response)}",
            "metadata": {},
            "ui_actions": ui_actions,
            "tool_pairs": tool_pairs,
            "suggested_replies": suggested_replies,
            # Per-turn telemetry for the analytics sink (summed across LLM calls).
            "usage": {
                "input_tokens": usage_acc["input_tokens"],
                "output_tokens": usage_acc["output_tokens"],
                "model": self.provider.model,
                "provider": getattr(self.provider, "name", ""),
            },
            "latency_ms": int(latency_acc["ms"]),
            "tools_called": [name for name, _ in tool_pairs],
        }

    async def _force_chip_generation(
        self,
        content: str,
        lead_state: dict,
        tool_pairs: list[tuple[str, dict]],
        _complete=None,
    ) -> list[str] | None:
        """Lightweight forced call to produce quick replies when the main flow didn't.

        ``_complete`` (when passed) is the per-turn wrapper that accumulates token
        usage + latency, so this auxiliary call is counted in analytics too. Falls
        back to ``self.provider.complete`` for callers that don't pass it.
        """
        _call = _complete or self.provider.complete
        chip_tool = next((t for t in TOOL_DEFINITIONS if t["name"] == "suggest_quick_replies"), None)
        if not chip_tool:
            return None

        tools_called = [name for name, _ in tool_pairs] if tool_pairs else ["ninguna"]
        collected = lead_state.get("collected", {})
        system = (
            "Genera opciones de respuesta rapida para el usuario. "
            "Deben ser 2-4 opciones cortas (2-5 palabras), coherentes con lo que acabas de decir. "
            "Usa datos reales: acciones concretas de cobranza."
        )
        messages = [{"role": "user", "content": (
            f"Tu respuesta al cliente: {content[:300]}\n"
            f"Tools usadas: {', '.join(tools_called)}\n"
            f"Datos del lead: {json.dumps(collected, ensure_ascii=False)}"
        )}]
        try:
            chip_response = await _call(
                system=system, messages=messages, tools=[chip_tool],
                max_tokens=256, force_tool="suggest_quick_replies",
            )
            for tc in chip_response.tool_calls:
                if tc.name == "suggest_quick_replies":
                    result = await self.tool_registry.execute(tc.name, tc.input)
                    options = result.get("options", [])
                    if options:
                        logger.debug(f"Forced chip generation: {options}")
                        return options
        except Exception:
            logger.opt(exception=True).debug("Forced chip generation failed (non-blocking)")
        return None

    async def _execute_tools(self, tool_calls: list[ToolCall]) -> list[dict]:
        results = []
        for tc in tool_calls:
            results.append(await self.tool_registry.execute(tc.name, tc.input))
        return results


class _PromptBuilder:
    def __init__(self, tenant=None):
        self.tenant = tenant

    def build(self, lead_state: dict, page_context: dict, history: list[dict] | None = None, channel: str = "web") -> str:
        return build_system_prompt(lead_state=lead_state, page_context=page_context, history=history, channel=channel, tenant=self.tenant)
