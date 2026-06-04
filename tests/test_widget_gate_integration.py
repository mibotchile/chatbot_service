"""Integration tests for the publishable-key gate wired onto FastAPI routes (A-5).

Tests all 5 gated routes + allow_no_key bootstrap routes via TestClient.
Written RED-first before A-6 wiring.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ── Helpers ──────────────────────────────────────────────────────────────────

VALID_PK = "pk_live_testkey_prestaunion"
VALID_ORIGIN = "https://demos.mibot.cl"
BAD_ORIGIN = "https://attacker.example.com"

# A minimal valid CSRF token that passes _validate_csrf_token.
# We generate a real one rather than hardcoding so the test doesn't
# depend on the HMAC secret value.
def _make_csrf_token():
    from api.middleware import _generate_csrf_token
    return _generate_csrf_token()


def _make_session_token(visitor_id: str = "00000000-0000-0000-0000-000000000001"):
    from api.middleware import _generate_session_token
    return _generate_session_token(visitor_id)


@pytest.fixture
def patched_app(tmp_path, monkeypatch):
    """Patch tenants root so prestaunion has a publishable key."""
    import api.deps.widget_gate as wg

    # Mirror the real tenant config but add publishable_keys + embed_origins
    prestaunion = tmp_path / "prestaunion"
    prestaunion.mkdir()
    (prestaunion / "tenant.config.json").write_text(
        json.dumps({
            "id": "prestaunion",
            "publishable_keys": [
                {"key": VALID_PK, "status": "current", "added": "2026-01-01"}
            ],
            "embed_origins": [
                "https://demos.mibot.cl",
                "http://localhost:*",
            ],
        }),
        encoding="utf-8",
    )
    # Also create a minimal prestamype entry so collect_embed_origins doesn't break
    prestamype = tmp_path / "prestamype"
    prestamype.mkdir()
    (prestamype / "tenant.config.json").write_text(
        json.dumps({
            "id": "prestamype",
            "publishable_keys": [
                {"key": "pk_live_testkey_prestamype", "status": "current", "added": "2026-01-01"}
            ],
            "embed_origins": ["https://demos.mibot.cl", "http://localhost:*", "http://127.0.0.1:*"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(wg, "_tenants_root", lambda: tmp_path)
    # Also patch cors._tenants_root so CORS regex still works
    import shared.config.cors as cors_mod
    monkeypatch.setattr(cors_mod, "_tenants_root", lambda: tmp_path)

    import api.main as m
    m._request_log.clear()
    m.store = m.get_store()
    return TestClient(m.app, raise_server_exceptions=False)


# ── Gated routes: valid key + origin → must NOT 403 from gate ────────────────

class TestGatedRoutesValidKeyAndOrigin:
    """With valid pk + allowlisted origin, the gate passes (route may still
    fail for other reasons — session/CSRF/body — but NOT 403 from the gate)."""

    def test_post_chat_gate_passes_with_valid_key(self, patched_app):
        csrf = _make_csrf_token()
        session = _make_session_token()
        r = patched_app.post(
            "/api/v1/chat",
            headers={
                "X-Publishable-Key": VALID_PK,
                "Origin": VALID_ORIGIN,
                "X-CSRF-Token": csrf,
                "X-Session-Token": session,
            },
            json={"text": "hola", "channel": "web", "tenant_id": "prestaunion"},
        )
        # Gate passes; may get a 200 or other error but NOT 403 from missing key
        assert r.status_code != 403 or "publishable" not in r.text.lower()

    def test_post_conversations_messages_gate_passes(self, patched_app):
        csrf = _make_csrf_token()
        session = _make_session_token()
        r = patched_app.post(
            "/api/v1/conversations/messages",
            headers={
                "X-Publishable-Key": VALID_PK,
                "Origin": VALID_ORIGIN,
                "X-CSRF-Token": csrf,
                "X-Session-Token": session,
            },
            json={"text": "hola", "channel": "web"},
        )
        assert r.status_code != 403 or "publishable" not in r.text.lower()

    def test_post_page_context_gate_passes(self, patched_app):
        csrf = _make_csrf_token()
        r = patched_app.post(
            "/api/v1/page-context",
            headers={
                "X-Publishable-Key": VALID_PK,
                "Origin": VALID_ORIGIN,
                "X-CSRF-Token": csrf,
            },
            json={"entry_source": "direct"},
        )
        assert r.status_code != 403 or "publishable" not in r.text.lower()

    def test_post_comprobante_gate_passes(self, patched_app):
        """Gate passes; the route itself may reject for form-validation reasons."""
        import io
        csrf = _make_csrf_token()
        session = _make_session_token()
        r = patched_app.post(
            "/api/v1/comprobante",
            headers={
                "X-Publishable-Key": VALID_PK,
                "Origin": VALID_ORIGIN,
                "X-CSRF-Token": csrf,
                "X-Session-Token": session,
            },
            data={
                "tenant_id": "prestaunion",
                "dni": "12345678",
                "monto": "100.00",
                "nro_operacion": "OP001",
                "account_type": "cci",
                "cuenta_destino": "12345678901234567890",
            },
            files={"file": ("test.pdf", io.BytesIO(b"%PDF fake"), "application/pdf")},
        )
        # Gate passes; the route rejects for business reasons (not 403 from key)
        assert r.status_code != 403 or "publishable" not in r.text.lower()

    def test_get_conversation_messages_gate_passes(self, patched_app):
        conv_id = "00000000-0000-0000-0000-000000000099"
        r = patched_app.get(
            f"/api/v1/conversations/{conv_id}/messages",
            headers={
                "X-Publishable-Key": VALID_PK,
                "Origin": VALID_ORIGIN,
            },
        )
        assert r.status_code != 403 or "publishable" not in r.text.lower()


# ── Gated routes: missing key → 403 ──────────────────────────────────────────

class TestGatedRoutesMissingKey:
    def test_post_chat_missing_key_403(self, patched_app):
        r = patched_app.post(
            "/api/v1/chat",
            headers={"Origin": VALID_ORIGIN},
            json={"text": "hola", "channel": "web"},
        )
        assert r.status_code == 403

    def test_post_conversations_messages_missing_key_403(self, patched_app):
        r = patched_app.post(
            "/api/v1/conversations/messages",
            headers={"Origin": VALID_ORIGIN},
            json={"text": "hola"},
        )
        assert r.status_code == 403

    def test_post_page_context_missing_key_403(self, patched_app):
        r = patched_app.post(
            "/api/v1/page-context",
            headers={"Origin": VALID_ORIGIN},
            json={},
        )
        assert r.status_code == 403

    def test_get_conversation_messages_missing_key_403(self, patched_app):
        conv_id = "00000000-0000-0000-0000-000000000099"
        r = patched_app.get(
            f"/api/v1/conversations/{conv_id}/messages",
            headers={"Origin": VALID_ORIGIN},
        )
        assert r.status_code == 403


# ── Gated routes: bad origin → 403 ───────────────────────────────────────────

class TestGatedRoutesBadOrigin:
    def test_post_chat_bad_origin_403(self, patched_app):
        r = patched_app.post(
            "/api/v1/chat",
            headers={"X-Publishable-Key": VALID_PK, "Origin": BAD_ORIGIN},
            json={"text": "hola", "channel": "web"},
        )
        assert r.status_code == 403

    def test_post_page_context_bad_origin_403(self, patched_app):
        r = patched_app.post(
            "/api/v1/page-context",
            headers={"X-Publishable-Key": VALID_PK, "Origin": BAD_ORIGIN},
            json={},
        )
        assert r.status_code == 403


# ── Auth-composition invariant: gate passes → CSRF/session still enforced ────

class TestAuthCompositionInvariant:
    """WARNING-1 (verify-report): prove that CSRF/session enforcement survives
    the gate composition.  With a VALID pk + allowlisted origin the gate passes,
    but a request missing a session token must still be rejected by the
    CSRF/session layer — NOT allowed through, and NOT rejected by the gate."""

    def test_comprobante_gate_passes_but_session_required(self, patched_app):
        """Gate passes (valid pk + origin); no session token → 401 from
        CSRF/session layer, NOT 403 from the gate.

        Distinguishes the two layers:
          - Gate 403: body contains "publishable"
          - Session 401: body contains "session"
        """
        import io

        csrf = _make_csrf_token()
        # Deliberately omit X-Session-Token — gate headers are present and valid
        r = patched_app.post(
            "/api/v1/comprobante",
            headers={
                "X-Publishable-Key": VALID_PK,
                "Origin": VALID_ORIGIN,
                "X-CSRF-Token": csrf,
                # NO X-Session-Token
            },
            data={
                "tenant_id": "prestaunion",
                "dni": "12345678",
                "monto": "100.00",
                "nro_operacion": "OP-INVARIANT",
                "account_type": "cci",
                "cuenta_destino": "12345678901234567890",
            },
            files={"file": ("test.pdf", io.BytesIO(b"%PDF fake"), "application/pdf")},
        )
        # Must be rejected — something must be 4xx
        assert r.status_code in (401, 403), (
            f"Expected 401 or 403 but got {r.status_code}"
        )
        # Must NOT be a gate rejection (gate detail contains "publishable")
        assert "publishable" not in r.text.lower(), (
            "Gate rejected the request — session/CSRF layer was never reached. "
            f"Response: {r.status_code} {r.text}"
        )
        # Must be a session/CSRF rejection specifically
        assert "session" in r.text.lower() or "csrf" in r.text.lower(), (
            "Expected session or CSRF error detail but got: "
            f"{r.status_code} {r.text}"
        )


# ── allow_no_key bootstrap routes: no key required ───────────────────────────

class TestAllowNoKeyRoutes:
    def test_branding_no_key_allowed(self, patched_app):
        r = patched_app.get("/api/v1/tenant/prestaunion/branding")
        # No key needed — must not 403 with "publishable" message
        assert r.status_code != 403 or "publishable" not in r.text.lower()

    def test_csrf_token_no_key_allowed(self, patched_app):
        r = patched_app.get("/api/v1/security/csrf-token")
        assert r.status_code != 403 or "publishable" not in r.text.lower()

    def test_session_token_no_key_allowed(self, patched_app):
        r = patched_app.get("/api/v1/security/session-token")
        assert r.status_code != 403 or "publishable" not in r.text.lower()
