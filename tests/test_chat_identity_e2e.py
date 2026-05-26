"""End-to-end HTTP test of the identity gate through the real /api/v1/chat path.

The LLM provider is faked (no API key / network needed) by patching
`build_llm_provider`. The fake provider always asks to call `consultar_deuda` on
the first turn, then echoes whether the tool was blocked — so we observe whether
the ToolRegistry gate (wired from the resolved campaign token) lets it through.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest
from fastapi.testclient import TestClient

from core.llm import LLMProvider, LLMResponse, ToolCall


def _tokens():
    import api.main as m

    secret = m._CSRF_SECRET.encode()
    ts = str(int(time.time()))
    csrf = f"{ts}_{hmac.new(secret, ts.encode(), hashlib.sha256).hexdigest()}"
    payload = f"anonymous:{ts}"
    session = f"{payload}:{hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()}"
    return csrf, session


class _FakeProvider(LLMProvider):
    """Turn 1 → tool_call consultar_deuda; turn 2 → text echoing tool result."""

    def __init__(self):
        self._calls = 0

    async def complete(self, *, system, messages, tools, model=None, max_tokens=1024, force_tool=None):
        self._calls += 1
        if self._calls == 1:
            return LLMResponse(text="", tool_calls=[ToolCall(id="t1", name="consultar_deuda", input={})])
        # later turns: read the neutral tool result message we appended
        tool_payload = ""
        for msg in messages:
            if msg.get("role") == "tool":
                tool_payload = msg.get("content", "")
        verdict = "BLOCKED" if "identity_required" in tool_payload else "OK"
        return LLMResponse(text=f"verdict={verdict}", tool_calls=[])


@pytest.fixture
def client(monkeypatch):
    import api.main as m

    monkeypatch.setattr(m, "build_llm_provider", lambda *a, **k: _FakeProvider())
    # fresh in-memory store per test
    m.store = m.get_store()
    return TestClient(m.app)


def test_chat_with_token_opens_gate(client):
    csrf, session = _tokens()
    body = {
        "channel": "web",
        "tenant_id": "prestaunion",
        "text": "cuanto debo?",
        "campaign_token": "demo-juan",
    }
    r = client.post(
        "/api/v1/chat",
        json=body,
        headers={"X-CSRF-Token": csrf, "X-Session-Token": session},
    )
    assert r.status_code == 200, r.text
    assert "verdict=OK" in r.json()["message"]["content"]


def test_chat_without_token_keeps_gate_closed(client):
    csrf, session = _tokens()
    body = {
        "channel": "web",
        "tenant_id": "prestaunion",
        "text": "cuanto debo?",
    }
    r = client.post(
        "/api/v1/chat",
        json=body,
        headers={"X-CSRF-Token": csrf, "X-Session-Token": session},
    )
    assert r.status_code == 200, r.text
    assert "verdict=BLOCKED" in r.json()["message"]["content"]
