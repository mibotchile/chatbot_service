"""Sorelia agent core — single LLM call with function calling."""

import json
from typing import Any

from loguru import logger
from prompts.system import build_system_prompt
from config.tools_schema import TOOL_DEFINITIONS
from config.settings import settings
from tools import ToolRegistry
from core.response_builder import build_ui_actions
from core.prospect_profile import build_prospect_profile, truncate_history


class SoreliaAgent:
    """Core agent: context assembly → LLM with tools → tool execution → response."""

    def __init__(self, llm_client, tool_registry: ToolRegistry | None = None, tenant=None):
        self.llm_client = llm_client
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

        # Inject profile as first message if we truncated
        messages = []
        if len(history) > len(recent) and profile:
            messages.append({"role": "user", "content": f"[Contexto de la conversacion anterior]\n{profile}"})
            messages.append({"role": "assistant", "content": "Entendido, continuo la conversacion con ese contexto."})
        messages.extend(recent)
        messages.append({"role": "user", "content": text})

        model = settings.anthropic_model
        token_savings = len(history) - len(recent)
        logger.info(f"Agent call | conv={conversation_id[:12]} | msg='{text[:50]}' | lead={lead_state.get('level', '?')} | history={len(history)} truncated={token_savings}")

        # Anthropic prompt caching: cache system prompt across turns
        system_with_cache = [{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }]

        # Filter tools per tenant config (agent.excluded_tools in tenant.config.json)
        _excluded_tools = set()
        _tenant = getattr(self, "tenant", None)
        if _tenant and hasattr(_tenant, "excluded_tools"):
            _excluded_tools = set(_tenant.excluded_tools or [])
        tools = [t for t in TOOL_DEFINITIONS if t["name"] not in _excluded_tools] if _excluded_tools else TOOL_DEFINITIONS

        # Single LLM call with tools
        response = await self.llm_client.messages.create(
            model=model,
            max_tokens=1024,
            system=system_with_cache,
            messages=messages,
            tools=tools,
        )

        # Parse Anthropic response content blocks
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        text_blocks = [b for b in response.content if b.type == "text"]

        # If LLM wants to call tools
        ui_actions = {}
        tool_pairs = []
        suggested_replies = None

        # Separate suggest_quick_replies from other tools
        data_tools = [b for b in tool_use_blocks if b.name != "suggest_quick_replies"]
        chip_tools = [b for b in tool_use_blocks if b.name == "suggest_quick_replies"]

        if data_tools:
            tool_names = [b.name for b in data_tools]
            logger.info(f"Tool calls: {tool_names}")
            tool_results = await self._execute_tools(data_tools)

            tool_pairs = [
                (block.name, result)
                for block, result in zip(data_tools, tool_results)
            ]
            ui_actions = build_ui_actions(tool_pairs)

            # Add assistant response + tool results to messages, call LLM again
            messages.append({"role": "assistant", "content": response.content})

            tool_result_content = [
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
                for block, result in zip(data_tools, tool_results)
            ]
            # Include chip tool results too if present
            for block in chip_tools:
                chip_result = await self.tool_registry.execute(block.name, block.input)
                suggested_replies = chip_result.get("options", [])
                tool_result_content.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(chip_result, ensure_ascii=False),
                })

            messages.append({"role": "user", "content": tool_result_content})

            final_response = await self.llm_client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_with_cache,
                messages=messages,
                tools=TOOL_DEFINITIONS,
            )

            content = next((b.text for b in final_response.content if b.type == "text"), "")

            # Check if final response also has chip suggestions
            for b in final_response.content:
                if b.type == "tool_use" and b.name == "suggest_quick_replies":
                    chip_result = await self.tool_registry.execute(b.name, b.input)
                    suggested_replies = chip_result.get("options", [])
        else:
            content = next((b.text for b in text_blocks), "")

            # Handle chip tools from non-data-tool responses
            for block in chip_tools:
                chip_result = await self.tool_registry.execute(block.name, block.input)
                suggested_replies = chip_result.get("options", [])

        # Force chip generation if LLM didn't call suggest_quick_replies
        if not suggested_replies and content:
            suggested_replies = await self._force_chip_generation(
                content, lead_state, tool_pairs, system_with_cache, model,
            )

        return {
            "content": content or "",
            "conversation_id": conversation_id,
            "response_id": f"sorelia_{id(response)}",
            "metadata": {},
            "ui_actions": ui_actions,
            "tool_pairs": tool_pairs,
            "suggested_replies": suggested_replies,
        }

    async def _force_chip_generation(
        self,
        content: str,
        lead_state: dict,
        tool_pairs: list[tuple[str, dict]],
        system_with_cache: list[dict],
        model: str,
    ) -> list[str] | None:
        """Lightweight LLM call to force quick reply generation when main flow didn't produce them."""
        chip_tool = next((t for t in TOOL_DEFINITIONS if t["name"] == "suggest_quick_replies"), None)
        if not chip_tool:
            return None

        tools_called = [name for name, _ in tool_pairs] if tool_pairs else ["ninguna"]
        collected = lead_state.get("collected", {})

        try:
            chip_response = await self.llm_client.messages.create(
                model=model,
                max_tokens=256,
                system=[{"type": "text", "text": (
                    "Genera opciones de respuesta rapida para el usuario. "
                    "Deben ser 2-4 opciones cortas (2-5 palabras), coherentes con lo que acabas de decir. "
                    "Usa datos reales: nombres de proyectos, distritos, acciones concretas."
                )}],
                messages=[{"role": "user", "content": (
                    f"Tu respuesta al cliente: {content[:300]}\n"
                    f"Tools usadas: {', '.join(tools_called)}\n"
                    f"Datos del lead: {json.dumps(collected, ensure_ascii=False)}"
                )}],
                tools=[chip_tool],
                tool_choice={"type": "tool", "name": "suggest_quick_replies"},
            )

            for b in chip_response.content:
                if b.type == "tool_use" and b.name == "suggest_quick_replies":
                    result = await self.tool_registry.execute(b.name, b.input)
                    options = result.get("options", [])
                    if options:
                        logger.debug(f"Forced chip generation: {options}")
                        return options
        except Exception:
            logger.opt(exception=True).debug("Forced chip generation failed (non-blocking)")

        return None

    async def _execute_tools(self, tool_use_blocks) -> list[dict]:
        results = []
        for block in tool_use_blocks:
            result = await self.tool_registry.execute(block.name, block.input)
            results.append(result)
        return results


class _PromptBuilder:
    def __init__(self, tenant=None):
        self.tenant = tenant

    def build(self, lead_state: dict, page_context: dict, history: list[dict] | None = None, channel: str = "web") -> str:
        return build_system_prompt(lead_state=lead_state, page_context=page_context, history=history, channel=channel, tenant=self.tenant)
