"""Hotfix — cuenta_bancaria DOUBLE→DECIMAL cast.

Root cause (confirmed live 2026-06-04):
  `numero_de_cuenta` is a DOUBLE column in Doris. A direct SELECT returns it as
  a proper integer string, BUT when Doris wraps the read inside a window CTE
  (SELECT *, ROW_NUMBER() OVER ...) it promotes the value to scientific notation
  "8.98348E+12". Once in scientific form, digits are lost permanently.

Fix:
  - Cast at the SOURCE (inside the asig_sel / assignment CTE) using
    CAST(numero_de_cuenta AS DECIMAL(38,0)). Must happen BEFORE the window, not after.
  - Casting inside/after the window CTE returns NULL (verified live).
  - Column config gets a new `cast: "id_number"` hint to express this intent.
  - _row_to_profile formats id_number fields as clean digit strings (str(int(value))).

These tests are RED against the current code and become GREEN after the hotfix.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from features.cobranza import doris_debt_source as dds

TENANT = "prestamype"


def _prestamype_schema() -> dict:
    root = Path(__file__).resolve().parent.parent / "tenants" / TENANT / "tenant.config.json"
    return json.loads(root.read_text(encoding="utf-8"))["doris_schema"]


def _make_conn(result_row: dict):
    """Mock pymysql connection returning a single combined result row."""
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchall.return_value = [result_row]
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


# ── SQL generation tests ──────────────────────────────────────────────────────

def test_sql_casts_numero_de_cuenta_as_decimal_at_source():
    """Generated SQL must CAST(numero_de_cuenta AS DECIMAL(38,0)) inside asig_sel CTE.

    The cast must appear BEFORE (or at) the base-table read — i.e. inside the
    asig_sel / assignment CTE that does the ROW_NUMBER window. Casting after the
    window CTE returns NULL (verified on Doris live). The outer SELECT must then
    reference the already-cast alias, NOT re-apply CAST after the window.
    """
    dds._load_schema.cache_clear()
    schema = _prestamype_schema()
    sql, _ = dds._build_sql(schema)

    # The CAST must appear in the asig_sel CTE (before the window CTE's outer select).
    # We verify by checking that:
    # 1. CAST(... AS DECIMAL appears in the SQL
    # 2. It appears before "WHERE a.rn = 1" (outer filter), i.e. inside the CTE
    assert "CAST(" in sql, "SQL must contain a CAST expression for numero_de_cuenta"
    assert "DECIMAL(38,0)" in sql, "Cast must be DECIMAL(38,0) to recover full integer"
    assert "numero_de_cuenta" in sql, "numero_de_cuenta column must appear in SQL"

    # The CAST must be inside the asig_sel CTE definition (before the outer SELECT)
    asig_sel_start = sql.find("asig_sel AS")
    outer_select_start = sql.find("SELECT\n    ", sql.find("WHERE a.rn = 1") - 500)
    cast_pos = sql.find("CAST(")
    assert cast_pos != -1, "CAST must appear in SQL"
    assert cast_pos < sql.find("WHERE a.rn = 1"), (
        "CAST must appear before the outer rn=1 filter — it must be inside the "
        "asig_sel CTE (base-table read), not applied after the window"
    )


def test_sql_outer_select_references_cast_alias_not_raw_column():
    """The outer SELECT must use the already-cast alias, not re-cast after the window."""
    dds._load_schema.cache_clear()
    schema = _prestamype_schema()
    sql, _ = dds._build_sql(schema)

    # After the CTE definitions, the outer SELECT (FROM asig_sel a JOIN...)
    # must select a.cuenta_bancaria (the alias) — NOT apply a new CAST on a column
    # that was already promoted to scientific notation by the window.
    outer_section = sql[sql.find("FROM asig_sel a"):]
    # The outer select should not contain a second CAST(a.numero_de_cuenta ...
    assert "CAST(a.numero_de_cuenta" not in outer_section, (
        "Outer SELECT must not re-cast after the window — "
        "cast MUST be inside the asig_sel CTE"
    )


def test_sql_cci_is_not_cast():
    """cci (codigo_de_cuenta_cci) must NOT receive a DECIMAL cast — it is a string with leading zeros."""
    dds._load_schema.cache_clear()
    schema = _prestamype_schema()
    sql, _ = dds._build_sql(schema)

    # cci should appear as a plain column reference, not wrapped in CAST(... DECIMAL)
    assert "CAST(a.codigo_de_cuenta_cci AS DECIMAL" not in sql, (
        "cci must not be DECIMAL-cast — it is a string column with leading zeros "
        "('00389801347991694443') and must be passed through as-is"
    )


# ── _row_to_profile formatting tests ─────────────────────────────────────────

def test_row_to_profile_formats_cuenta_bancaria_as_clean_digit_string():
    """_row_to_profile must return cuenta_bancaria as clean digit string '8983479916944'.

    The row comes from Doris after the CAST — value is a Decimal/float/int like
    8983479916944 or 8983479916944.0. Must become '8983479916944', NOT:
    - '8.98348E+12'  (scientific notation — what the uncast window returns)
    - '8983479916944.0'  (trailing .0 from float)
    """
    row = {
        "account_id": "P99999",
        "loan_number": "P99999",
        "borrower_name": "Test Borrower",
        "dni": "44444444",
        "email": "",
        "phone": "999000000",
        "currency": "SOLES",
        "days_overdue": 10,
        "next_due_date": None,
        "banco": "BCP",
        "cci": "00389801347991694443",  # string with leading zeros — must not be touched
        "cuenta_bancaria": 8983479916944,  # integer (post-DECIMAL-cast from Doris)
        "inversionista": "FONDO B",
        "saldo_por_cancelar": 12500.00,
        "balance": 12500.00,
        "cuota_esperada": 1500.00,
        "next_installment_amount": 1500.00,
        "monto_vencido": 0,
        "cuotas_vencidas": 0,
    }
    profile = dds._row_to_profile(row)
    assert profile["cuenta_bancaria"] == "8983479916944", (
        f"Expected '8983479916944', got {profile['cuenta_bancaria']!r}. "
        "Must not be scientific notation or have trailing .0"
    )


def test_row_to_profile_formats_float_cuenta_bancaria_as_clean_digit_string():
    """When Doris returns cuenta_bancaria as float 8983479916944.0, format as '8983479916944'."""
    row = {
        "account_id": "P99999",
        "loan_number": "P99999",
        "borrower_name": "Test",
        "dni": "44444444",
        "email": "",
        "phone": "",
        "currency": "SOLES",
        "days_overdue": 0,
        "next_due_date": None,
        "banco": "BCP",
        "cci": "00389801347991694443",
        "cuenta_bancaria": 8983479916944.0,  # float — trailing .0 must be stripped
        "inversionista": "FONDO B",
        "saldo_por_cancelar": 0.0,
        "balance": 0.0,
        "cuota_esperada": 0.0,
        "next_installment_amount": 0.0,
        "monto_vencido": 0,
        "cuotas_vencidas": 0,
    }
    profile = dds._row_to_profile(row)
    assert profile["cuenta_bancaria"] == "8983479916944", (
        f"Float 8983479916944.0 must become '8983479916944', got {profile['cuenta_bancaria']!r}"
    )
    assert "." not in profile["cuenta_bancaria"], "No decimal point in formatted account number"
    assert "E" not in profile["cuenta_bancaria"].upper(), "No scientific notation in account number"


def test_row_to_profile_cuenta_bancaria_none_returns_none():
    """None/empty cuenta_bancaria must produce None in profile, not crash."""
    row = {
        "account_id": "P00001",
        "loan_number": "P00001",
        "borrower_name": "Test",
        "dni": "11111111",
        "email": "",
        "phone": "",
        "currency": "SOLES",
        "days_overdue": 0,
        "next_due_date": None,
        "banco": "BCP",
        "cci": "00389801347991694443",
        "cuenta_bancaria": None,
        "inversionista": "FONDO A",
        "saldo_por_cancelar": 0.0,
        "balance": 0.0,
        "cuota_esperada": 0.0,
        "next_installment_amount": 0.0,
        "monto_vencido": 0,
        "cuotas_vencidas": 0,
    }
    profile = dds._row_to_profile(row)
    assert profile["cuenta_bancaria"] is None, (
        f"None cuenta_bancaria must produce None, got {profile['cuenta_bancaria']!r}"
    )


def test_row_to_profile_cci_with_leading_zeros_is_unchanged():
    """cci with leading zeros '00389801347991694443' must NOT be altered.

    cci is a string column (not DOUBLE), so it must pass through as-is.
    The id_number cast+format logic must NOT touch cci.
    """
    row = {
        "account_id": "P00002",
        "loan_number": "P00002",
        "borrower_name": "Test",
        "dni": "22222222",
        "email": "",
        "phone": "",
        "currency": "SOLES",
        "days_overdue": 0,
        "next_due_date": None,
        "banco": "BCP",
        "cci": "00389801347991694443",  # 20-digit string with leading zeros
        "cuenta_bancaria": 12345678901234,
        "inversionista": "FONDO A",
        "saldo_por_cancelar": 0.0,
        "balance": 0.0,
        "cuota_esperada": 0.0,
        "next_installment_amount": 0.0,
        "monto_vencido": 0,
        "cuotas_vencidas": 0,
    }
    profile = dds._row_to_profile(row)
    assert profile["cci"] == "00389801347991694443", (
        f"cci must preserve leading zeros '00389801347991694443', "
        f"got {profile['cci']!r}"
    )


def test_end_to_end_resolve_dni_cuenta_bancaria_clean(monkeypatch):
    """resolve_dni must return cuenta_bancaria as clean digit string (not scientific notation)."""
    dds._load_schema.cache_clear()

    # Simulate what Doris returns after DECIMAL cast: large integer
    joined_row = {
        "account_id": "P99999",
        "loan_number": "P99999",
        "borrower_name": "Test Borrower",
        "dni": "44444444",
        "email": "test@prestamype.com",
        "phone": "999000000",
        "currency": "SOLES",
        "days_overdue": 10,
        "next_due_date": None,
        "banco": "BCP",
        "cci": "00389801347991694443",
        "cuenta_bancaria": 8983479916944,  # integer from DECIMAL(38,0) cast
        "inversionista": "FONDO B",
        "saldo_por_cancelar": 12500.00,
        "balance": 12500.00,
        "cuota_esperada": 1500.00,
        "next_installment_amount": 1500.00,
        "monto_vencido": 3000.00,
        "cuotas_vencidas": 2,
    }

    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchall.return_value = [joined_row]
    conn = MagicMock()
    conn.cursor.return_value = cur
    monkeypatch.setattr(dds, "_connect", lambda db: conn)

    prof = dds.resolve_dni("44444444", tenant_id=TENANT)
    assert prof is not None
    cb = prof["cuenta_bancaria"]
    assert cb == "8983479916944", (
        f"cuenta_bancaria must be clean digit string '8983479916944', got {cb!r}"
    )
    assert "E" not in str(cb).upper(), "No scientific notation"
    assert "." not in str(cb), "No decimal point"
    # cci must be unchanged
    assert prof["cci"] == "00389801347991694443", "cci must preserve leading zeros"
