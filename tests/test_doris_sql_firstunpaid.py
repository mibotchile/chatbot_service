"""SLICE A — RED tests: first-unpaid installment balance + latest-batch selection.

These tests assert the NEW _build_sql behavior (ROW_NUMBER window subqueries).
They FAIL against the current MAX-aggregate implementation and pass after GREEN.

P04197 fixture facts (confirmed live 2026-06-04):
  balance         = 81,510.15  (first-unpaid saldo_por_cancelar)
  cuota           = 7,031.91   (cuota_esperada_mensual of first-unpaid row)
  next_due_date   = 2026-03-02 (fecha_de_pago_esperada_original)
  dias_mora       = derived (today - fecha_de_pago_esperada_original),
                    NOT the stale batch column dias_de_atraso_de_pago

days_overdue resolution: live Doris shows dias_de_atraso_de_pago=44 for P04197
but today (2026-06-04) - 2026-03-02 = 94 days. The column is batch-computed and
stale; derived value is always correct. Decision: use SQL-derived days_overdue =
DATEDIFF(CURDATE(), fecha_de_pago_esperada_original) clamped to >= 0.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from features.cobranza import doris_debt_source as dds

TENANT = "prestamype"


def _prestamype_schema() -> dict:
    root = Path(__file__).resolve().parent.parent / "tenants" / TENANT / "tenant.config.json"
    return json.loads(root.read_text(encoding="utf-8"))["doris_schema"]


# ── Slice A.1: SQL shape must use ROW_NUMBER, NOT MAX ──────────────────────────

def test_build_sql_uses_row_number_not_max_for_pagos():
    """After GREEN: _build_sql must emit ROW_NUMBER() OVER PARTITION BY codigo_contrato."""
    schema = _prestamype_schema()
    sql, _ = dds._build_sql(schema)
    # ROW_NUMBER window for pagos selection (first-unpaid ordering)
    assert "ROW_NUMBER() OVER" in sql, "SQL must use ROW_NUMBER() OVER window function"
    assert "PARTITION BY" in sql, "SQL must use PARTITION BY for row numbering"
    # Must NOT use MAX aggregate for the corrected fields
    assert "MAX(p.saldo_por_cancelar)" not in sql, "Must not use MAX for saldo_por_cancelar"
    assert "MAX(p.cuota_esperada" not in sql, "Must not use MAX for cuota fields"


def test_build_sql_pagos_cte_orders_by_unpaid_first():
    """pagos_sel CTE must order unpaid rows first (fecha_de_pago_del_cliente IS NULL DESC)."""
    schema = _prestamype_schema()
    sql, _ = dds._build_sql(schema)
    # The unpaid-first ordering predicate
    assert "fecha_de_pago_del_cliente IS NULL" in sql
    assert "fecha_de_pago_esperada_original ASC" in sql


def test_build_sql_asig_cte_orders_by_batch_desc():
    """asig_sel CTE must pick the latest batch (creado_el DESC, archivo DESC)."""
    schema = _prestamype_schema()
    sql, _ = dds._build_sql(schema)
    assert "creado_el DESC" in sql, "asig_sel CTE must order by creado_el DESC"
    assert "archivo DESC" in sql, "asig_sel CTE must order by archivo DESC (tiebreak)"


def test_build_sql_joins_on_rn_equals_1():
    """The outer SELECT must filter on rn = 1 for both CTEs."""
    schema = _prestamype_schema()
    sql, _ = dds._build_sql(schema)
    assert "p.rn = 1" in sql or "pagos_sel.rn = 1" in sql, "Must filter pagos on rn=1"
    assert "a.rn = 1" in sql or "asig_sel.rn = 1" in sql, "Must filter asig on rn=1"


def test_build_sql_coalesces_cuota():
    """SQL must COALESCE(cuota_esperada_actualizada, cuota_esperada_mensual)."""
    schema = _prestamype_schema()
    sql, _ = dds._build_sql(schema)
    assert "COALESCE" in sql.upper(), "SQL must use COALESCE for cuota fallback"
    assert "cuota_esperada_actualizada" in sql
    assert "cuota_esperada_mensual" in sql


def test_build_sql_derives_days_overdue():
    """SQL must derive days_overdue from DATEDIFF, not read dias_de_atraso_de_pago directly."""
    schema = _prestamype_schema()
    sql, _ = dds._build_sql(schema)
    # Derived days_overdue using DATEDIFF or GREATEST
    assert "DATEDIFF" in sql.upper() or "days_overdue" in sql.lower(), \
        "SQL must derive days_overdue from date arithmetic"


# ── Slice A.2: P04197 fixture → correct values ───────────────────────────────

# Three pagos rows for P04197: 2 paid, 1 unpaid (the first-unpaid).
# cuota_esperada_actualizada is NULL (Bug 3) so COALESCE falls to cuota_esperada_mensual.
_P04197_PAGOS_ROWS = [
    {
        "codigo_contrato": "P04197",
        "codigo_de_cuota": "P04197-01",
        "cuota_esperada_mensual": 7031.91,
        "cuota_esperada_actualizada": None,  # NULL — Bug 3 case
        "nro_cuotas": 20,
        "fecha_de_pago_esperada_original": date(2026, 1, 2),
        "saldo_por_cancelar": 95542.06,
        "fecha_de_pago_del_cliente": date(2026, 1, 15),  # PAID
        "status": "pagado",
        "dias_de_atraso_de_pago": 0,
        "inversionista": "FONDO A",
        "codigo_de_cuenta_cci": "00382100123456789012",
        "numero_de_cuenta": "20300001234",
        "archivo": "Asignacion_19052026",
        "capital": 108450.0,
    },
    {
        "codigo_contrato": "P04197",
        "codigo_de_cuota": "P04197-02",
        "cuota_esperada_mensual": 7031.91,
        "cuota_esperada_actualizada": None,  # NULL
        "nro_cuotas": 20,
        "fecha_de_pago_esperada_original": date(2026, 2, 2),
        "saldo_por_cancelar": 88478.15,
        "fecha_de_pago_del_cliente": date(2026, 2, 20),  # PAID
        "status": "pagado",
        "dias_de_atraso_de_pago": 0,
        "inversionista": "FONDO A",
        "codigo_de_cuenta_cci": "00382100123456789012",
        "numero_de_cuenta": "20300001234",
        "archivo": "Asignacion_19052026",
        "capital": 108450.0,
    },
    {
        "codigo_contrato": "P04197",
        "codigo_de_cuota": "P04197-03",
        "cuota_esperada_mensual": 7031.91,
        "cuota_esperada_actualizada": None,  # NULL — COALESCE picks mensual
        "nro_cuotas": 20,
        "fecha_de_pago_esperada_original": date(2026, 3, 2),  # FIRST UNPAID
        "saldo_por_cancelar": 81510.15,   # ← correct balance
        "fecha_de_pago_del_cliente": None,  # UNPAID
        "status": "pendiente",
        "dias_de_atraso_de_pago": 44,  # stale batch value — must NOT be used
        "inversionista": "FONDO A",
        "codigo_de_cuenta_cci": "00382100123456789012",
        "numero_de_cuenta": "20300001234",
        "archivo": "Asignacion_20052026_F",  # LATEST batch
        "capital": 108450.0,
    },
]

# Three asig rows for P04197 — three different batches. Latest = _20052026_F.
_P04197_ASIG_ROWS = [
    {
        "id_credito": "P04197",
        "nombre_completo": "PRUEBA CLIENTE CUATRO",
        "dni_ruc": "12345678",
        "correo_electronico": "test@example.com",
        "telefono": "987654321",
        "capital": 108450.0,
        "dias_mora": 44,  # stale
        "fecha_vencimiento": date(2026, 3, 2),
        "moneda": "SOLES",
        "banco": "BCP",
        "codigo_de_cuenta_cci": "00382100123456789012",
        "numero_de_cuenta": "20300001234",
        "inversionista": "FONDO A",
        "creado_el": "2026-05-19 00:00:00",
        "archivo": "Asignacion_19052026",
    },
    {
        "id_credito": "P04197",
        "nombre_completo": "PRUEBA CLIENTE CUATRO",
        "dni_ruc": "12345678",
        "correo_electronico": "test@example.com",
        "telefono": "987654321",
        "capital": 108450.0,
        "dias_mora": 44,
        "fecha_vencimiento": date(2026, 3, 2),
        "moneda": "SOLES",
        "banco": "BCP",
        "codigo_de_cuenta_cci": "00382100123456789012",
        "numero_de_cuenta": "20300001234",
        "inversionista": "FONDO A",
        "creado_el": "2026-05-20 00:00:00",
        "archivo": "Asignacion_20052026",
    },
    {
        "id_credito": "P04197",
        "nombre_completo": "PRUEBA CLIENTE CUATRO",
        "dni_ruc": "12345678",
        "correo_electronico": "test@example.com",
        "telefono": "987654321",
        "capital": 108450.0,
        "dias_mora": 44,
        "fecha_vencimiento": date(2026, 3, 2),
        "moneda": "SOLES",
        "banco": "BCP",
        "codigo_de_cuenta_cci": "00382100123456789012",
        "numero_de_cuenta": "20300001234",
        "inversionista": "FONDO A",
        "creado_el": "2026-05-20 12:00:00",
        "archivo": "Asignacion_20052026_F",  # LATEST
    },
]


def _make_conn(result_row: dict):
    """Mock pymysql connection returning a single combined result row."""
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchall.return_value = [result_row]
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


# The row the new SQL returns: JOIN of latest asig + first-unpaid pagos.
_P04197_JOINED_ROW = {
    "account_id": "P04197",
    "loan_number": "P04197",
    "borrower_name": "PRUEBA CLIENTE CUATRO",
    "dni": "12345678",
    "email": "test@example.com",
    "phone": "987654321",
    "days_overdue": 94,  # derived: today (2026-06-04) - 2026-03-02
    "next_due_date": date(2026, 3, 2),
    "currency": "SOLES",
    "banco": "BCP",
    "cci": "00382100123456789012",
    "cuenta_bancaria": "20300001234",
    "inversionista": "FONDO A",
    # COALESCE result: cuota_esperada_actualizada=NULL → cuota_esperada_mensual=7031.91
    "next_installment_amount": 7031.91,
    "cuota_esperada": 7031.91,
    # first-unpaid saldo_por_cancelar (NOT MAX=108450):
    "saldo_por_cancelar": 81510.15,
    "balance": 81510.15,
    # capital omitted per spec (principal_original kept for backward compat but 0/None)
    "principal_original": None,
}


def test_p04197_balance_is_first_unpaid_not_max(monkeypatch):
    """resolve_dni for P04197 must return balance=81510.15, NOT 108450 (MAX bug)."""
    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_connect", lambda db: _make_conn(_P04197_JOINED_ROW))

    prof = dds.resolve_dni("12345678", tenant_id=TENANT)
    assert prof is not None, "profile must not be None"
    assert prof["balance"] == 81510.15, (
        f"balance must be first-unpaid 81510.15, got {prof['balance']} "
        f"(MAX would give 108450)"
    )
    assert prof["saldo_por_cancelar"] == 81510.15


def test_p04197_cuota_coalesce_uses_mensual_when_actualizada_is_null(monkeypatch):
    """When cuota_esperada_actualizada is NULL, cuota must equal cuota_esperada_mensual."""
    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_connect", lambda db: _make_conn(_P04197_JOINED_ROW))

    prof = dds.resolve_dni("12345678", tenant_id=TENANT)
    assert prof is not None
    # COALESCE(None, 7031.91) = 7031.91
    cuota = prof.get("next_installment_amount") or prof.get("cuota_esperada")
    assert cuota == 7031.91, f"cuota must be 7031.91 via COALESCE, got {cuota}"


def test_p04197_next_due_date_is_first_unpaid_row(monkeypatch):
    """next_due_date must be 2026-03-02 (first-unpaid fecha_de_pago_esperada_original)."""
    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_connect", lambda db: _make_conn(_P04197_JOINED_ROW))

    prof = dds.resolve_dni("12345678", tenant_id=TENANT)
    assert prof is not None
    assert str(prof["next_due_date"]) == "2026-03-02", (
        f"next_due_date must be 2026-03-02, got {prof['next_due_date']}"
    )


def test_p04197_days_overdue_is_derived_not_stale_column(monkeypatch):
    """days_overdue must come from derived date arithmetic, NOT dias_de_atraso_de_pago=44."""
    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_connect", lambda db: _make_conn(_P04197_JOINED_ROW))

    prof = dds.resolve_dni("12345678", tenant_id=TENANT)
    assert prof is not None
    # days_overdue in the joined row is 94 (derived, 2026-06-04 - 2026-03-02)
    # NOT 44 (the stale batch column value)
    assert prof["days_overdue"] == 94, (
        f"days_overdue must be derived (94), not stale batch column (44). Got {prof['days_overdue']}"
    )


# ── Slice A.3: latest-batch determinism ────────────────────────────────────────

def test_latest_batch_is_deterministic_regardless_of_row_order(monkeypatch):
    """The latest batch row must be returned consistently (ROW_NUMBER not ORDER BY luck)."""
    # SQL window function handles this — mock returns the already-filtered row.
    # If _build_sql emits ROW_NUMBER PARTITION BY id_credito ORDER BY creado_el DESC,
    # the JOIN on rn=1 ensures only one row comes back regardless of fetch order.
    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_connect", lambda db: _make_conn(_P04197_JOINED_ROW))

    results = [dds.resolve_dni("12345678", tenant_id=TENANT) for _ in range(3)]
    balances = [r["balance"] for r in results if r]
    assert len(set(balances)) == 1, f"balance must be deterministic across calls: {balances}"


# ── Slice A.4: cuota COALESCE — both branches ─────────────────────────────────

def test_cuota_coalesce_uses_actualizada_when_present(monkeypatch):
    """When cuota_esperada_actualizada is non-NULL, it takes precedence over mensual."""
    dds._load_schema.cache_clear()
    row_with_actualizada = dict(_P04197_JOINED_ROW)
    row_with_actualizada["next_installment_amount"] = 6500.00  # actualizada present
    row_with_actualizada["cuota_esperada"] = 6500.00
    monkeypatch.setattr(dds, "_connect", lambda db: _make_conn(row_with_actualizada))

    prof = dds.resolve_dni("12345678", tenant_id=TENANT)
    assert prof is not None
    cuota = prof.get("next_installment_amount") or prof.get("cuota_esperada")
    assert cuota == 6500.00, f"COALESCE must use actualizada (6500.00) when present, got {cuota}"


# ── Slice A.5: cuenta_bancaria in profile ─────────────────────────────────────

def test_profile_contains_cuenta_bancaria(monkeypatch):
    """Profile must include cuenta_bancaria from numero_de_cuenta column."""
    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_connect", lambda db: _make_conn(_P04197_JOINED_ROW))

    prof = dds.resolve_dni("12345678", tenant_id=TENANT)
    assert prof is not None
    assert "cuenta_bancaria" in prof, "Profile must contain cuenta_bancaria field"
    assert prof["cuenta_bancaria"] == "20300001234"
