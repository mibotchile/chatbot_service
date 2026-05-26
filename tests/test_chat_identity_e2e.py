"""End-to-end HTTP test of the identity gate through the real /api/v1/chat path.

The Anthropic client is faked (no API key / network needed). The fake LLM
always asks to call `consultar_deuda`, so we observe whether the ToolRegistry
gate — wired from the resolved campaign token — lets it through or blocks it.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest
from fastapi.testclient import TestClient


def _tokens():
    import api.main as m

    secret = m._CSRF_SECRET.encode()
    ts = str(int(time.time()))
    csrf = f"{ts}_{hmac.new(secret, ts.encode(), hashlib.sha256).hexdigest()}"
    payload = f"anonymous:{ts}"
    session = f"{payload}:{hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()}"
    return csrf, session


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Resp:
    def __init__(self, content):
        self.content = content


class _FakeMessages:
    """First create() asks for consultar_deuda; second returns final text echoing
    whether the tool was blocked."""

    def __init__(self):
        self._calls = 0
        self.last_tool_result = None

    async def create(self, **kwargs):
        self._calls += 1
        if self._calls == 1:
            return _Resp([
                _Block(type="tool_use", id="t1", name="consultar_deuda", input={}),
            ])
        # second call: messages include our tool_result; surface it as text
        import json as _json
        msgs = kwargs.get("messages", [])
        tool_payload = ""
        for msg in msgs:
            content = msg.get("content")
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "tool_result":
                        tool_payload = c.get("content", "")
        self.last_tool_result = tool_payload
        verdict = "BLOCKED" if "identity_required" in tool_payload else "OK"
        return _Resp([_Block(type="text", text=f"verdict={verdict}")])


class _FakeAnthropic:
    def __init__(self, *a, **kw):
        self.messages = _FakeMessages()


@pytest.fixture
def client(monkeypatch):
    import api.main as m

    monkeypatch.setattr(m.anthropic, "AsyncAnthropic", _FakeAnthropic)
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
