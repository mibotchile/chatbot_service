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

# Fixture borrowers (synthetic demo data). DNIs are 8-digit and fictitious.
# 5 casos de casuística distinta (demo-1..5).
LUIS = "44218903"   # P02137, al día, PEN, cuota 462.14, saldo 23800, CCI 00389801338381007048
LUCIA = "76310582"  # P03250, mora leve (8d), PEN, saldo 14620.50
SANDRA = "46128750" # P04239, mora severa (97d), PEN, saldo alto 138720.84
ELMER = "08642195"  # P03650, mora, USD, cuota 1397.71, saldo 31840
ROSA = "40517264"   # P04880, casi cancelado / al día, PEN, saldo 845.60


def _fixture() -> dict:
    root = Path(__file__).resolve().parent.parent / "tenants" / TENANT / "mock" / "borrowers.json"
    return json.loads(root.read_text(encoding="utf-8"))


# ── Fixture loads ─────────────────────────────────────────────────────────

def test_fixture_loads_with_5_borrowers_and_tokens():
    data = _fixture()
    assert len(data["borrowers"]) == 5
    assert set(data["tokens"]) == {"demo-1", "demo-2", "demo-3", "demo-4", "demo-5"}
    # Every borrower has the standard + prestamype extra fields.
    for prof in data["borrowers"].values():
        for field in ("account_id", "dni", "balance", "status", "days_overdue"):
            assert field in prof
        for extra in ("cci", "banco", "inversionista", "cuota_esperada", "saldo_por_cancelar"):
            assert extra in prof


def test_demo_tokens_point_at_distinct_credits():
    data = _fixture()
    accounts = [data["tokens"][t] for t in ("demo-1", "demo-2", "demo-3", "demo-4", "demo-5")]
    assert accounts == ["P02137", "P04239", "P03650", "P05012", "P05480"]
    assert len(set(accounts)) == 5


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
    # demo-3 is the USD credit (Javier, mora leve en dólares).
    prof = debt_source.resolve_token("demo-3", tenant_id=TENANT)
    assert prof is not None
    assert prof["account_id"] == "P03650"
    assert prof["currency"] == "USD"
    assert prof["status"] == "en_mora"


def test_resolve_dni_unknown_returns_none():
    assert debt_source.resolve_dni("00000000", tenant_id=TENANT) is None


# ── PrestamYpe casuística: multi-crédito y crédito grupal ──────────────────

async def test_consultar_deuda_lists_multiple_credits_same_dni():
    from tools.cobranza import consultar_deuda

    # demo-4: mismo DNI con 2 créditos vigentes (uno al día, otro en mora leve).
    prof = debt_source.resolve_dni(LUCIA, tenant_id=TENANT)
    assert prof["account_id"] == "P05012"
    summary = await consultar_deuda(prof)
    assert summary["has_multiple_credits"] is True
    assert summary["credits_count"] == 2
    accts = {c["account_id"] for c in summary["credits"]}
    assert accts == {"P05012", "P05119"}
    # ambos créditos exponen su saldo y estado
    statuses = {c["status"] for c in summary["credits"]}
    assert statuses == {"al_dia", "en_mora"}


async def test_consultar_deuda_flags_grupal_with_codeudores():
    from tools.cobranza import consultar_deuda

    # demo-5: crédito grupal compartido por 2 codeudores, casi cancelado.
    prof = debt_source.resolve_dni(ROSA, tenant_id=TENANT)
    assert prof["account_id"] == "P05480"
    summary = await consultar_deuda(prof)
    assert summary["is_grupal"] is True
    assert len(summary["codeudores"]) == 2
    # DNIs de codeudores SIEMPRE enmascarados (no se expone el completo)
    for c in summary["codeudores"]:
        assert c["borrower_name"]
        assert "*" in c["dni_masked"]
        assert len(c["dni_masked"]) <= 8


async def test_consultar_deuda_single_credit_has_no_multi_flags():
    from tools.cobranza import consultar_deuda

    prof = debt_source.resolve_dni(LUIS, tenant_id=TENANT)  # al día, 1 crédito
    summary = await consultar_deuda(prof)
    assert "has_multiple_credits" not in summary
    assert "is_grupal" not in summary


# ── Side panel: structured cards in the consultar_deuda response ───────────
# The widget paints a contextual side panel from ui_actions["panel"], built by
# build_ui_actions for the consultar_deuda tool result. Tenant-agnostic / core.


async def test_consultar_deuda_exposes_bank_and_masked_cci():
    from tools.cobranza import consultar_deuda

    prof = debt_source.resolve_dni(LUIS, tenant_id=TENANT)
    summary = await consultar_deuda(prof)
    assert summary["banco"] == "INTERBANK"
    # CCI is masked to its last 4 digits — the full 20-digit CCI is never exposed.
    assert summary["cci_masked"] == "···7048"
    assert prof["cci"] not in summary["cci_masked"]


async def test_build_panel_single_credit():
    from tools.cobranza import consultar_deuda
    from core.response_builder import build_ui_actions

    prof = debt_source.resolve_dni(LUIS, tenant_id=TENANT)  # al día, 1 crédito
    summary = await consultar_deuda(prof)
    actions = build_ui_actions([("consultar_deuda", summary)])
    panel = actions["panel"]
    assert panel["type"] == "debt"
    assert panel["count"] == 1
    card = panel["cards"][0]
    assert card["loan_number"] == "P02137"
    assert card["balance_formatted"] == "S/ 18,420.00"
    assert card["next_due_date"] == "2026-06-18"
    assert card["banco"] == "INTERBANK"
    assert card["cci_masked"] == "···7048"
    assert card["badge"]["kind"] == "aldia"


async def test_build_panel_mora_badge_has_days():
    from tools.cobranza import consultar_deuda
    from core.response_builder import build_ui_actions

    prof = debt_source.resolve_dni(SANDRA, tenant_id=TENANT)  # mora 97d
    summary = await consultar_deuda(prof)
    panel = build_ui_actions([("consultar_deuda", summary)])["panel"]
    badge = panel["cards"][0]["badge"]
    assert badge["kind"] == "mora"
    assert "97 día" in badge["label"]


async def test_build_panel_multi_credit_one_card_each():
    from tools.cobranza import consultar_deuda
    from core.response_builder import build_ui_actions

    prof = debt_source.resolve_dni(LUCIA, tenant_id=TENANT)  # 2 créditos
    summary = await consultar_deuda(prof)
    panel = build_ui_actions([("consultar_deuda", summary)])["panel"]
    assert panel["count"] == 2
    loans = {c["loan_number"] for c in panel["cards"]}
    assert loans == {"P05012", "P05119"}
    badges = {c["badge"]["kind"] for c in panel["cards"]}
    assert badges == {"aldia", "mora"}


async def test_build_panel_grupal_attaches_codeudores():
    from tools.cobranza import consultar_deuda
    from core.response_builder import build_ui_actions

    prof = debt_source.resolve_dni(ROSA, tenant_id=TENANT)  # crédito grupal
    summary = await consultar_deuda(prof)
    panel = build_ui_actions([("consultar_deuda", summary)])["panel"]
    assert panel["count"] == 1
    card = panel["cards"][0]
    assert card["is_grupal"] is True
    assert len(card["codeudores"]) == 2
    assert all(g.get("borrower_name") for g in card["codeudores"])


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
        {"cci": "00389801338381007048", "monto": 18420.00, "nro_operacion": "OP-003"},
    )
    assert r["cuenta_valida"] is True
    assert r["tipo"] == "cancelacion"


async def test_validar_comprobante_arbitrary_cci_accepted(tmp_path, monkeypatch):
    # CCI pertenencia is NO LONGER validated: any CCI is accepted and stored
    # as-is. Classification is done against the DNI's credit, not the CCI.
    _isolate_dedup(monkeypatch, tmp_path)
    reg = ToolRegistry(identity_verified=True, debt_context=_luis_profile(), tenant_id=TENANT)
    r = await reg.execute(
        "validar_comprobante",
        {"cci": "99999999999999999999", "monto": 462.14, "nro_operacion": "OP-004"},
    )
    assert r["cuenta_valida"] is True
    assert r["credito"] == "P02137"
    assert r["tipo"] == "pago"  # classified vs the credit's cuota, not the CCI
    assert r["dedup_ok"] is True
    assert "no corresponde" not in r["mensaje"].lower()


async def test_validar_comprobante_dedup(tmp_path, monkeypatch):
    _isolate_dedup(monkeypatch, tmp_path)
    reg = ToolRegistry(identity_verified=True, debt_context=_luis_profile(), tenant_id=TENANT)
    args = {"cci": "00389801338381007048", "monto": 462.14, "nro_operacion": "OP-DUP"}
    first = await reg.execute("validar_comprobante", args)
    assert first["dedup_ok"] is True
    second = await reg.execute("validar_comprobante", args)
    assert second["dedup_ok"] is False
    assert "duplicad" in second["mensaje"].lower() or "ya lo recibimos" in second["mensaje"].lower()


async def test_validar_comprobante_cci_with_spaces_accepted(tmp_path, monkeypatch):
    # CCIs with spaces are accepted (normalized to digits for storage); the
    # voucher is still classified against the DNI's credit.
    _isolate_dedup(monkeypatch, tmp_path)
    reg = ToolRegistry(identity_verified=True, debt_context=_luis_profile(), tenant_id=TENANT)
    r = await reg.execute(
        "validar_comprobante",
        {"cci": "003 898 013383810 07048", "monto": 462.14, "nro_operacion": "OP-005"},
    )
    assert r["cuenta_valida"] is True
    assert r["tipo"] == "pago"


# ── account_type: número de cuenta (corto) vs CCI (20 dígitos) ──────────────

async def test_validar_comprobante_account_type_cuenta_corta(tmp_path, monkeypatch):
    # Número de cuenta corto (Jorge feedback): se acepta, se guarda el tipo, y
    # la clasificación sigue siendo por MONTO (no por la cuenta).
    _isolate_dedup(monkeypatch, tmp_path)
    reg = ToolRegistry(identity_verified=True, debt_context=_luis_profile(), tenant_id=TENANT)
    r = await reg.execute(
        "validar_comprobante",
        {
            "account_type": "cuenta",
            "cuenta_destino": "1320268376",
            "monto": 462.14,
            "nro_operacion": "OP-CUENTA",
        },
    )
    assert r["cuenta_valida"] is True          # NO se valida contra Doris
    assert r["account_type"] == "cuenta"
    assert r["cuenta_destino"] == "1320268376"
    assert r["tipo"] == "pago"                 # clasificado por monto
    assert r["dedup_ok"] is True
    # número de cuenta corto en el mensaje (NO se fuerza CCI)
    assert "número de cuenta" in r["mensaje"].lower()


async def test_validar_comprobante_account_type_cci_20(tmp_path, monkeypatch):
    _isolate_dedup(monkeypatch, tmp_path)
    reg = ToolRegistry(identity_verified=True, debt_context=_luis_profile(), tenant_id=TENANT)
    r = await reg.execute(
        "validar_comprobante",
        {
            "account_type": "cci",
            "cuenta_destino": "00389801338381007048",
            "monto": 462.14,
            "nro_operacion": "OP-CCI20",
        },
    )
    assert r["cuenta_valida"] is True
    assert r["account_type"] == "cci"
    assert r["tipo"] == "pago"


async def test_validar_comprobante_stores_account_type_in_audit(tmp_path, monkeypatch):
    # El audit/registro debe guardar tipo + número de cuenta (y el alias cci).
    _isolate_dedup(monkeypatch, tmp_path)
    import json as _json
    import tools.cobranza as cobranza

    reg = ToolRegistry(identity_verified=True, debt_context=_luis_profile(), tenant_id=TENANT)
    await reg.execute(
        "validar_comprobante",
        {
            "account_type": "cuenta",
            "cuenta_destino": "1320268376",
            "monto": 462.14,
            "nro_operacion": "OP-AUDIT",
        },
    )
    items = _json.loads(cobranza._COMPROBANTES_PATH.read_text(encoding="utf-8"))
    rec = next(r for r in items if r["nro_operacion"] == "OP-AUDIT")
    assert rec["account_type"] == "cuenta"
    assert rec["cuenta_destino"] == "1320268376"
    assert rec["cci"] == "1320268376"  # legacy alias preserved


async def test_validar_comprobante_dedup_by_nro_operacion_independent_of_type(tmp_path, monkeypatch):
    # Dedup sigue siendo por nº de operación (no por la cuenta).
    _isolate_dedup(monkeypatch, tmp_path)
    reg = ToolRegistry(identity_verified=True, debt_context=_luis_profile(), tenant_id=TENANT)
    first = await reg.execute(
        "validar_comprobante",
        {
            "account_type": "cuenta", "cuenta_destino": "1320268376",
            "monto": 462.14, "nro_operacion": "OP-DUP2",
        },
    )
    assert first["dedup_ok"] is True
    # mismo nº de operación, distinta cuenta/tipo → igual se considera duplicado
    second = await reg.execute(
        "validar_comprobante",
        {
            "account_type": "cci", "cuenta_destino": "00389801338381007048",
            "monto": 462.14, "nro_operacion": "OP-DUP2",
        },
    )
    assert second["dedup_ok"] is False


# ── scope: prestamype acotado a 2 capacidades (consulta + comprobante) ──────

def _tenant_config():
    from core.tenant_loader import TenantConfig

    root = Path(__file__).resolve().parent.parent / "tenants" / TENANT
    return TenantConfig.from_directory(root)


def test_prestamype_excludes_out_of_scope_tools():
    """No refi/negociación/plan/certificado/reclamo: those tools are excluded.

    The engine has no negociación/refi/plan tools, so the only out-of-scope
    tools that EXIST are reclamo, certificado and enviar_documento — all three
    must be excluded for prestamype.
    """
    excluded = set(_tenant_config().excluded_tools or [])
    assert {"registrar_reclamo", "emitir_certificado_no_adeudo", "enviar_documento"} <= excluded


def test_prestamype_active_tools_only_in_scope():
    """After filtering, the tool set is limited to the 2 capabilities + plumbing.

    Allowed: identity (identificar_cliente), debt query (consultar_deuda),
    voucher (validar_comprobante), escalation, and generic engine plumbing.
    Forbidden: any reclamo/certificado/documento tool.
    """
    from config.tools_schema import TOOL_DEFINITIONS

    excluded = set(_tenant_config().excluded_tools or [])
    active = {t["name"] for t in TOOL_DEFINITIONS if t["name"] not in excluded}
    assert "registrar_reclamo" not in active
    assert "emitir_certificado_no_adeudo" not in active
    assert "enviar_documento" not in active
    # the 2 real capabilities + identity remain available
    assert {"identificar_cliente", "consultar_deuda", "validar_comprobante"} <= active


def test_prestamype_guardrails_forbid_refi_and_keep_two_capabilities():
    g = _tenant_config().guardrails.lower()
    # explicitly scoped to debt query + voucher
    assert "consulta de deuda" in g
    assert "comprobante" in g
    # explicitly forbids refi / negotiation / plan / cert / reclamo
    for term in ("refinanciamiento", "negociación", "plan", "certificado", "reclamo"):
        assert term in g


# ── helper ──────────────────────────────────────────────────────────────────

def _isolate_dedup(monkeypatch, tmp_path) -> None:
    """Point the comprobantes dedup store at a temp file per test."""
    import tools.cobranza as cobranza

    monkeypatch.setattr(cobranza, "_COMPROBANTES_PATH", tmp_path / "comprobantes.json")
