"""Tests for the tenant branding endpoint + the comprobante upload endpoint.

Both run against the SEEDED FIXTURE (no live Doris, no LLM): the doris source
falls back to the fixture, which is exactly the path exercised in CI. The
comprobante upload stores images to a tmp dir and dedups via a tmp JSON store.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

TENANT = "prestamype"
# Fixture borrower P02137 (al día). Synthetic demo DNI.
LUIS_DNI = "44218903"
LUIS_CCI = "00389801338381007048"
LUIS_CUOTA = 462.14
LUIS_SALDO = 23800.00


@pytest.fixture
def client(monkeypatch, tmp_path):
    import api.main as m
    import tools.cobranza as cobranza

    # Isolate image storage + dedup store per test (off /tmp, off real volume).
    monkeypatch.setattr(m.settings, "comprobante_dir", str(tmp_path / "comprobantes"))
    monkeypatch.setattr(cobranza, "_COMPROBANTES_PATH", tmp_path / "comprobantes.json")
    # Reset the in-memory rate-limit log so the shared 10/min window doesn't
    # bleed across tests (the limiter now also covers /comprobante).
    m._request_log.clear()
    m.store = m.get_store()
    return TestClient(m.app)


def _security_headers():
    """Valid session + CSRF tokens, same gate as /chat (HIGH-02)."""
    import api.main as m

    return {
        "X-Session-Token": m._generate_session_token("test-visitor"),
        "X-CSRF-Token": m._generate_csrf_token(),
    }


_PNG = b"\x89PNG\r\n\x1a\n_fake_image_bytes"


def _file(name: str = "comprobante.png", content_type: str = "image/png", data: bytes = _PNG):
    return {"file": (name, io.BytesIO(data), content_type)}


# ── Branding endpoint ──────────────────────────────────────────────────────

def test_branding_prestamype_green_and_logo(client):
    r = client.get(f"/api/v1/tenant/{TENANT}/branding")
    assert r.status_code == 200
    b = r.json()
    assert b["primary_color"] == "#00b369"
    assert b["logo_url"] == "https://d14bodb4yrsx8y.cloudfront.net/static/logo.svg"
    assert b["name"] == "PrestamYpe"
    assert b["footer"] == "Powered by Onbotgo"
    # demo cards come from the fixture tokens (demo-1/2/3).
    tokens = {c["token"] for c in b["demo_tokens"]}
    assert tokens == {"demo-1", "demo-2", "demo-3"}


def test_branding_demo_tokens_have_truthful_labels(client):
    b = client.get(f"/api/v1/tenant/{TENANT}/branding").json()
    by_token = {c["token"]: c for c in b["demo_tokens"]}
    # demo-2 → P03650 → en mora, USD → "Crédito en dólares"
    assert by_token["demo-2"]["status"] == "en_mora"
    assert by_token["demo-2"]["currency"] == "USD"
    assert "dólar" in by_token["demo-2"]["label"].lower()
    # demo-1 → P02137 → al día
    assert by_token["demo-1"]["status"] == "al_dia"


def test_branding_prestamype_hides_demo_cards(client):
    # prestamype relies on DNI-first identification in the chat: the landing
    # "Ingresa como uno de estos clientes" cards are hidden.
    b = client.get(f"/api/v1/tenant/{TENANT}/branding").json()
    assert b["show_demo_cards"] is False


def test_branding_demo_tokens_carry_no_pii(client):
    # CRIT-01: the public /branding endpoint must NEVER expose borrower PII.
    b = client.get(f"/api/v1/tenant/{TENANT}/branding").json()
    for t in b["demo_tokens"]:
        for pii_field in ("dni", "name", "borrower_name", "email", "phone"):
            assert pii_field not in t, f"PII field {pii_field!r} leaked in /branding"
        # only safe presentational fields are allowed
        assert set(t).issubset({"token", "label", "status", "status_label", "currency"})


def test_branding_prestaunion_keeps_its_own_theme(client):
    b = client.get("/api/v1/tenant/prestaunion/branding").json()
    assert b["name"] == "PrestaUnion"
    assert b["primary_color"] == "#f5c518"  # NOT prestamype green
    assert b["footer"] == "Powered by Onbotgo"
    # prestaunion keeps its demo cards (default = shown).
    assert b["show_demo_cards"] is True


def test_branding_unknown_tenant_404(client):
    assert client.get("/api/v1/tenant/nope/branding").status_code == 404


def test_branding_rejects_path_traversal(client):
    # ".." can't escape the tenants dir (sanitized slug).
    assert client.get("/api/v1/tenant/..%2f..%2fetc/branding").status_code in (404, 400)


# ── Comprobante upload endpoint ─────────────────────────────────────────────

def _post(client, **overrides):
    data = {
        "tenant_id": TENANT,
        "dni": LUIS_DNI,
        "cci": LUIS_CCI,
        "monto": str(LUIS_CUOTA),
        "nro_operacion": "OP-001",
    }
    data.update({k: v for k, v in overrides.items() if k not in ("files", "headers")})
    files = overrides.get("files", _file())
    headers = overrides.get("headers", _security_headers())
    return client.post("/api/v1/comprobante", data=data, files=files, headers=headers)


def test_comprobante_pago_classified_and_stored(client, tmp_path):
    r = _post(client, monto=str(LUIS_CUOTA), nro_operacion="OP-PAGO")
    assert r.status_code == 200
    body = r.json()
    assert body["cuenta_valida"] is True
    assert body["credito"] == "P02137"
    assert body["tipo"] == "pago"
    assert body["dedup_ok"] is True
    # image persisted under <dir>/<dni>/<op>.<ext>
    stored = tmp_path / "comprobantes" / LUIS_DNI / "OP-PAGO.png"
    assert stored.exists()


def test_comprobante_abono_and_cancelacion(client):
    abono = _post(client, monto="100.00", nro_operacion="OP-AB").json()
    assert abono["tipo"] == "abono"
    canc = _post(client, monto=str(LUIS_SALDO), nro_operacion="OP-CN").json()
    assert canc["tipo"] == "cancelacion"


def test_comprobante_arbitrary_cci_accepted(client):
    # CCI pertenencia is no longer validated: any CCI is accepted and stored
    # as-is; the voucher is classified against the DNI's credit.
    r = _post(client, cci="00000000000000000000", nro_operacion="OP-ANYCCI")
    assert r.status_code == 200
    body = r.json()
    assert body["cuenta_valida"] is True
    assert body["credito"] == "P02137"
    assert body["tipo"] == "pago"
    assert body["dedup_ok"] is True
    assert "no corresponde" not in body["mensaje"].lower()


def test_comprobante_duplicate_detected(client):
    first = _post(client, nro_operacion="OP-DUP").json()
    assert first["dedup_ok"] is True
    second = _post(client, nro_operacion="OP-DUP").json()
    assert second["dedup_ok"] is False
    assert "duplicad" in second["mensaje"].lower() or "ya lo recibimos" in second["mensaje"].lower()


def test_comprobante_unknown_dni_404(client):
    r = _post(client, dni="00000000", nro_operacion="OP-NODNI")
    assert r.status_code == 404


def test_comprobante_rejects_bad_filetype(client):
    bad = {"file": ("evil.exe", io.BytesIO(b"MZ..."), "application/octet-stream")}
    r = _post(client, files=bad)
    assert r.status_code == 400


def test_comprobante_requires_session_and_csrf(client):
    # HIGH-02: no session/CSRF token → rejected (401 session checked first).
    r = _post(client, nro_operacion="OP-NOAUTH", headers={})
    assert r.status_code == 401


def test_comprobante_rejects_html_disguised_as_png(client):
    # HIGH-02: content-type says image/png but the bytes are HTML → magic-byte
    # sniff rejects it (400).
    html = {"file": ("x.png", io.BytesIO(b"<html><script>alert(1)</script></html>"), "image/png")}
    r = _post(client, files=html, nro_operacion="OP-HTML")
    assert r.status_code == 400


def test_comprobante_accepts_real_pdf(client):
    pdf = {"file": ("c.pdf", io.BytesIO(b"%PDF-1.4\n%fake pdf body"), "application/pdf")}
    r = _post(client, files=pdf, nro_operacion="OP-PDF")
    assert r.status_code == 200
    assert r.json()["cuenta_valida"] is True


def test_comprobante_rejects_bad_nro_operacion(client):
    r = _post(client, nro_operacion="../escape")
    assert r.status_code == 400


def test_comprobante_rejects_zero_monto(client):
    r = _post(client, monto="0", nro_operacion="OP-ZERO")
    assert r.status_code == 400


# ── Rate limiting at the upload endpoint (429 + Retry-After) ────────────────

def test_comprobante_upload_per_hour_429(client, monkeypatch):
    """Past the upload/hour cap the endpoint returns 429 with Retry-After."""
    import api.main as m

    # Squeeze the cap to 2 so the test is short (uses the real wiring).
    monkeypatch.setattr(m.rate_limiter.config, "upload_per_hour", 2)
    assert _post(client, nro_operacion="OP-RL1").status_code == 200
    assert _post(client, nro_operacion="OP-RL2").status_code == 200
    r = _post(client, nro_operacion="OP-RL3")
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1
    # The neutral message must not leak the internal limit name.
    assert "upload_per_hour" not in r.text


def test_comprobante_dni_sweep_blocks_429(client, monkeypatch):
    """Scanning many distinct DNIs at the upload endpoint trips the sweep block."""
    import api.main as m

    monkeypatch.setattr(m.rate_limiter.config, "distinct_dni_per_hour", 2)
    monkeypatch.setattr(m.rate_limiter.config, "ident_per_hour", 100)  # isolate diversity
    monkeypatch.setattr(m.rate_limiter.config, "upload_per_hour", 100)  # isolate diversity
    # 3 distinct DNIs (all unknown → 404 normally) → the 3rd trips the block.
    _post(client, dni="11111111", nro_operacion="OP-S1")
    _post(client, dni="22222222", nro_operacion="OP-S2")
    r = _post(client, dni="33333333", nro_operacion="OP-S3")
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1
