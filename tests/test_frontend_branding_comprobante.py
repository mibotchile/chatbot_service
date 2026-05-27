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
# Fixture borrower P02137 (al día). CCI is 100% clean in Doris/fixture.
LUIS_DNI = "10052986"
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
    m.store = m.get_store()
    return TestClient(m.app)


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


def test_branding_prestaunion_keeps_its_own_theme(client):
    b = client.get("/api/v1/tenant/prestaunion/branding").json()
    assert b["name"] == "PrestaUnion"
    assert b["primary_color"] == "#f5c518"  # NOT prestamype green
    assert b["footer"] == "Powered by Onbotgo"


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
    data.update({k: v for k, v in overrides.items() if k != "files"})
    files = overrides.get("files", _file())
    return client.post("/api/v1/comprobante", data=data, files=files)


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


def test_comprobante_wrong_cci_rejected(client):
    r = _post(client, cci="99999999999999999999", nro_operacion="OP-BAD")
    assert r.status_code == 200
    body = r.json()
    assert body["cuenta_valida"] is False
    assert "no corresponde" in body["mensaje"].lower()


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


def test_comprobante_rejects_bad_nro_operacion(client):
    r = _post(client, nro_operacion="../escape")
    assert r.status_code == 400


def test_comprobante_rejects_zero_monto(client):
    r = _post(client, monto="0", nro_operacion="OP-ZERO")
    assert r.status_code == 400
