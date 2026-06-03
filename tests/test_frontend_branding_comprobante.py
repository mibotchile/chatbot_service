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
LUIS_SALDO = 18420.00


@pytest.fixture
def client(monkeypatch, tmp_path):
    import api.main as m
    import features.comprobantes.validator as _validator

    # Isolate image storage + dedup store per test (off /tmp, off real volume).
    monkeypatch.setattr(m.settings, "comprobante_dir", str(tmp_path / "comprobantes"))
    monkeypatch.setattr(_validator, "_COMPROBANTES_PATH", tmp_path / "comprobantes.json")
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
    # 5 demo cards come from the fixture tokens (demo-1..5), one per casuística.
    tokens = {c["token"] for c in b["demo_tokens"]}
    assert tokens == {"demo-1", "demo-2", "demo-3", "demo-4", "demo-5"}


def test_branding_prestamype_minimalist_content_stays_empty(client):
    # PrestamYpe opts OUT of the rich landing: it omits kicker/hero_subline/
    # features in its config so those blocks render empty/hidden. This guards
    # that the data-driven refactor did NOT inject prestaunion-style content
    # into prestamype (its minimalist green look must be unchanged).
    b = client.get(f"/api/v1/tenant/{TENANT}/branding").json()
    assert b["kicker"] == ""
    assert b["hero_subline"] == ""
    assert b["features"] == []


def test_branding_demo_tokens_have_truthful_labels(client):
    b = client.get(f"/api/v1/tenant/{TENANT}/branding").json()
    by_token = {c["token"]: c for c in b["demo_tokens"]}
    # demo-1 → P02137 → al día
    assert by_token["demo-1"]["status"] == "al_dia"
    # demo-3 → P03650 → en mora, USD → "Crédito en dólares"
    assert by_token["demo-3"]["status"] == "en_mora"
    assert by_token["demo-3"]["currency"] == "USD"
    assert "dólar" in by_token["demo-3"]["label"].lower()
    # demo-4 → P05012 → multi-crédito (mismo DNI, varios créditos)
    assert "más de una deuda" in by_token["demo-4"]["label"].lower()
    # demo-5 → P05480 → crédito grupal con codeudores
    assert "grupal" in by_token["demo-5"]["label"].lower()


def test_branding_prestamype_shows_demo_cards(client):
    # The fixture is fully synthetic (no PII), so the 5 casuística cards are
    # shown on the public landing again.
    b = client.get(f"/api/v1/tenant/{TENANT}/branding").json()
    assert b["show_demo_cards"] is True


def test_branding_demo_cases_table_has_5_synthetic_rows(client):
    # DNI-first table: name + synthetic DNI + casuística per test case. The user
    # types one of these DNIs in the chat to identify (no magic links).
    b = client.get(f"/api/v1/tenant/{TENANT}/branding").json()
    cases = b["demo_cases"]
    assert len(cases) == 5
    for c in cases:
        assert set(c) == {"name", "dni", "casuistica", "status", "status_label", "currency"}
        assert len(c["dni"]) == 8 and c["dni"].isdigit()  # synthetic 8-digit DNI
        assert c["name"]  # title-cased fictitious name shown in the table
    casuisticas = {c["casuistica"] for c in cases}
    assert "Más de una deuda" in casuisticas         # multi-crédito (mismo DNI)
    assert "Crédito grupal (codeudores)" in casuisticas  # multi-deudor (grupal)
    assert any("dólar" in c.lower() for c in casuisticas)


def test_branding_demo_tokens_carry_no_pii(client):
    # CRIT-01: the public /branding endpoint must NEVER expose borrower PII.
    b = client.get(f"/api/v1/tenant/{TENANT}/branding").json()
    for t in b["demo_tokens"]:
        for pii_field in ("dni", "name", "borrower_name", "email", "phone"):
            assert pii_field not in t, f"PII field {pii_field!r} leaked in /branding"
        # only safe presentational fields are allowed
        assert set(t).issubset({"token", "label", "status", "status_label", "currency"})


def test_branding_prestaunion_is_data_driven(client):
    # After the refactor, prestaunion is NO LONGER the hardcoded default: its
    # look (Vox blue, name, hero, features) is served data-driven from config,
    # exactly like every other tenant. primary_color is the REAL rendered blue
    # (#0083E0) — previously the config carried an unused #f5c518 that never
    # painted because the HTML was hardcoded prestaunion.
    b = client.get("/api/v1/tenant/prestaunion/branding").json()
    assert b["name"] == "PrestaUnion"
    assert b["primary_color"] == "#0083E0"  # Vox blue, NOT prestamype green
    assert b["footer"] == "Powered by Onbotgo"
    # prestaunion keeps its demo cards (default = shown).
    assert b["show_demo_cards"] is True
    # Landing content is now data-driven (not baked in the HTML).
    assert b["hero_headline"]
    assert b["hero_subline"]
    assert isinstance(b["features"], list) and len(b["features"]) == 3


def test_branding_unknown_tenant_404(client):
    assert client.get("/api/v1/tenant/nope/branding").status_code == 404


def test_branding_rejects_path_traversal(client):
    # ".." can't escape the tenants dir (sanitized slug).
    assert client.get("/api/v1/tenant/..%2f..%2fetc/branding").status_code in (404, 400)


# ── Comprobante upload endpoint ─────────────────────────────────────────────

def _post(client, **overrides):
    # Default uses the legacy ``cci`` field (account_type defaults to "cci")
    # to confirm backward compat still works.
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


# ── account_type: número de cuenta (corto) vs CCI (20 dígitos) ──────────────

def test_comprobante_account_type_cuenta_short_accepted(client):
    # Jorge feedback: con account_type=cuenta se acepta un número corto, se
    # clasifica por MONTO y se deduplica por nº de operación. No se fuerza CCI.
    data = {"account_type": "cuenta", "cuenta_destino": "1320268376", "cci": ""}
    r = _post(client, nro_operacion="OP-CUENTA", **data)
    assert r.status_code == 200
    body = r.json()
    assert body["cuenta_valida"] is True       # NO valida contra Doris
    assert body["account_type"] == "cuenta"
    assert body["cuenta_destino"] == "1320268376"
    assert body["credito"] == "P02137"
    assert body["tipo"] == "pago"              # por monto
    assert body["dedup_ok"] is True


def test_comprobante_account_type_cci_requires_20_digits(client):
    # account_type=cci con menos de 20 dígitos → 400 (validación de FORMATO).
    data = {"account_type": "cci", "cuenta_destino": "1320268376", "cci": ""}
    r = _post(client, nro_operacion="OP-CCISHORT", **data)
    assert r.status_code == 400


def test_comprobante_account_type_cuenta_rejects_too_short(client):
    # account_type=cuenta con menos de 8 dígitos → 400 (formato).
    data = {"account_type": "cuenta", "cuenta_destino": "123", "cci": ""}
    r = _post(client, nro_operacion="OP-TOOSHORT", **data)
    assert r.status_code == 400


def test_comprobante_account_type_cuenta_classifies_by_monto(client):
    # La clasificación NO depende de la cuenta: mismo número de cuenta corta,
    # distintos montos → pago / abono / cancelacion.
    base = {"account_type": "cuenta", "cuenta_destino": "1320268376", "cci": ""}
    abono = _post(client, monto="100.00", nro_operacion="OP-CTA-AB", **base).json()
    assert abono["tipo"] == "abono"
    canc = _post(client, monto=str(LUIS_SALDO), nro_operacion="OP-CTA-CN", **base).json()
    assert canc["tipo"] == "cancelacion"


def test_comprobante_dedup_by_nro_operacion_independent_of_account(client):
    base = {"account_type": "cuenta", "cuenta_destino": "1320268376", "cci": ""}
    first = _post(client, nro_operacion="OP-CTA-DUP", **base).json()
    assert first["dedup_ok"] is True
    # mismo nº de operación con CCI distinto → igual duplicado
    cci_args = {"account_type": "cci", "cuenta_destino": LUIS_CCI, "cci": ""}
    second = _post(client, nro_operacion="OP-CTA-DUP", **cci_args).json()
    assert second["dedup_ok"] is False


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
