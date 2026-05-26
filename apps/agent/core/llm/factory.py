"""Factory: pick the LLMProvider from settings.llm_provider.

Only the ACTIVE provider is instantiated — the inactive provider's key may be
absent without crashing (so the server boots with either flag).
"""

from __future__ import annotations

from typing import Any

from core.llm.base import LLMProvider


def build_llm_provider(settings: Any, *, api_key_override: str | None = None) -> LLMProvider:
    """Build the configured provider.

    Args:
        settings: the Settings instance (reads llm_provider, *_api_key, *_model).
        api_key_override: per-request key (e.g. resolved per tenant for Anthropic).
            Falls back to the provider's settings key when None/empty.
    """
    provider = (getattr(settings, "llm_provider", "anthropic") or "anthropic").lower()

    if provider == "openai":
        from core.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(
            api_key=api_key_override or settings.openai_api_key,
            model=settings.openai_model,
        )

    if provider == "anthropic":
        from core.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=api_key_override or settings.anthropic_api_key,
            model=settings.anthropic_model,
        )

    raise ValueError(f"Unknown llm_provider: {provider!r} (expected 'anthropic' or 'openai')")
