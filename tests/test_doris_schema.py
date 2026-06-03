"""Tests for the GENERIC, config-driven Doris debt source (doris_schema).

The module no longer hardcodes any tenant schema — it builds the SQL and the
row→profile mapping from the ``doris_schema`` block in the tenant config. These
tests verify:
  - the SQL is built from the config (tables, join, columns, db);
  - identifiers are whitelist-sanitized (corrupt config → ValueError, no SQL
    injection);
  - a tenant with data_source 'doris' but no doris_schema errors clearly;
  - prestamype produces the SAME profile it produced when the schema was
    hardcoded (regression guard) — pymysql is mocked, no live Doris.

NONE of these touch a live Doris instance.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from features.cobranza import doris_debt_source as dds

TENANT = "prestamype"


def _prestamype_schema() -> dict:
    root = Path(__file__).resolve().parent.parent / "tenants" / TENANT / "tenant.config.json"
    return json.loads(root.read_text(encoding="utf-8"))["doris_schema"]


# ── SQL build comes from the config ─────────────────────────────────────────


def test_build_sql_uses_config_tables_join_and_columns():
    schema = _prestamype_schema()
    sql, db = dds._build_sql(schema)
    # db falls back to settings.doris_db when config db is null.
    assert db.startswith("project_")
    # Tables + join from config (NOT hardcoded constants).
    assert "batch_asignacion_review_bronze a" in sql
    assert "batch_pagos_v2_bronze p" in sql
    assert "ON p.codigo_contrato = a.id_credito" in sql
    # DNI is parameterized, never interpolated.
    assert "WHERE a.dni_ruc = %s" in sql
    assert "44218903" not in sql
    # Aggregated pagos columns are aliased to their profile field names.
    assert "MAX(p.cuota_esperada_actualizada) AS cuota_esperada" in sql
    assert "MAX(p.saldo_por_cancelar) AS saldo_por_cancelar" in sql
    assert "MAX(p.saldo_por_cancelar) AS balance" in sql
    # Non-aggregated debt columns appear in GROUP BY.
    assert "GROUP BY" in sql
    assert "a.id_credito" in sql.split("GROUP BY")[1]


def test_build_sql_db_override_from_config():
    schema = dict(_prestamype_schema())
    schema["db"] = "project_OTHER123"
    sql, db = dds._build_sql(schema)
    assert db == "project_OTHER123"
    assert "project_OTHER123.batch_asignacion_review_bronze a" in sql


# ── Identifier sanitization (anti-injection on corrupt config) ───────────────


@pytest.mark.parametrize(
    "field,bad",
    [
        ("debt_table", "users; DROP TABLE x"),
        ("debt_table", "tbl WHERE 1=1"),
        ("pagos_table", "a.b"),
        ("dni_column", "dni-ruc"),
        ("db", "db`name"),
    ],
)
def test_build_sql_rejects_malicious_identifiers(field, bad):
    schema = dict(_prestamype_schema())
    schema = json.loads(json.dumps(schema))  # deep copy
    schema[field] = bad
    with pytest.raises(ValueError):
        dds._build_sql(schema)


def test_build_sql_rejects_malicious_column_name():
    schema = json.loads(json.dumps(_prestamype_schema()))
    schema["column_map"]["banco"]["column"] = "banco); DELETE FROM x;--"
    with pytest.raises(ValueError):
        dds._build_sql(schema)


def test_build_sql_rejects_malicious_agg():
    schema = json.loads(json.dumps(_prestamype_schema()))
    schema["column_map"]["cuota_esperada"]["agg"] = "MAX(x)) UNION SELECT"
    with pytest.raises(ValueError):
        dds._build_sql(schema)


# ── Missing schema → clear error ─────────────────────────────────────────────


def test_doris_tenant_without_schema_errors(tmp_path):
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "tenant.config.json").write_text(
        json.dumps({"id": "broken", "data_source": "doris"}), encoding="utf-8"
    )
    dds._load_schema.cache_clear()
    orig = dds._tenants_root
    dds._tenants_root = lambda: tmp_path  # type: ignore[assignment]
    try:
        with pytest.raises(ValueError, match="no 'doris_schema'"):
            dds._load_schema("broken")
    finally:
        dds._tenants_root = orig  # type: ignore[assignment]
        dds._load_schema.cache_clear()


# ── Regression: prestamype maps a Doris row to the SAME profile as before ─────

# A raw Doris result row. The query aliases every selected column to its profile
# field name (``... AS account_id``), so the cursor returns rows keyed by the
# profile field names. This is the row for DNI 44218903 (credit P02137).
_DORIS_ROW = {
    "account_id": "P02137",
    "loan_number": "P02137",
    "borrower_name": "CARLOS ANTONIO MENDOZA RIVERA",
    "dni": "44218903",
    "email": "cmendoza.demo@example.com",
    "phone": 951000111,           # Doris may return an int — coerced to str
    "principal_original": 23800.0,
    "days_overdue": 0,
    "next_due_date": "2026-04-18",
    "currency": "SOLES",          # raw Doris value, mapped to PEN/S/
    "banco": "INTERBANK",
    "cci": "00389801338381007048",
    "inversionista": "INVERSIONISTA DEMO UNO",
    "cuota_esperada": 462.14,
    "next_installment_amount": 462.14,
    "saldo_por_cancelar": 23800.0,
    "balance": 23800.0,
}


def test_row_to_profile_matches_legacy_prestamype_shape(monkeypatch):
    """The config-driven mapping reproduces the previously-hardcoded profile."""

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            # The DNI must arrive as a bound param, never in the SQL text.
            assert params == ("44218903",)
            assert "44218903" not in sql

        def fetchall(self):
            return [_DORIS_ROW]

    class _Conn:
        def cursor(self):
            return _Cursor()

        def close(self):
            pass

    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_connect", lambda db: _Conn())

    prof = dds.resolve_dni("44218903", tenant_id=TENANT)
    assert prof is not None

    # Same values the hardcoded implementation produced.
    expected = {
        "account_id": "P02137",
        "loan_number": "P02137",
        "borrower_name": "CARLOS ANTONIO MENDOZA RIVERA",
        "dni": "44218903",
        "email": "cmendoza.demo@example.com",
        "phone": "951000111",
        "currency": "PEN",
        "currency_symbol": "S/",
        "principal_original": 23800.0,
        "balance": 23800.0,
        "next_due_date": "2026-04-18",
        "next_installment_amount": 462.14,
        "days_overdue": 0,
        "status": "al_dia",
        "status_label": "Al día",
        "cci": "00389801338381007048",
        "banco": "INTERBANK",
        "inversionista": "INVERSIONISTA DEMO UNO",
        "cuota_esperada": 462.14,
        "saldo_por_cancelar": 23800.0,
    }
    for key, value in expected.items():
        assert prof[key] == value, f"{key}: {prof.get(key)!r} != {value!r}"
    # Exact same key set as the legacy hardcoded profile — no extra fields leak.
    assert set(prof.keys()) == set(expected.keys())


def test_validate_comprobante_uses_mapped_fields(monkeypatch):
    """Classification (pago/abono/cancelación) works off the mapped fields."""

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            pass

        def fetchall(self):
            return [_DORIS_ROW]

    class _Conn:
        def cursor(self):
            return _Cursor()

        def close(self):
            pass

    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_connect", lambda db: _Conn())

    # cuota 462.14 → pago; saldo 23800 → cancelacion; below cuota → abono.
    pago = dds.validate_comprobante("44218903", cci="x", monto=462.14, nro_operacion="OP-1", tenant_id=TENANT)
    assert pago["cuenta_valida"] is True
    assert pago["credito"] == "P02137"
    assert pago["tipo"] == "pago"
    assert pago["cuota_esperada"] == 462.14
    assert pago["saldo_por_cancelar"] == 23800.0

    cancel = dds.validate_comprobante("44218903", cci="x", monto=23800.0, nro_operacion="OP-2", tenant_id=TENANT)
    assert cancel["tipo"] == "cancelacion"

    abono = dds.validate_comprobante("44218903", cci="x", monto=100.0, nro_operacion="OP-3", tenant_id=TENANT)
    assert abono["tipo"] == "abono"
