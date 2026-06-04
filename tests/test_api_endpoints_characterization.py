"""Characterization tests for api/main.py endpoints not covered elsewhere.

These tests establish behavioral contracts for the seams being extracted in PR6
(api/main.py split). They exercise real request→response behavior — NOT source
inspection. If main.py is split correctly and behavior is preserved, every
assertion here must keep passing.

Covered seams:
- GET /api/v1/security/csrf-token
- GET /api/v1/security/session-token
- GET /api/v1/cobranza/certificate/{filename}
- GET /api/v1/cobranza/reclamos
- POST /api/v1/webhooks/whatsapp
- GET /api/v1/conversations/{conversation_id}/messages
- POST /api/v1/page-context
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    import api.main as m

    m._request_log.clear()
    m.store = m.get_store()
    return TestClient(m.app)


def _csrf_token(client: TestClient) -> str:
    """Obtain a fresh CSRF token via the endpoint."""
    r = client.get("/api/v1/security/csrf-token")
    assert r.status_code == 200
    return r.headers["X-CSRF-Token"]


def _session_token(visitor_id: str = "test-visitor") -> str:
    import api.main as m
    return m._generate_session_token(visitor_id)


_GATE_PK = "pk_live_PLACEHOLDER_PRESTAUNION"
_GATE_ORIGIN = "https://demos.mibot.cl"


def _gate_headers() -> dict:
    """Publishable-key + origin required by gated routes."""
    return {"X-Publishable-Key": _GATE_PK, "Origin": _GATE_ORIGIN}


def _security_headers(visitor_id: str = "test-visitor") -> dict:
    import api.main as m
    return {
        "X-Session-Token": m._generate_session_token(visitor_id),
        "X-CSRF-Token": m._generate_csrf_token(),
    }


# ── Security: CSRF token ─────────────────────────────────────────────────────


def test_csrf_token_returns_200_and_header(client):
    r = client.get("/api/v1/security/csrf-token")
    assert r.status_code == 200
    assert "X-CSRF-Token" in r.headers
    assert r.json() == {"status": "ok"}


def test_csrf_token_is_string_of_reasonable_length(client):
    r = client.get("/api/v1/security/csrf-token")
    token = r.headers["X-CSRF-Token"]
    # timestamp_hex format: digits + underscore + sha256 hex (64 chars)
    assert len(token) > 10
    assert "_" in token


def test_csrf_token_sets_cookie(client):
    r = client.get("/api/v1/security/csrf-token")
    # Cookie should be set (httponly + samesite=lax + secure)
    assert "csrf_token" in r.cookies


# ── Security: session token ──────────────────────────────────────────────────


def test_session_token_returns_200_and_token(client):
    r = client.get("/api/v1/security/session-token?visitor_id=abc123")
    assert r.status_code == 200
    data = r.json()
    assert "token" in data
    assert "expires_in" in data
    assert isinstance(data["expires_in"], int)
    assert data["expires_in"] > 0


def test_session_token_anonymous_visitor_works(client):
    r = client.get("/api/v1/security/session-token")
    assert r.status_code == 200
    assert "token" in r.json()


def test_session_token_is_verifiable(client):
    """Token issued by the endpoint must be accepted by _verify_session_token."""
    import api.main as m

    r = client.get("/api/v1/security/session-token?visitor_id=check-me")
    token = r.json()["token"]
    valid, _ = m._verify_session_token(token)
    assert valid is True


# ── Certificate download ─────────────────────────────────────────────────────


def test_certificate_download_missing_returns_404(tmp_path, client):
    r = client.get("/api/v1/cobranza/certificate/nonexistent_cert.pdf")
    assert r.status_code == 404


def test_certificate_download_invalid_filename_returns_400(client):
    # Path traversal or invalid pattern must be rejected
    r = client.get("/api/v1/cobranza/certificate/../etc/passwd")
    # FastAPI may return 404 due to path encoding, but never 200
    assert r.status_code in (400, 404, 422)


def test_certificate_download_bad_extension_returns_400(client):
    r = client.get("/api/v1/cobranza/certificate/malicious.sh")
    assert r.status_code == 400


def test_certificate_download_serves_existing_file(tmp_path, monkeypatch):
    """A valid PDF placed at the expected path is served correctly."""
    import api.main as m

    cert_dir = tmp_path / "prestaunion_certificates"
    cert_dir.mkdir()
    fake_pdf = cert_dir / "test_cert.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake content")

    import api.main as m_module
    monkeypatch.setattr(m_module.settings, "comprobante_dir", str(tmp_path))

    from unittest.mock import patch
    with patch("pathlib.Path") as mock_path_cls:
        # Only patch the certificate path lookup inside the endpoint
        pass

    # Simpler: write the file to the actual /tmp path used by the endpoint
    import pathlib
    actual_dir = pathlib.Path("/tmp/prestaunion_certificates")
    actual_dir.mkdir(exist_ok=True)
    actual_cert = actual_dir / "characterization_test.pdf"
    actual_cert.write_bytes(b"%PDF-1.4 characterization test")

    m._request_log.clear()
    m.store = m.get_store()
    test_client = TestClient(m.app)

    try:
        r = test_client.get("/api/v1/cobranza/certificate/characterization_test.pdf")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
    finally:
        actual_cert.unlink(missing_ok=True)


# ── Reclamos ─────────────────────────────────────────────────────────────────


def test_reclamos_returns_empty_list_when_no_file(client, tmp_path, monkeypatch):
    """When /tmp/prestaunion_reclamos.json does not exist, returns empty list."""
    import pathlib
    from unittest.mock import patch

    # Ensure the file doesn't exist for this test
    reclamos_path = pathlib.Path("/tmp/prestaunion_reclamos.json")
    existed = reclamos_path.exists()
    if existed:
        content = reclamos_path.read_text()
        reclamos_path.unlink()

    try:
        r = client.get("/api/v1/cobranza/reclamos")
        assert r.status_code == 200
        data = r.json()
        assert "reclamos" in data
        assert isinstance(data["reclamos"], list)
    finally:
        if existed:
            reclamos_path.write_text(content)


def test_reclamos_returns_list_when_file_exists(client, tmp_path):
    """When reclamos.json exists with valid content, it's returned."""
    import pathlib

    reclamos_path = pathlib.Path("/tmp/prestaunion_reclamos.json")
    sample = [{"id": "r001", "tipo": "pago_no_acreditado", "desc": "test"}]
    reclamos_path.write_text(json.dumps(sample), encoding="utf-8")

    try:
        r = client.get("/api/v1/cobranza/reclamos")
        assert r.status_code == 200
        data = r.json()
        assert len(data["reclamos"]) == 1
        assert data["reclamos"][0]["id"] == "r001"
    finally:
        reclamos_path.unlink(missing_ok=True)


# ── Conversations: get messages ──────────────────────────────────────────────


def test_get_conversation_messages_invalid_id_returns_400(client):
    r = client.get("/api/v1/conversations/not-a-uuid/messages", headers=_gate_headers())
    assert r.status_code == 400


def test_get_conversation_messages_empty_conversation(client):
    """A fresh UUID returns empty messages list (no history)."""
    cid = str(uuid.uuid4())
    r = client.get(f"/api/v1/conversations/{cid}/messages", headers=_gate_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["messages"] == []
    assert "page_context" in data
    assert "debtor_status" in data


# ── Page context ─────────────────────────────────────────────────────────────


def test_page_context_requires_csrf(client):
    """Without CSRF token, page-context must be rejected with 403."""
    r = client.post(
        "/api/v1/page-context",
        json={"project_slug": None, "entry_source": "direct"},
    )
    assert r.status_code == 403


def test_page_context_returns_initial_message(client):
    """With a valid CSRF token, page-context returns an initial greeting."""
    import api.main as m

    csrf = m._generate_csrf_token()
    r = client.post(
        "/api/v1/page-context",
        json={"project_slug": "test", "entry_source": "direct"},
        headers={"X-CSRF-Token": csrf, **_gate_headers()},
    )
    assert r.status_code == 200
    data = r.json()
    assert "initial_message" in data
    assert "conversation_metadata" in data
    assert isinstance(data["initial_message"], str)
    assert len(data["initial_message"]) > 0


def test_page_context_echoes_metadata(client):
    """conversation_metadata in response echoes slug + entry_source."""
    import api.main as m

    csrf = m._generate_csrf_token()
    r = client.post(
        "/api/v1/page-context",
        json={"project_slug": "slug123", "entry_source": "referral"},
        headers={"X-CSRF-Token": csrf, **_gate_headers()},
    )
    assert r.status_code == 200
    meta = r.json()["conversation_metadata"]
    assert meta["project_slug"] == "slug123"
    assert meta["entry_source"] == "referral"


# ── WhatsApp webhook ─────────────────────────────────────────────────────────


def test_whatsapp_webhook_ignores_non_upsert_event(client):
    r = client.post(
        "/api/v1/webhooks/whatsapp",
        json={"event": "connection.update", "instance": "test", "data": {}},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ignored"
    assert "event=" in data["reason"]


# ── Dashboard: app.state.visitor_memory wiring ───────────────────────────────


def test_dashboard_leads_reads_app_state_visitor_memory(monkeypatch):
    """Dashboard /leads reads visitor_memory from app.state (not a module global).

    This test locks the behavioral contract established in PR7: the lifespan
    assigns ``app.state.visitor_memory`` and ``_get_pool`` reads it from
    ``request.app.state``. If the wiring breaks (e.g. _get_pool reverts to a
    module-level import), this test surfaces a 500 instead of 503.

    Scenario: app.state.visitor_memory is set to a stub with _pool=None →
    _get_pool raises HTTPException(503) → response is 503, not 500 (AttributeError).
    """
    import api.main as m

    class _FakeVM:
        _pool = None

    # Wire a fake VisitorMemory onto app.state (simulates a successful lifespan).
    m.app.state.visitor_memory = _FakeVM()

    # Activate dashboard key so auth passes.
    monkeypatch.setattr(m.settings, "dashboard_key", "test-key-pr8")

    try:
        with TestClient(m.app, raise_server_exceptions=False) as c:
            r = c.get(
                "/api/v1/dashboard/leads",
                headers={"X-Dashboard-Key": "test-key-pr8"},
            )
        # _get_pool finds _pool=None → 503 "Database not available"
        # (NOT a 500 AttributeError — the wiring to app.state is working).
        assert r.status_code == 503
        assert "Database not available" in r.json().get("detail", "")
    finally:
        # Clean up app.state so other tests are not affected.
        m.app.state.visitor_memory = None


def test_whatsapp_webhook_ignores_outgoing_messages(client):
    r = client.post(
        "/api/v1/webhooks/whatsapp",
        json={
            "event": "messages.upsert",
            "instance": "test",
            "data": {
                "key": {"fromMe": True, "remoteJid": "5191234567@s.whatsapp.net"},
                "messageType": "conversation",
                "message": {"conversation": "hello"},
            },
        },
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"
    assert r.json()["reason"] == "fromMe"


def test_whatsapp_webhook_ignores_group_messages(client):
    r = client.post(
        "/api/v1/webhooks/whatsapp",
        json={
            "event": "messages.upsert",
            "instance": "test",
            "data": {
                "key": {"fromMe": False, "remoteJid": "123456789@g.us"},
                "messageType": "conversation",
                "message": {"conversation": "hello group"},
            },
        },
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"
    assert r.json()["reason"] == "group"


def test_whatsapp_webhook_ignores_invalid_json(client):
    r = client.post(
        "/api/v1/webhooks/whatsapp",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"
    assert r.json()["reason"] == "invalid json"


def test_whatsapp_webhook_unmapped_instance_ignored(client):
    """An instance not in whatsapp_tenants mapping is filtered."""
    r = client.post(
        "/api/v1/webhooks/whatsapp",
        json={
            "event": "messages.upsert",
            "instance": "unknown-instance-xyz",
            "data": {
                "key": {"fromMe": False, "remoteJid": "5191234567@s.whatsapp.net"},
                "pushName": "Test User",
                "messageType": "conversation",
                "message": {"conversation": "hello"},
            },
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ignored"
    assert "unmapped instance" in data["reason"]
