"""Tests for the PrestamYpe tenant: fixture + Doris-source fallback + comprobantes.

These tests run against the SEEDED FIXTURE (real sample from Doris), never the
live Doris instance — the doris_debt_source falls back to the fixture when Doris
is unreachable, which is exactly the path exercised here (no Doris in CI).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integrations import debt_source, doris_debt_source
from tools import ToolRegistry

TENANT = "prestamype"

# Fixture borrowers (real sample). DNIs are 8-digit.
LUIS = "10052986"   # P02137, al día, cuota 462.14, saldo 23800, CCI 00389801338381007048
SILVIA = "72884356"  # P03250, al día, cuota 856.30, saldo 34237.50
ELMER = "04065803"  # P03650, en mora, USD, cuota 1397.71, saldo 96250


def _fixture() -> dict:
    root = Path(__file__).resolve().parent.parent / "tenants" / TENANT / "mock" / "borrowers.json"
    return json.loads(root.read_text(encoding="utf-8"))


# ── Fixture loads ─────────────────────────────────────────────────────────

def test_fixture_loads_with_8_borrowers_and_tokens():
    data = _fixture()
    assert len(data["borrowers"]) == 8
    assert set(data["tokens"]) == {"demo-1", "demo-2", "demo-3"}
    # Every borrower has the standard + prestamype extra fields.
    for prof in data["borrowers"].values():
        for field in ("account_id", "dni", "balance", "status", "days_overdue"):
            assert field in prof
        for extra in ("cci", "banco", "inversionista", "cuota_esperada", "saldo_por_cancelar"):
            assert extra in prof


def test_demo_tokens_point_at_distinct_credits():
    data = _fixture()
    accounts = [data["tokens"][t] for t in ("demo-1", "demo-2", "demo-3")]
    assert accounts == ["P02137", "P03650", "P04605"]
    assert len(set(accounts)) == 3


# ── resolve_dni / resolve_token via the dispatcher (fixture fallback) ──────

def test_resolve_dni_fixture_fallback():
    # No live Doris in tests → falls back to the seeded fixture.
    prof = debt_source.resolve_dni(LUIS, tenant_id=TENANT)
    assert prof is not None
    assert prof["account_id"] == "P02137"
    assert prof["status"] == "al_dia"
    assert prof["cci"] == "00389801338381007048"
    assert prof["cuota_esperada"] == 462.14


def test_resolve_token_fixture_fallback():
    prof = debt_source.resolve_token("demo-2", tenant_id=TENANT)
    assert prof is not None
    assert prof["account_id"] == "P03650"
    assert prof["currency"] == "USD"
    assert prof["status"] == "en_mora"


def test_resolve_dni_unknown_returns_none():
    assert debt_source.resolve_dni("00000000", tenant_id=TENANT) is None


def test_prestaunion_still_uses_mock():
    # Backward-compat: prestaunion has no data_source → mock backend.
    prof = debt_source.resolve_token("demo-juan", tenant_id="prestaunion")
    assert prof is not None
    assert prof["account_id"] == "ACC-PYPE-2024-00123"


# ── classify_tipo unit ─────────────────────────────────────────────────────

def test_classify_tipo_pago_abono_cancelacion():
    cuota, saldo = 462.14, 23800.0
    assert doris_debt_source.classify_tipo(462.14, cuota, saldo) == "pago"
    assert doris_debt_source.classify_tipo(460.0, cuota, saldo) == "pago"  # within 2%
    assert doris_debt_source.classify_tipo(200.0, cuota, saldo) == "abono"
    assert doris_debt_source.classify_tipo(23800.0, cuota, saldo) == "cancelacion"
    assert doris_debt_source.classify_tipo(23700.0, cuota, saldo) == "cancelacion"  # within 2%


# ── identity gate covers validar_comprobante ───────────────────────────────

async def test_gate_blocks_validar_comprobante_without_identity():
    reg = ToolRegistry(identity_verified=False, debt_context={}, tenant_id=TENANT)
    r = await reg.execute(
        "validar_comprobante",
        {"cci": "00389801338381007048", "monto": 462.14, "nro_operacion": "OP-X"},
    )
    assert r.get("blocked") == "identity_required"


# ── validar_comprobante logic (via the gated tool) ─────────────────────────

def _luis_profile() -> dict:
    return debt_source.resolve_dni(LUIS, tenant_id=TENANT)


async def test_validar_comprobante_pago(tmp_path, monkeypatch):
    _isolate_dedup(monkeypatch, tmp_path)
    reg = ToolRegistry(identity_verified=True, debt_context=_luis_profile(), tenant_id=TENANT)
    r = await reg.execute(
        "validar_comprobante",
        {"cci": "00389801338381007048", "monto": 462.14, "nro_operacion": "OP-001"},
    )
    assert r["cuenta_valida"] is True
    assert r["credito"] == "P02137"
    assert r["tipo"] == "pago"
    assert r["dedup_ok"] is True


async def test_validar_comprobante_abono(tmp_path, monkeypatch):
    _isolate_dedup(monkeypatch, tmp_path)
    reg = ToolRegistry(identity_verified=True, debt_context=_luis_profile(), tenant_id=TENANT)
    r = await reg.execute(
        "validar_comprobante",
        {"cci": "00389801338381007048", "monto": 100.00, "nro_operacion": "OP-002"},
    )
    assert r["cuenta_valida"] is True
    assert r["tipo"] == "abono"


async def test_validar_comprobante_cancelacion(tmp_path, monkeypatch):
    _isolate_dedup(monkeypatch, tmp_path)
    reg = ToolRegistry(identity_verified=True, debt_context=_luis_profile(), tenant_id=TENANT)
    r = await reg.execute(
        "validar_comprobante",
        {"cci": "00389801338381007048", "monto": 23800.00, "nro_operacion": "OP-003"},
    )
    assert r["cuenta_valida"] is True
    assert r["tipo"] == "cancelacion"


async def test_validar_comprobante_wrong_cci(tmp_path, monkeypatch):
    _isolate_dedup(monkeypatch, tmp_path)
    reg = ToolRegistry(identity_verified=True, debt_context=_luis_profile(), tenant_id=TENANT)
    r = await reg.execute(
        "validar_comprobante",
        {"cci": "99999999999999999999", "monto": 462.14, "nro_operacion": "OP-004"},
    )
    assert r["cuenta_valida"] is False
    assert "no corresponde" in r["mensaje"].lower()


async def test_validar_comprobante_dedup(tmp_path, monkeypatch):
    _isolate_dedup(monkeypatch, tmp_path)
    reg = ToolRegistry(identity_verified=True, debt_context=_luis_profile(), tenant_id=TENANT)
    args = {"cci": "00389801338381007048", "monto": 462.14, "nro_operacion": "OP-DUP"}
    first = await reg.execute("validar_comprobante", args)
    assert first["dedup_ok"] is True
    second = await reg.execute("validar_comprobante", args)
    assert second["dedup_ok"] is False
    assert "duplicad" in second["mensaje"].lower() or "ya lo recibimos" in second["mensaje"].lower()


async def test_validar_comprobante_cci_with_spaces_matches(tmp_path, monkeypatch):
    # Doris stores some CCIs with spaces; the tool normalizes to digits.
    _isolate_dedup(monkeypatch, tmp_path)
    reg = ToolRegistry(identity_verified=True, debt_context=_luis_profile(), tenant_id=TENANT)
    r = await reg.execute(
        "validar_comprobante",
        {"cci": "003 898 013383810 07048", "monto": 462.14, "nro_operacion": "OP-005"},
    )
    assert r["cuenta_valida"] is True
    assert r["tipo"] == "pago"


# ── helper ──────────────────────────────────────────────────────────────────

def _isolate_dedup(monkeypatch, tmp_path) -> None:
    """Point the comprobantes dedup store at a temp file per test."""
    import tools.cobranza as cobranza

    monkeypatch.setattr(cobranza, "_COMPROBANTES_PATH", tmp_path / "comprobantes.json")
