"""Multi-provider LLM abstraction (Strategy pattern).

The agent talks to an `LLMProvider` (neutral interface). Concrete providers
(`AnthropicProvider`, `OpenAIProvider`) translate the neutral request/response
to/from their SDK. No router, no LiteLLM — full control of the loop and native
Anthropic prompt caching.
"""

from core.llm.base import LLMProvider, LLMResponse, ToolCall, LLMError
from core.llm.factory import build_llm_provider

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "ToolCall",
    "LLMError",
    "build_llm_provider",
]
