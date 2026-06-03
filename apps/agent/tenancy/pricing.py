"""LLM model pricing (USD per million tokens) and cost computation.

Used by the analytics sink to record ``cost_usd`` per turn in
``cobranza_analytics.bot_llm_usage``. Prices are USD per 1,000,000 tokens.

Verified against Anthropic docs (2026-05-27):
  - claude-haiku-4-5: $1.00 / MTok input, $5.00 / MTok output.

OpenAI gpt-4o is included as a provisional reference; VERIFY before relying on
its cost figures. Unknown models fall back to ``DEFAULT_PRICING`` (clearly the
Haiku 4.5 rate) so cost is never zero — adjust if a new model is introduced.
"""

from __future__ import annotations

# (input_price_per_mtok, output_price_per_mtok)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Anthropic — VERIFIED 2026-05-27 (platform.claude.com pricing).
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # OpenAI — PROVISIONAL, verify before trusting cost figures.
    "gpt-4o": (2.50, 10.00),
}

# Fallback for any model not in the table (Haiku 4.5 rate — the active default
# model). Keeps cost_usd > 0 rather than silently dropping to zero.
DEFAULT_PRICING: tuple[float, float] = (1.00, 5.00)


def get_pricing(model: str) -> tuple[float, float]:
    """Return (input_price, output_price) per MTok for ``model``."""
    return MODEL_PRICING.get(model, DEFAULT_PRICING)


def compute_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute the USD cost of a turn from token counts.

    ``cost_usd = input_tokens/1e6 * in_price + output_tokens/1e6 * out_price``.
    Rounded to 6 decimals to match the DECIMAL(12,6) column in Doris.
    """
    in_price, out_price = get_pricing(model)
    cost = (input_tokens or 0) / 1e6 * in_price + (output_tokens or 0) / 1e6 * out_price
    return round(cost, 6)
