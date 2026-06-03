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

from shared.llm import LLMProvider, LLMResponse, ToolCall


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


def test_response_identity_includes_business_name(client):
    """Bloque 3: the chat response exposes identity.business_name so the widget
    strip can show the business as subline (consistent with the token strip)."""
    csrf, session = _tokens()
    r = client.post(
        "/api/v1/chat",
        json={"channel": "web", "tenant_id": "prestaunion", "text": "hola", "campaign_token": "demo-juan"},
        headers={"X-CSRF-Token": csrf, "X-Session-Token": session},
    )
    assert r.status_code == 200, r.text
    ident = r.json()["message"]["identity"]
    assert ident["verified"] is True
    assert ident["display_name"] == "Juan Pérez Rojas"
    assert ident["business_name"] == "Bodega Don Juan E.I.R.L."


class _CertProvider(LLMProvider):
    """Turn 1 → emitir_certificado_no_adeudo; turn 2 → plain text."""

    def __init__(self):
        self._calls = 0

    async def complete(self, *, system, messages, tools, model=None, max_tokens=1024, force_tool=None):
        self._calls += 1
        if self._calls == 1:
            return LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="emitir_certificado_no_adeudo", input={})])
        return LLMResponse(text="Tu certificado está listo.", tool_calls=[])


def test_response_document_field_for_certificate(monkeypatch):
    """Bloque 3: when the certificate tool runs, the response carries a structured
    `document` field so the widget renders the download chip regardless of LLM wording."""
    import api.main as m

    monkeypatch.setattr(m, "build_llm_provider", lambda *a, **k: _CertProvider())
    m.store = m.get_store()
    client = TestClient(m.app)
    csrf, session = _tokens()
    r = client.post(
        "/api/v1/chat",
        json={"channel": "web", "tenant_id": "prestaunion", "text": "mi certificado", "campaign_token": "demo-maria"},
        headers={"X-CSRF-Token": csrf, "X-Session-Token": session},
    )
    assert r.status_code == 200, r.text
    doc = r.json()["message"]["document"]
    assert doc is not None
    assert doc["filename"].endswith(".pdf")
    assert "/api/v1/cobranza/certificate/" in doc["download_url"]


# ── Rate limiting through the real /api/v1/chat path ───────────────────────


def test_chat_per_min_returns_429_with_retry_after(monkeypatch):
    """A short burst over chat/min → 429 with Retry-After; message stays neutral.

    Reuses _FakeProvider (proven to return 200 through the full post-processing
    path) so the 200 turns are clean and the assertion isolates the 429.
    """
    import api.main as m

    monkeypatch.setattr(m, "build_llm_provider", lambda *a, **k: _FakeProvider())
    monkeypatch.setattr(m.rate_limiter.config, "chat_per_min", 2)
    m.store = m.get_store()
    client = TestClient(m.app)
    csrf, session = _tokens()
    headers = {"X-CSRF-Token": csrf, "X-Session-Token": session}
    # campaign_token resolves identity so the post-processing (quick replies) has
    # a populated lead — keeps the 200 turns clean and isolates the 429 assertion.
    body = {"channel": "web", "tenant_id": "prestaunion", "text": "hola", "campaign_token": "demo-juan"}

    assert client.post("/api/v1/chat", json=body, headers=headers).status_code == 200
    assert client.post("/api/v1/chat", json=body, headers=headers).status_code == 200
    r = client.post("/api/v1/chat", json=body, headers=headers)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1
    assert "chat_per_min" not in r.text  # internal reason not leaked


class _IdentSweepProvider(LLMProvider):
    """Each turn asks to identify with the DNI carried in the user text."""

    async def complete(self, *, system, messages, tools, model=None, max_tokens=1024, force_tool=None):
        # On a tool result turn, just echo it.
        for msg in messages:
            if msg.get("role") == "tool":
                return LLMResponse(text="done", tool_calls=[])
        # Otherwise pull the latest user text (a DNI) and call identificar_cliente.
        dni = ""
        for msg in messages:
            if msg.get("role") == "user":
                dni = msg.get("content", "")
        return LLMResponse(
            text="", tool_calls=[ToolCall(id="i1", name="identificar_cliente", input={"dni": dni})]
        )


def test_chat_dni_sweep_blocks_via_tool(monkeypatch):
    """Scanning distinct DNIs through the chat tool trips the sweep block.

    The block surfaces in the tool result (reason=rate_limited) — the gate
    rejects WITHOUT resolving, and the request itself stays 200 (the LLM
    narrates the neutral message). This proves the anti-enumeration hook fires
    on the LLM-driven identification path.
    """
    import api.main as m

    monkeypatch.setattr(m, "build_llm_provider", lambda *a, **k: _IdentSweepProvider())
    monkeypatch.setattr(m.rate_limiter.config, "distinct_dni_per_hour", 2)
    monkeypatch.setattr(m.rate_limiter.config, "ident_per_hour", 100)  # isolate diversity
    monkeypatch.setattr(m.rate_limiter.config, "chat_per_min", 100)
    m.store = m.get_store()
    client = TestClient(m.app)
    csrf, session = _tokens()
    headers = {"X-CSRF-Token": csrf, "X-Session-Token": session}

    def _try(dni: str):
        return client.post(
            "/api/v1/chat",
            json={"channel": "web", "tenant_id": "prestaunion", "text": dni},
            headers=headers,
        )

    # 2 distinct DNIs are allowed; the 3rd distinct one trips the block.
    assert _try("11111111").status_code == 200
    assert _try("22222222").status_code == 200
    # Block now active: a further identification attempt is denied by the limiter.
    decision = m.rate_limiter.check_identification("testclient", "33333333")
    assert decision.allowed is False
    assert decision.reason in ("dni_sweep_block", "temp_block")
