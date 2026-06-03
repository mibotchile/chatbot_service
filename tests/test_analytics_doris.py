"""Tests for the Doris analytics sink + pricing + project_uid propagation.

NONE of these touch a live Doris instance — the pydoris client is mocked. They
verify:
  - cost_usd computation (Haiku 4.5 verified rates).
  - the sink no-ops when analytics is not configured.
  - the sink swallows Doris errors (fire-and-forget, never raises).
  - project_uid + tenant_id + channel propagate into the written rows.
  - datetime_utc is UTC, formatted for Doris (no tz suffix, 'YYYY-MM-DD HH:MM:SS').
  - TenantConfig loads project_uid from tenant.config.json.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tenancy import pricing
from features.conversation.agent import SoreliaAgent
from shared.llm import LLMProvider, LLMResponse
from tenancy.tenant_loader import TenantConfig as TC
from features.analytics import analytics_sink

_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


# ── Pricing ─────────────────────────────────────────────────────────────────

def test_cost_usd_haiku_45_known_values():
    # 1M input @ $1, 1M output @ $5.
    assert pricing.compute_cost_usd("claude-haiku-4-5-20251001", 1_000_000, 0) == 1.0
    assert pricing.compute_cost_usd("claude-haiku-4-5-20251001", 0, 1_000_000) == 5.0
    # 1000 in + 500 out = 0.001 + 0.0025 = 0.0035.
    assert pricing.compute_cost_usd("claude-haiku-4-5-20251001", 1000, 500) == 0.0035


def test_cost_usd_unknown_model_uses_default_nonzero():
    # Unknown model falls back to DEFAULT_PRICING (Haiku rate) — never zero.
    assert pricing.compute_cost_usd("some-future-model", 1000, 1000) > 0


def test_cost_usd_rounds_to_6_decimals():
    cost = pricing.compute_cost_usd("claude-haiku-4-5-20251001", 1, 1)
    assert cost == round(cost, 6)


# ── datetime UTC formatting ──────────────────────────────────────────────────

def test_now_utc_format_and_timezone():
    ts = analytics_sink._now_utc()
    assert _DT_RE.match(ts), ts
    # Must be within a few seconds of real UTC now.
    parsed = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert delta < 5


# ── Sink: no-op + error swallowing ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_sink_noop_when_not_configured(monkeypatch):
    monkeypatch.setattr(analytics_sink, "analytics_enabled", lambda: False)
    # Should NOT attempt any client construction — just return cleanly.
    monkeypatch.setattr(
        analytics_sink, "_client",
        lambda: (_ for _ in ()).throw(AssertionError("client must not be built")),
    )
    await analytics_sink.record_interaction(
        project_uid="QUIdI0iwQY0l3pJwRKLB", tenant_id="prestamype",
        session_id="s1", channel="web", interaction_id="i1",
        user_text="hola", assistant_text="hola!",
    )
    await analytics_sink.record_llm_usage(
        project_uid="QUIdI0iwQY0l3pJwRKLB", tenant_id="prestamype",
        session_id="s1", interaction_id="i1", provider="anthropic",
        model="claude-haiku-4-5-20251001", input_tokens=10, output_tokens=5,
    )


@pytest.mark.asyncio
async def test_sink_swallows_doris_errors(monkeypatch):
    monkeypatch.setattr(analytics_sink, "analytics_enabled", lambda: True)

    def _boom(table, rows):
        raise RuntimeError("doris down")

    monkeypatch.setattr(analytics_sink, "_write_rows", _boom)
    # Must NOT raise — fire-and-forget contract.
    await analytics_sink.record_interaction(
        project_uid="X", tenant_id="prestamype", session_id="s", channel="web",
        interaction_id="i", user_text="u", assistant_text="a",
    )
    await analytics_sink.record_llm_usage(
        project_uid="X", tenant_id="prestamype", session_id="s",
        interaction_id="i", provider="anthropic", model="claude-haiku-4-5-20251001",
        input_tokens=1, output_tokens=1,
    )


# ── Sink: rows are well-formed (project_uid, channel, UTC, raw content) ───────

@pytest.mark.asyncio
async def test_record_interaction_rows(monkeypatch):
    monkeypatch.setattr(analytics_sink, "analytics_enabled", lambda: True)
    captured: dict = {}

    def _capture(table, rows):
        captured["table"] = table
        captured["rows"] = rows

    monkeypatch.setattr(analytics_sink, "_write_rows", _capture)

    await analytics_sink.record_interaction(
        project_uid="QUIdI0iwQY0l3pJwRKLB", tenant_id="prestamype",
        session_id="sess-123", channel="whatsapp", interaction_id="int-9",
        user_text="Quiero pagar mi cuota",
        assistant_text="Claro, te ayudo con eso.",
        tools_called=["consultar_deuda"], latency_ms=842,
    )

    assert captured["table"] == "bot_interactions"
    rows = captured["rows"]
    assert len(rows) == 2
    user_row, asst_row = rows
    assert user_row["role"] == "user"
    assert asst_row["role"] == "assistant"
    # project_uid propagates.
    assert all(r["project_uid"] == "QUIdI0iwQY0l3pJwRKLB" for r in rows)
    assert all(r["tenant_id"] == "prestamype" for r in rows)
    assert all(r["channel"] == "whatsapp" for r in rows)
    assert all(r["interaction_id"] == "int-9" for r in rows)
    # content stored RAW (not masked).
    assert user_row["content"] == "Quiero pagar mi cuota"
    assert asst_row["content"] == "Claro, te ayudo con eso."
    # tools + latency on the assistant row only.
    assert asst_row["tools_called"] == "consultar_deuda"
    assert asst_row["latency_ms"] == 842
    assert user_row["latency_ms"] is None
    # UTC datetime format, shared across the turn.
    assert _DT_RE.match(user_row["datetime_utc"])
    assert user_row["datetime_utc"] == asst_row["datetime_utc"]


@pytest.mark.asyncio
async def test_record_llm_usage_row_cost_and_uid(monkeypatch):
    monkeypatch.setattr(analytics_sink, "analytics_enabled", lambda: True)
    captured: dict = {}
    monkeypatch.setattr(
        analytics_sink, "_write_rows",
        lambda table, rows: captured.update(table=table, rows=rows),
    )

    await analytics_sink.record_llm_usage(
        project_uid="QUIdI0iwQY0l3pJwRKLB", tenant_id="prestamype",
        session_id="sess-1", interaction_id="int-1", provider="anthropic",
        model="claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=500,
    )

    assert captured["table"] == "bot_llm_usage"
    (row,) = captured["rows"]
    assert row["project_uid"] == "QUIdI0iwQY0l3pJwRKLB"
    assert row["input_tokens"] == 1000
    assert row["output_tokens"] == 500
    assert row["cost_usd"] == 0.0035
    assert _DT_RE.match(row["datetime_utc"])


# ── TenantConfig loads project_uid ────────────────────────────────────────────

def test_tenant_config_loads_project_uid():
    root = Path(__file__).resolve().parent.parent / "tenants" / "prestamype"
    cfg = TC.from_directory(root)
    assert cfg.project_uid == "QUIdI0iwQY0l3pJwRKLB"


def test_tenant_config_project_uid_optional_for_prestaunion():
    root = Path(__file__).resolve().parent.parent / "tenants" / "prestaunion"
    cfg = TC.from_directory(root)
    assert cfg.project_uid is None


# ── Agent surfaces aggregated usage + latency for the sink ────────────────────

class _Usage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Raw:
    def __init__(self, input_tokens, output_tokens):
        self.usage = _Usage(input_tokens, output_tokens)


class _UsageProvider(LLMProvider):
    """No tool calls → single LLM call per turn, with a usage-bearing raw."""

    name = "anthropic"
    model = "claude-haiku-4-5-20251001"

    async def complete(self, *, system, messages, tools, model=None, max_tokens=1024, force_tool=None):
        # force_tool path = the forced chip pass; give it its own usage too.
        if force_tool:
            return LLMResponse(text="", tool_calls=[], raw=_Raw(7, 3))
        return LLMResponse(text="Hola, te ayudo.", tool_calls=[], raw=_Raw(100, 40))


@pytest.mark.asyncio
async def test_agent_aggregates_usage_and_latency():
    agent = SoreliaAgent(provider=_UsageProvider())
    result = await agent.process_message(
        text="hola", conversation_id="c1", history=[],
        lead_state={"level": "cold", "collected": {}}, page_context={}, channel="web",
    )
    usage = result["usage"]
    # First call (100/40) + forced-chip call (7/3) are both accumulated.
    assert usage["input_tokens"] == 107
    assert usage["output_tokens"] == 43
    assert usage["provider"] == "anthropic"
    assert usage["model"] == "claude-haiku-4-5-20251001"
    assert isinstance(result["latency_ms"], int)
    assert result["latency_ms"] >= 0
    assert result["tools_called"] == []
