"""Tests for the multi-provider LLM abstraction (Strategy adapter).

No network: SDK responses are faked. Covers:
  (a) factory returns the right provider per flag,
  (b) OpenAIProvider translates tools → function format and parses tool_calls
      whose `arguments` come as a JSON STRING → dict,
  (c) AnthropicProvider parses `tool_use` content blocks,
  (d) the neutral tool schema never leaks account_id/borrower_id (security fix).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.llm import build_llm_provider
from core.llm.anthropic_provider import AnthropicProvider
from core.llm.openai_provider import OpenAIProvider
from config.tools_schema import TOOL_DEFINITIONS


def _settings(provider: str):
    return SimpleNamespace(
        llm_provider=provider,
        anthropic_api_key="sk-ant-test",
        anthropic_model="claude-haiku-4-5-20251001",
        openai_api_key="sk-openai-test",
        openai_model="gpt-4o",
    )


# ── (a) factory ──────────────────────────────────────────────────────────

def test_factory_returns_anthropic_by_flag():
    p = build_llm_provider(_settings("anthropic"))
    assert isinstance(p, AnthropicProvider)
    assert p.model == "claude-haiku-4-5-20251001"


def test_factory_returns_openai_by_flag():
    p = build_llm_provider(_settings("openai"))
    assert isinstance(p, OpenAIProvider)
    assert p.model == "gpt-4o"


def test_factory_unknown_provider_raises():
    with pytest.raises(ValueError):
        build_llm_provider(_settings("gemini"))


def test_factory_api_key_override():
    # override is used; settings key ignored
    p = build_llm_provider(_settings("anthropic"), api_key_override="sk-tenant-x")
    assert isinstance(p, AnthropicProvider)


def test_providers_build_without_key():
    """Server must boot with either flag even if that provider's key is absent."""
    s = SimpleNamespace(
        llm_provider="openai", anthropic_api_key="", anthropic_model="m",
        openai_api_key="", openai_model="gpt-4o",
    )
    assert isinstance(build_llm_provider(s), OpenAIProvider)  # no raise despite empty key
    s.llm_provider = "anthropic"
    assert isinstance(build_llm_provider(s), AnthropicProvider)


# ── (b) OpenAIProvider: tool translation + tool_call parsing ──────────────

def test_openai_translates_tools_to_function_format():
    tools = OpenAIProvider._tools_to_openai(TOOL_DEFINITIONS)
    assert all(t["type"] == "function" for t in tools)
    by_name = {t["function"]["name"]: t for t in tools}
    assert "consultar_deuda" in by_name
    # neutral `parameters` carried straight into function.parameters
    assert by_name["consultar_deuda"]["function"]["parameters"]["type"] == "object"
    assert "description" in by_name["registrar_reclamo"]["function"]


def test_openai_parses_tool_calls_with_string_args():
    """`arguments` arrives as a JSON STRING → must be parsed to a dict."""
    fake = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=None,
        tool_calls=[SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(
                name="registrar_reclamo",
                arguments='{"tipo": "reclamo", "descripcion": "cobro indebido"}',
            ),
        )],
    ))])
    resp = OpenAIProvider._parse(fake)
    assert resp.text == ""
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.id == "call_1"
    assert tc.name == "registrar_reclamo"
    assert tc.input == {"tipo": "reclamo", "descripcion": "cobro indebido"}  # string→dict


def test_openai_parses_plain_text_response():
    fake = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content="Tu saldo es S/ 4,850.00.", tool_calls=None,
    ))])
    resp = OpenAIProvider._parse(fake)
    assert resp.text == "Tu saldo es S/ 4,850.00."
    assert resp.tool_calls == []


def test_openai_serializes_neutral_messages():
    from core.llm import ToolCall

    msgs = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "", "tool_calls": [ToolCall(id="c1", name="consultar_deuda", input={})]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"balance": 0}'},
    ]
    out = OpenAIProvider._messages_to_openai("SYS", msgs)
    assert out[0] == {"role": "system", "content": "SYS"}
    # assistant tool-call turn serialized with arguments as a JSON string
    asst = out[2]
    assert asst["tool_calls"][0]["type"] == "function"
    assert asst["tool_calls"][0]["function"]["arguments"] == "{}"
    # tool result mapped to role:tool
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"balance": 0}'}


# ── (c) AnthropicProvider: parse tool_use blocks + caching translation ────

def test_anthropic_parses_tool_use_blocks():
    fake = SimpleNamespace(content=[
        SimpleNamespace(type="text", text="déjame consultar"),
        SimpleNamespace(type="tool_use", id="tu_1", name="consultar_deuda", input={}),
    ])
    resp = AnthropicProvider._parse(fake)
    assert resp.text == "déjame consultar"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].id == "tu_1"
    assert resp.tool_calls[0].name == "consultar_deuda"
    assert resp.tool_calls[0].input == {}


def test_anthropic_translates_tools_to_input_schema():
    tools = AnthropicProvider._tools_to_anthropic(TOOL_DEFINITIONS)
    by_name = {t["name"]: t for t in tools}
    assert "input_schema" in by_name["consultar_deuda"]
    assert by_name["consultar_deuda"]["input_schema"]["type"] == "object"


def test_anthropic_serializes_neutral_messages():
    from core.llm import ToolCall

    msgs = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "ok", "tool_calls": [ToolCall(id="tu1", name="consultar_deuda", input={})]},
        {"role": "tool", "tool_call_id": "tu1", "content": '{"balance": 0}'},
    ]
    out = AnthropicProvider._messages_to_anthropic(msgs)
    # assistant turn → content blocks with a tool_use block
    asst_blocks = out[1]["content"]
    assert any(b["type"] == "tool_use" and b["id"] == "tu1" for b in asst_blocks)
    # tool result → user message with tool_result block
    assert out[2]["role"] == "user"
    assert out[2]["content"][0]["type"] == "tool_result"
    assert out[2]["content"][0]["tool_use_id"] == "tu1"


# ── (d) security: no account_id/borrower_id leaks in the neutral schema ───

def test_no_account_id_leak_in_neutral_schema():
    blob = json.dumps(TOOL_DEFINITIONS).lower()
    assert "account_id" not in blob
    assert "borrower_id" not in blob
