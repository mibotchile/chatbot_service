"""Chathub inbound adapter + endpoint tests.

These tests mock the cobranza engine (no Doris, no LLM, no network). They cover:
  · payload → response for the 3 shapes (text / interactive / redirect handoff)
  · handoff redirect with receiver type agent and group
  · CT- token extraction → engine receives the token (identity resolution path)
  · multi-line debounced message joining
  · tenant routing by bot_path (slug fallback + explicit map + unknown → 404)
  · echo of unique_id in every shape
  · shared-secret auth: open when unset, enforced when set
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from features.messaging import chathub_adapter as ca
from features.messaging.chathub_adapter import (
    ChathubChatAdapter,
    ChathubChatRequest,
    build_chathub_response,
    check_auth,
    extract_ct_token,
    normalize_message,
    resolve_handoff_receiver,
    resolve_tenant,
    sanitize_bot_path,
)


# ── Pure helpers ─────────────────────────────────────────────────────────────


def test_extract_ct_token_found():
    assert extract_ct_token("Hola CT-abc123 quiero pagar") == "CT-abc123"


def test_extract_ct_token_first_line_of_multiline():
    msg = "CT-juan-2024\nbuenas\nquiero regularizar"
    assert extract_ct_token(msg) == "CT-juan-2024"


def test_extract_ct_token_absent():
    assert extract_ct_token("hola, cuanto debo?") is None
    assert extract_ct_token("") is None


def test_normalize_message_joins_debounced_bubbles():
    msg = "hola\n\nquiero  pagar\n  mi deuda  "
    assert normalize_message(msg) == "hola quiero pagar mi deuda"


def test_normalize_message_empty():
    assert normalize_message("") == ""
    assert normalize_message("\n\n  \n") == ""


def test_sanitize_bot_path():
    assert sanitize_bot_path("/prestamype/") == "prestamype"
    assert sanitize_bot_path("  PrestaMype ") == "prestamype"


# ── Tenant routing ────────────────────────────────────────────────────────────


def test_resolve_tenant_slug_fallback():
    exists = {"prestamype"}.__contains__
    assert resolve_tenant("/prestamype/", exists) == "prestamype"
    assert resolve_tenant("prestamype", exists) == "prestamype"


def test_resolve_tenant_unknown_returns_none():
    exists = {"prestamype"}.__contains__
    assert resolve_tenant("/nope/", exists) is None


def test_resolve_tenant_explicit_map(monkeypatch):
    monkeypatch.setenv("COBRANZA_CHATHUB_BOTPATH_MAP", json.dumps({"/cobranza-bot/": "prestamype"}))
    # bot_path slug ("cobranza-bot") is NOT a tenant, but the map points it to one.
    exists = {"prestamype"}.__contains__
    assert resolve_tenant("/cobranza-bot/", exists) == "prestamype"


def test_resolve_tenant_bad_map_falls_back(monkeypatch):
    monkeypatch.setenv("COBRANZA_CHATHUB_BOTPATH_MAP", "{not json")
    exists = {"prestamype"}.__contains__
    assert resolve_tenant("/prestamype/", exists) == "prestamype"


# ── Auth ──────────────────────────────────────────────────────────────────────


def test_check_auth_open_when_unset(monkeypatch):
    monkeypatch.delenv("COBRANZA_CHATHUB_TOKEN", raising=False)
    assert check_auth(None) is True
    assert check_auth("anything") is True


def test_check_auth_enforced_when_set(monkeypatch):
    monkeypatch.setenv("COBRANZA_CHATHUB_TOKEN", "s3cr3t")
    assert check_auth("s3cr3t") is True
    assert check_auth("wrong") is False
    assert check_auth(None) is False


# ── Handoff receiver resolution ─────────────────────────────────────────────────


def test_resolve_handoff_receiver_from_tenant_cfg_agent():
    cfg = {"handoff": {"type": "agent", "identifier": "asesor@onbotgo.com"}}
    assert resolve_handoff_receiver(cfg) == {"type": "agent", "identifier": "asesor@onbotgo.com"}


def test_resolve_handoff_receiver_from_tenant_cfg_group():
    cfg = {"handoff": {"type": "group", "identifier": "5"}}
    assert resolve_handoff_receiver(cfg) == {"type": "group", "identifier": "5"}


def test_resolve_handoff_receiver_default_group():
    assert resolve_handoff_receiver({}) == {"type": "group", "identifier": "1"}
    assert resolve_handoff_receiver(None) == {"type": "group", "identifier": "1"}


def test_resolve_handoff_receiver_env_fallback(monkeypatch):
    monkeypatch.setenv(
        "COBRANZA_CHATHUB_HANDOFF_RECEIVER",
        json.dumps({"type": "agent", "identifier": "ops@onbotgo.com"}),
    )
    assert resolve_handoff_receiver({}) == {"type": "agent", "identifier": "ops@onbotgo.com"}


# ── Response shaping (3 shapes) ──────────────────────────────────────────────────


def test_build_response_text():
    out = build_chathub_response(
        engine_result={"content": "Hola, soy Ada.", "tool_pairs": [], "ui_actions": {}},
        unique_id="u-1",
        tenant_cfg={},
    )
    assert out == {"type": "text", "response": "Hola, soy Ada.", "unique_id": "u-1"}


def test_build_response_interactive():
    interactive = {
        "type": "button",
        "body": {"text": "¿Cómo querés regularizar?"},
        "action": {"buttons": [{"type": "reply", "reply": {"id": "total", "title": "Pago total"}}]},
    }
    out = build_chathub_response(
        engine_result={
            "content": "Tenés estas opciones:",
            "tool_pairs": [],
            "ui_actions": {"interactive": interactive},
        },
        unique_id="u-2",
        tenant_cfg={},
    )
    assert out["type"] == "interactive"
    assert out["content"] == interactive
    assert out["unique_id"] == "u-2"
    assert out["response"] == "Tenés estas opciones:"


def test_build_response_redirect_agent():
    out = build_chathub_response(
        engine_result={
            "content": "Te derivo con un asesor.",
            "tool_pairs": [("escalate_to_human", {"escalated": True, "reason": "legal"})],
            "ui_actions": {},
        },
        unique_id="u-3",
        tenant_cfg={"handoff": {"type": "agent", "identifier": "asesor@onbotgo.com"}},
    )
    assert out["type"] == "redirect"
    assert out["content"] == {"receiver": {"type": "agent", "identifier": "asesor@onbotgo.com"}}
    assert out["response"] == "Te derivo con un asesor."
    assert out["unique_id"] == "u-3"


def test_build_response_redirect_group_default():
    out = build_chathub_response(
        engine_result={
            "content": "",  # no farewell from engine → adapter supplies one
            "tool_pairs": [("escalate_to_human", {"escalated": True, "reason": "x"})],
            "ui_actions": {},
        },
        unique_id="u-4",
        tenant_cfg={},
    )
    assert out["type"] == "redirect"
    assert out["content"] == {"receiver": {"type": "group", "identifier": "1"}}
    assert out["response"]  # non-empty fallback farewell


# ── Adapter.handle wiring (mocked engine) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_adapter_passes_token_and_normalized_text():
    captured = {}

    async def fake_engine(**kwargs):
        captured.update(kwargs)
        return {"content": "ok", "tool_pairs": [], "ui_actions": {}}

    adapter = ChathubChatAdapter(engine_runner=fake_engine)
    body = ChathubChatRequest(
        channel_id="ch1",
        message="CT-juan\nhola\nquiero pagar",
        unique_id="uid-9",
        chathub_conversation_id="conv-42",
        chathub_project_id="proj-x",
    )
    out = await adapter.handle(body=body, tenant_id="prestamype", tenant_cfg={})

    assert captured["campaign_token"] == "CT-juan"
    assert captured["text"] == "CT-juan hola quiero pagar"
    assert captured["tenant_id"] == "prestamype"
    assert captured["channel"] == "whatsapp"
    # Stable conversation id binds the chathub conversation across turns.
    assert captured["conversation_id"] == "chathub-prestamype-conv-42"
    assert out["unique_id"] == "uid-9"


def test_conversation_id_is_stable_per_chathub_conv():
    a = ChathubChatAdapter.conversation_id_for("prestamype", "c1")
    b = ChathubChatAdapter.conversation_id_for("prestamype", "c1")
    c = ChathubChatAdapter.conversation_id_for("prestamype", "c2")
    assert a == b
    assert a != c


# ── Endpoint (TestClient, engine mocked) ─────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    """TestClient with the engine turn mocked — observes routing/auth/shaping
    without touching the LLM or Doris."""
    import api.routers.chathub as ch
    import api.main as m

    async def fake_engine(**kwargs):
        text = kwargs.get("text", "")
        # Simulate a handoff when the user message asks for a human.
        if "asesor" in text.lower():
            return {
                "content": "Te derivo con un asesor.",
                "tool_pairs": [("escalate_to_human", {"escalated": True, "reason": "user"})],
                "ui_actions": {},
            }
        return {
            "content": f"echo: {text}",
            "tool_pairs": [],
            "ui_actions": {},
            "_token": kwargs.get("campaign_token"),
        }

    # Rebuild the adapter against the fake engine, and route the module-level one.
    monkeypatch.setattr(ch, "_adapter", ch.ChathubChatAdapter(engine_runner=fake_engine))
    return TestClient(m.app)


def _payload(**over):
    base = {
        "channel_id": "ch1",
        "message": "hola, cuanto debo?",
        "unique_id": "uid-1",
        "platform": "chathub",
        "chathub_conversation_id": "conv-1",
        "chathub_project_id": "proj-1",
    }
    base.update(over)
    return base


def test_endpoint_text_shape(client):
    r = client.post("/prestamype/chat", json=_payload())
    assert r.status_code == 200
    data = r.json()
    assert data["type"] == "text"
    assert data["response"] == "echo: hola, cuanto debo?"
    assert data["unique_id"] == "uid-1"


def test_endpoint_token_reaches_engine(client):
    r = client.post("/prestamype/chat", json=_payload(message="CT-demo-1 hola"))
    assert r.status_code == 200
    assert r.json()["type"] == "text"
    # The fake engine echoes the normalized text incl. the token text.
    assert "CT-demo-1" in r.json()["response"]


def test_endpoint_handoff_redirect(client):
    r = client.post("/prestamype/chat", json=_payload(message="quiero hablar con un asesor"))
    assert r.status_code == 200
    data = r.json()
    assert data["type"] == "redirect"
    assert data["content"]["receiver"]["type"] in ("agent", "group")
    assert data["unique_id"] == "uid-1"


def test_endpoint_unknown_tenant_404(client):
    r = client.post("/no-such-bot/chat", json=_payload())
    assert r.status_code == 404


def test_endpoint_auth_open_when_unset(client, monkeypatch):
    monkeypatch.delenv("COBRANZA_CHATHUB_TOKEN", raising=False)
    r = client.post("/prestamype/chat", json=_payload())
    assert r.status_code == 200


def test_endpoint_auth_rejects_without_token_when_set(client, monkeypatch):
    monkeypatch.setenv("COBRANZA_CHATHUB_TOKEN", "s3cr3t")
    r = client.post("/prestamype/chat", json=_payload())
    assert r.status_code == 401


def test_endpoint_auth_accepts_with_token_when_set(client, monkeypatch):
    monkeypatch.setenv("COBRANZA_CHATHUB_TOKEN", "s3cr3t")
    r = client.post(
        "/prestamype/chat",
        json=_payload(),
        headers={"X-Chathub-Token": "s3cr3t"},
    )
    assert r.status_code == 200
