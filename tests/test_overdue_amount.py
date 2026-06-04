"""SLICE E — RED tests: monto_vencido + cuotas_vencidas as primary debt display.

Confirmed live for P04197 (2026-06-04):
  monto_vencido   = 28,127.64  (SUM cuota_esperada_mensual where unpaid + overdue)
  cuotas_vencidas = 4          (COUNT same filter)
  balance         = 81,510.15  (total remaining — kept as secondary)

The bot must LEAD with what the borrower owes to GET CURRENT (overdue amount),
not the total remaining loan balance.

Edge case: cuotas_vencidas == 0 (al_dia) — no scary "vencido" label shown.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from features.cobranza import doris_debt_source as dds
from features.cobranza.tools import consultar_deuda
from features.conversation import responses as R
from tenancy.responses_spec import ResponsesSpec

TENANT = "prestamype"


def _prestamype_schema() -> dict:
    root = Path(__file__).resolve().parent.parent / "tenants" / TENANT / "tenant.config.json"
    return json.loads(root.read_text(encoding="utf-8"))["doris_schema"]


def _tenant_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "tenants" / TENANT


def _spec() -> ResponsesSpec:
    return ResponsesSpec.from_dir(_tenant_dir(), response_mode="hybrid")


def _make_conn(result_row: dict):
    """Mock pymysql connection returning a single combined result row."""
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchall.return_value = [result_row]
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


# ── P04197 joined row WITH overdue aggregates ─────────────────────────────────

_P04197_JOINED_ROW_WITH_OVERDUE = {
    "account_id": "P04197",
    "loan_number": "P04197",
    "borrower_name": "PRUEBA CLIENTE CUATRO",
    "dni": "12345678",
    "email": "test@example.com",
    "phone": "987654321",
    "days_overdue": 94,
    "next_due_date": "2026-03-02",
    "currency": "SOLES",
    "banco": "BCP",
    "cci": "00382100123456789012",
    "cuenta_bancaria": "20300001234",
    "inversionista": "FONDO A",
    "next_installment_amount": 7031.91,
    "cuota_esperada": 7031.91,
    "saldo_por_cancelar": 81510.15,
    "balance": 81510.15,
    "principal_original": None,
    # Slice E: overdue aggregate fields
    "monto_vencido": 28127.64,
    "cuotas_vencidas": 4,
}

# Al día case: no overdue installments
_AL_DIA_ROW = {
    "account_id": "P02137",
    "loan_number": "P02137",
    "borrower_name": "CARLOS MENDOZA",
    "dni": "44218903",
    "email": "c@example.com",
    "phone": "951000111",
    "days_overdue": 0,
    "next_due_date": "2026-06-18",
    "currency": "SOLES",
    "banco": "INTERBANK",
    "cci": "00389801338381007048",
    "cuenta_bancaria": "20100001234",
    "inversionista": "INVERSIONISTA DEMO UNO",
    "next_installment_amount": 462.14,
    "cuota_esperada": 462.14,
    "saldo_por_cancelar": 18420.0,
    "balance": 18420.0,
    "principal_original": None,
    # Al día: no overdue installments
    "monto_vencido": 0.0,
    "cuotas_vencidas": 0,
}


# ── E.1: SQL includes overdue aggregate subquery ───────────────────────────────

def test_build_sql_includes_monto_vencido_aggregate():
    """_build_sql must emit a SUM subquery for monto_vencido (overdue installments)."""
    schema = _prestamype_schema()
    sql, _ = dds._build_sql(schema)
    assert "monto_vencido" in sql.lower(), (
        "SQL must include monto_vencido aggregate column"
    )


def test_build_sql_includes_cuotas_vencidas_aggregate():
    """_build_sql must emit a COUNT subquery for cuotas_vencidas."""
    schema = _prestamype_schema()
    sql, _ = dds._build_sql(schema)
    assert "cuotas_vencidas" in sql.lower(), (
        "SQL must include cuotas_vencidas aggregate column"
    )


def test_build_sql_overdue_aggregate_filters_unpaid_and_overdue():
    """The overdue aggregate must filter: fecha_de_pago_del_cliente IS NULL AND fecha <= CURDATE()."""
    schema = _prestamype_schema()
    sql, _ = dds._build_sql(schema)
    # Must aggregate only unpaid AND past-due installments
    assert "fecha_de_pago_del_cliente IS NULL" in sql
    assert "CURDATE()" in sql
    # SUM of cuota_esperada_mensual
    assert "SUM" in sql.upper()
    assert "cuota_esperada_mensual" in sql


def test_build_sql_overdue_cte_groups_by_codigo_contrato():
    """The overdue aggregate CTE must GROUP BY codigo_contrato."""
    schema = _prestamype_schema()
    sql, _ = dds._build_sql(schema)
    # There must be a GROUP BY that groups per-contrato for the aggregate
    assert "GROUP BY" in sql.upper()


# ── E.2: consultar_deuda returns monto_vencido + cuotas_vencidas ──────────────

async def test_consultar_deuda_returns_monto_vencido(monkeypatch):
    """consultar_deuda must include monto_vencido in its result dict."""
    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_connect", lambda db: _make_conn(_P04197_JOINED_ROW_WITH_OVERDUE))

    profile = dds.resolve_dni("12345678", tenant_id=TENANT)
    assert profile is not None
    result = await consultar_deuda(profile)
    assert "monto_vencido" in result, "consultar_deuda must return monto_vencido"
    assert result["monto_vencido"] == 28127.64, (
        f"monto_vencido must be 28127.64, got {result['monto_vencido']}"
    )


async def test_consultar_deuda_returns_monto_vencido_formatted(monkeypatch):
    """consultar_deuda must include monto_vencido_formatted (currency-formatted string)."""
    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_connect", lambda db: _make_conn(_P04197_JOINED_ROW_WITH_OVERDUE))

    profile = dds.resolve_dni("12345678", tenant_id=TENANT)
    result = await consultar_deuda(profile)
    assert "monto_vencido_formatted" in result
    assert "28,127.64" in result["monto_vencido_formatted"], (
        f"monto_vencido_formatted must contain 28,127.64, got {result['monto_vencido_formatted']}"
    )


async def test_consultar_deuda_returns_cuotas_vencidas(monkeypatch):
    """consultar_deuda must include cuotas_vencidas in its result dict."""
    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_connect", lambda db: _make_conn(_P04197_JOINED_ROW_WITH_OVERDUE))

    profile = dds.resolve_dni("12345678", tenant_id=TENANT)
    result = await consultar_deuda(profile)
    assert "cuotas_vencidas" in result, "consultar_deuda must return cuotas_vencidas"
    assert result["cuotas_vencidas"] == 4, (
        f"cuotas_vencidas must be 4, got {result['cuotas_vencidas']}"
    )


async def test_consultar_deuda_balance_still_present(monkeypatch):
    """balance (total remaining) must still be present as secondary context."""
    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_connect", lambda db: _make_conn(_P04197_JOINED_ROW_WITH_OVERDUE))

    profile = dds.resolve_dni("12345678", tenant_id=TENANT)
    result = await consultar_deuda(profile)
    assert result["balance"] == 81510.15, "balance (total) must remain in result"


# ── E.3: al-día edge case (cuotas_vencidas == 0) ──────────────────────────────

async def test_consultar_deuda_al_dia_monto_vencido_is_zero(monkeypatch):
    """For an al-día borrower monto_vencido must be 0.0 and cuotas_vencidas must be 0."""
    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_connect", lambda db: _make_conn(_AL_DIA_ROW))

    profile = dds.resolve_dni("44218903", tenant_id=TENANT)
    assert profile is not None
    result = await consultar_deuda(profile)
    assert result.get("monto_vencido", 0.0) == 0.0, (
        f"al-día borrower must have monto_vencido=0.0, got {result.get('monto_vencido')}"
    )
    assert result.get("cuotas_vencidas", 0) == 0, (
        f"al-día borrower must have cuotas_vencidas=0, got {result.get('cuotas_vencidas')}"
    )


# ── E.4: template leads with vencido, secondary saldo ────────────────────────

def test_consulta_deuda_template_mentions_vencido():
    """The consulta_deuda template must reference monto_vencido or vencido concept."""
    spec = _spec()
    intent_cfg = spec.intents.get("consulta_deuda", {})
    template = intent_cfg.get("template", "")
    assert "vencido" in template.lower() or "monto_vencido" in template.lower(), (
        f"consulta_deuda template must lead with 'vencido' concept. Template: {template!r}"
    )


def test_consulta_deuda_template_has_monto_vencido_variable():
    """The consulta_deuda template must use {monto_vencido} variable."""
    spec = _spec()
    intent_cfg = spec.intents.get("consulta_deuda", {})
    template = intent_cfg.get("template", "")
    assert "{monto_vencido}" in template, (
        f"consulta_deuda template must contain {{monto_vencido}}. Template: {template!r}"
    )


def test_consulta_deuda_template_has_cuotas_vencidas_variable():
    """The consulta_deuda template must use {cuotas_vencidas} variable."""
    spec = _spec()
    intent_cfg = spec.intents.get("consulta_deuda", {})
    template = intent_cfg.get("template", "")
    assert "{cuotas_vencidas}" in template, (
        f"consulta_deuda template must contain {{cuotas_vencidas}}. Template: {template!r}"
    )


def test_consulta_deuda_template_render_vencido_values():
    """Template render must fill monto_vencido and cuotas_vencidas from profile."""
    from features.conversation.responses import render_intent, SOURCE_KEYWORD
    from shared.templates import build_variables

    spec = _spec()
    # Profile with overdue data (mock fixture)
    profile = {
        "account_id": "P04239",
        "loan_number": "P04239",
        "borrower_name": "SANDRA HUAMAN DIAZ",
        "currency": "PEN",
        "currency_symbol": "S/",
        "balance": 138720.84,
        "saldo_por_cancelar": 138720.84,
        "next_due_date": "2026-02-20",
        "next_installment_amount": 3210.55,
        "days_overdue": 97,
        "status": "en_mora",
        "status_label": "En mora",
        "banco": "BCP",
        "cci": "00219100993300003256",
        "inversionista": "INVERSIONISTA DEMO TRES",
        "cuenta_bancaria": "20300009876",
        "monto_vencido": 9631.65,    # 3 overdue cuotas × 3210.55
        "cuotas_vencidas": 3,
    }
    res = render_intent(spec, "consulta_deuda", profile, source=SOURCE_KEYWORD)
    assert res is not None
    # Rendered text must contain the overdue amount
    assert "9,631.65" in res.text or "9631" in res.text, (
        f"Rendered text must contain monto_vencido value. Got: {res.text!r}"
    )
    # Must contain cuotas_vencidas count
    assert "3" in res.text, f"Rendered text must mention cuotas_vencidas=3. Got: {res.text!r}"


# ── E.5: mock fixture has monto_vencido + cuotas_vencidas ────────────────────

def test_mock_borrowers_have_monto_vencido():
    """All mock borrowers must have monto_vencido field for demo coherence."""
    path = Path(__file__).resolve().parent.parent / "tenants" / TENANT / "mock" / "borrowers.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for account_id, b in data["borrowers"].items():
        assert "monto_vencido" in b, f"Borrower {account_id} missing monto_vencido"
        assert "cuotas_vencidas" in b, f"Borrower {account_id} missing cuotas_vencidas"


def test_mock_borrower_al_dia_has_zero_vencido():
    """Al día borrowers in mock must have monto_vencido=0 and cuotas_vencidas=0."""
    path = Path(__file__).resolve().parent.parent / "tenants" / TENANT / "mock" / "borrowers.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for account_id, b in data["borrowers"].items():
        if b.get("status") == "al_dia":
            assert b["monto_vencido"] == 0.0, (
                f"Al día borrower {account_id} must have monto_vencido=0.0"
            )
            assert b["cuotas_vencidas"] == 0, (
                f"Al día borrower {account_id} must have cuotas_vencidas=0"
            )


def test_mock_borrower_en_mora_has_positive_vencido():
    """En mora borrowers in mock must have monto_vencido > 0 and cuotas_vencidas > 0."""
    path = Path(__file__).resolve().parent.parent / "tenants" / TENANT / "mock" / "borrowers.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for account_id, b in data["borrowers"].items():
        if b.get("status") == "en_mora":
            assert b["monto_vencido"] > 0, (
                f"En mora borrower {account_id} must have monto_vencido>0"
            )
            assert b["cuotas_vencidas"] > 0, (
                f"En mora borrower {account_id} must have cuotas_vencidas>0"
            )
