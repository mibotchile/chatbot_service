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
    """prestamype now uses window CTEs (pagos_selection + batch_selection declared).

    Updated to the new contract: ROW_NUMBER() OVER CTEs replace GROUP BY / MAX.
    The old MAX assertions are replaced by the window-CTE assertions.
    """
    schema = _prestamype_schema()
    sql, db = dds._build_sql(schema)
    # db falls back to settings.doris_db when config db is null.
    assert db.startswith("project_")
    # Tables present in the CTE definitions (not as direct FROM aliases).
    assert "batch_asignacion_review_bronze" in sql
    assert "batch_pagos_v2_bronze" in sql
    # Join condition from config.
    assert "p.codigo_contrato = a.id_credito" in sql
    # DNI is parameterized, never interpolated.
    assert "%s" in sql
    assert "44218903" not in sql
    # Window CTEs with ROW_NUMBER (new contract — replaces GROUP BY / MAX).
    assert "ROW_NUMBER() OVER" in sql
    assert "PARTITION BY" in sql
    assert "p.rn = 1" in sql
    assert "a.rn = 1" in sql
    # COALESCE for cuota (replaces MAX).
    assert "COALESCE" in sql.upper()
    assert "cuota_esperada_actualizada" in sql
    assert "cuota_esperada_mensual" in sql
    # days_overdue is derived.
    assert "DATEDIFF" in sql.upper()
    # MAX aggregate must NOT appear for pagos balance/cuota fields.
    assert "MAX(p.saldo_por_cancelar)" not in sql
    assert "MAX(p.cuota_esperada" not in sql


def test_build_sql_db_override_from_config():
    """DB name override is reflected in both CTEs (window strategy)."""
    schema = dict(_prestamype_schema())
    schema["db"] = "project_OTHER123"
    sql, db = dds._build_sql(schema)
    assert db == "project_OTHER123"
    # Both tables are in CTEs and must use the overridden DB.
    assert "project_OTHER123.batch_asignacion_review_bronze" in sql
    assert "project_OTHER123.batch_pagos_v2_bronze" in sql


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
    """Legacy schema (no window blocks) with a malicious agg value must raise ValueError.

    Updated to use a minimal legacy schema because the prestamype schema no longer
    uses ``agg`` (it uses ``coalesce`` instead). The security guard lives in
    _build_sql_legacy and must still fire when a legacy schema has a bad ``agg``.
    """
    legacy_schema = {
        "db": "project_TEST",
        "debt_table": "asig_table",
        "pagos_table": "pagos_table",
        "join": {"debt_key": "id_credito", "pagos_key": "codigo_contrato"},
        "dni_column": "dni_ruc",
        # No pagos_selection / batch_selection → legacy path
        "column_map": {
            "account_id": {"source": "debt", "column": "id_credito"},
            "cuota_esperada": {
                "source": "pagos",
                "column": "cuota_col",
                "agg": "MAX(x)) UNION SELECT",  # malicious
            },
        },
    }
    with pytest.raises(ValueError):
        dds._build_sql(legacy_schema)


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
#
# Updated for window-CTE schema (Slice A 2026-06-04):
#   - principal_original removed (capital col dropped from column_map).
#   - cuenta_bancaria added (numero_de_cuenta col).
#   - days_overdue is SQL-derived (GREATEST(DATEDIFF(...),0)) — already an int.
_DORIS_ROW = {
    "account_id": "P02137",
    "loan_number": "P02137",
    "borrower_name": "CARLOS ANTONIO MENDOZA RIVERA",
    "dni": "44218903",
    "email": "cmendoza.demo@example.com",
    "phone": 951000111,           # Doris may return an int — coerced to str
    "days_overdue": 0,            # SQL-derived (DATEDIFF); 0 = credit al_dia
    "next_due_date": "2026-04-18",
    "currency": "SOLES",          # raw Doris value, mapped to PEN/S/
    "banco": "INTERBANK",
    "cci": "00389801338381007048",
    "cuenta_bancaria": "12300001234",  # numero_de_cuenta (new Slice A field)
    "inversionista": "INVERSIONISTA DEMO UNO",
    "cuota_esperada": 462.14,
    "next_installment_amount": 462.14,
    "saldo_por_cancelar": 23800.0,
    "balance": 23800.0,
}


def test_row_to_profile_matches_legacy_prestamype_shape(monkeypatch):
    """The config-driven mapping produces the expected profile shape.

    Updated for the window-CTE schema (Slice A):
      - principal_original removed (capital dropped from column_map per spec).
      - cuenta_bancaria added (numero_de_cuenta from pagos).
      - days_overdue is SQL-derived; _row_to_profile reads it from the row as-is.
    """

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

    # Expected profile shape after Slice A (window-CTE schema).
    expected = {
        "account_id": "P02137",
        "loan_number": "P02137",
        "borrower_name": "CARLOS ANTONIO MENDOZA RIVERA",
        "dni": "44218903",
        "email": "cmendoza.demo@example.com",
        "phone": "951000111",
        "currency": "PEN",
        "currency_symbol": "S/",
        "balance": 23800.0,
        "next_due_date": "2026-04-18",
        "next_installment_amount": 462.14,
        "days_overdue": 0,
        "status": "al_dia",
        "status_label": "Al día",
        "cci": "00389801338381007048",
        "cuenta_bancaria": "12300001234",
        "banco": "INTERBANK",
        "inversionista": "INVERSIONISTA DEMO UNO",
        "cuota_esperada": 462.14,
        "saldo_por_cancelar": 23800.0,
        # Slice E: overdue aggregates always present (0 for al-día borrowers).
        "monto_vencido": 0.0,
        "cuotas_vencidas": 0,
        # Phase 3 additions (INF-02, INF-03): present but None when not in Doris row.
        "cuotas_pagadas": None,
        "cuotas_pendientes": None,
        "fecha_venc_contrato": None,
        # Phase 8 additions (MCD-01): per-credit fields; None when absent from row.
        # cuenta_bancaria is sourced from the row here (mapped above), so it keeps
        # the mapped value, not None.
        "valor_cuota": None,
        "plazo": None,
        "fecha_vencimiento_contrato": None,
        "fecha_inicio_prestamo": None,
    }
    for key, value in expected.items():
        assert prof[key] == value, f"{key}: {prof.get(key)!r} != {value!r}"
    # Key set now includes Phase 3 + Phase 8 additions.
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
