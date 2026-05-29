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
from core import responses as responses_engine


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
        session_state: dict | None = None,
    ) -> dict[str, Any]:
        """Process a user message through the agent loop.

        Curated-responses feature (tenant-agnostic): when the active tenant ships
        a ``responses.json`` and a non-``llm`` ``response_mode``, a 2-layer router
        runs FIRST — Layer 1 (keyword, zero LLM) and, in hybrid, Layer 2 (cheap
        LLM intent classification → canned). Only when no canned path applies does
        the full agent loop generate a free reply. ``session_state`` carries the
        per-session variant memory + the chosen credit for desambiguación.
        """
        # ── Curated responses router (no-op when the tenant has no spec) ──
        canned = await self._try_canned(text, lead_state, session_state)
        if canned is not None:
            return canned

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
            # Turn resolved by free LLM generation (vs canned_keyword/canned_intent).
            "response_source": responses_engine.SOURCE_LLM,
        }

    # ── Curated responses (canned/scripted, tenant-agnostic) ─────────────

    def _responses_spec(self):
        """The active tenant's ResponsesSpec (empty/llm when none)."""
        tenant = getattr(self, "tenant", None)
        return getattr(tenant, "responses", None)

    def _verified_profile(self) -> dict | None:
        """The verified borrower profile from the tool registry (None if the gate
        is closed). Canned responses that fill account data require it."""
        reg = self.tool_registry
        if getattr(reg, "_identity_verified", False):
            return getattr(reg, "_debt_context", None) or None
        return None

    async def _try_canned(
        self, text: str, lead_state: dict, session_state: dict | None,
    ) -> dict[str, Any] | None:
        """Run the 2-layer router. Returns a full result dict when it handles the
        turn with a canned reply, else None (the agent loop proceeds normally).

        Layer 1 (keyword) is FREE. Layer 2 (hybrid) does ONE cheap classification
        call (short output) → canned. The verbatim/variant text + variable fill
        come from the engine; the LLM never authors the customer-facing copy here.
        """
        spec = self._responses_spec()
        if spec is None or not spec.enabled:
            return None

        profile = self._verified_profile()
        verified = bool(getattr(self.tool_registry, "_identity_verified", False))
        # Canned intents fill account data — without a verified profile the canned
        # text would have empty variables. Identity-free intents (saludo, despedida,
        # no_entendido, derivar_asesor) carry no account variables, so they're safe.
        # The data-driven requires_identity flag gates the rest.
        prof = profile or {}

        outcome = responses_engine.route_layer1(
            text, spec, prof, session_state=session_state, identity_verified=verified,
        )
        if outcome.handled:
            return await self._canned_result(outcome)

        if outcome.needs_llm_classification:
            intent = await self._classify_intent(text, spec)
            if intent:
                resolved = responses_engine.resolve_classified_intent(
                    intent, spec, prof, session_state=session_state, identity_verified=verified,
                )
                if resolved.handled:
                    return await self._canned_result(resolved)
        # No canned path → fall through to the full agent loop (free generation).
        return None

    async def _classify_intent(self, text: str, spec) -> str | None:
        """Cheap LLM classification: pick ONE intent from the spec's DATA-DRIVEN menu.

        The catalog ``{intent: description}`` is built from the tenant's
        responses.json (``classifier_menu``) — never hard-coded. Output is a single
        label → minimal output tokens (where Haiku cost lives). Returns the matched
        intent or None (then the agent generates freely).
        """
        menu = responses_engine.classifier_menu(spec)
        if not menu:
            return None
        catalog = "\n".join(f"- {name}: {desc}" for name, desc in menu.items())
        system = (
            "Eres un clasificador de intención. Dada la lista de intenciones (cada una "
            "con su descripción), responde EXACTAMENTE con el nombre de la que mejor "
            "encaja, sin explicación ni puntuación. Si ninguna encaja, responde "
            f"'ninguna'.\n\nIntenciones:\n{catalog}"
        )
        messages = [{"role": "user", "content": text[:500]}]
        try:
            resp = await self.provider.complete(
                system=system, messages=messages, tools=[], max_tokens=12,
            )
            raw = (resp.text or "").strip().lower()
            label = raw.split()[0] if raw else ""
            label = label.strip(".,:;\"'")
            if label in menu:
                return label
        except Exception:
            logger.opt(exception=True).debug("intent classification failed (non-blocking)")
        return None

    async def _canned_result(self, outcome) -> dict[str, Any]:
        """Build the standard process_message result dict for a canned reply.

        When the matched intent declares a ``tool`` (data-driven, in responses.json)
        the engine runs it against the existing ToolRegistry FIRST, so the canned
        copy reflects the side effect (e.g. validar_comprobante registers the
        voucher) and the UI/analytics see the tool. ``response_source``
        (canned_keyword | canned_intent) is surfaced so analytics can measure the
        % of turns resolved without LLM generation. Usage is zero (no generation).
        """
        tool_pairs: list[tuple[str, dict]] = []
        ui_actions: dict = {}
        if outcome.run_tool and self.tool_registry.has_tool(outcome.run_tool):
            try:
                tool_result = await self.tool_registry.execute(outcome.run_tool, {})
                tool_pairs = [(outcome.run_tool, tool_result)]
                ui_actions = build_ui_actions(tool_pairs)
            except Exception:
                logger.opt(exception=True).warning(
                    "canned intent tool '%s' failed (non-blocking)", outcome.run_tool
                )
        return {
            "content": outcome.text,
            "conversation_id": "",
            "response_id": f"canned_{outcome.intent}",
            "metadata": {"response_source": outcome.source, "intent": outcome.intent},
            "ui_actions": ui_actions,
            "tool_pairs": tool_pairs,
            "suggested_replies": None,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "model": self.provider.model,
                "provider": getattr(self.provider, "name", ""),
            },
            "latency_ms": 0,
            "tools_called": [name for name, _ in tool_pairs],
            "response_source": outcome.source,
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
