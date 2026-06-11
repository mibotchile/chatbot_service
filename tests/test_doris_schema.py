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


# ── role_column / authorized_roles from config ───────────────────────────────


def test_get_role_column_reads_from_schema():
    """role_column is read from doris_schema config."""
    schema = _prestamype_schema()
    assert schema["role_column"] == "posicion_contractual"
    result = dds._get_role_column(schema)
    assert result == "posicion_contractual"


def test_get_role_column_fallback_when_missing():
    """Missing role_column triggers a loguru warning and falls back to 'posicion_contractual'."""
    from loguru import logger

    warnings: list[str] = []

    def _sink(msg):
        if msg.record["level"].name == "WARNING":
            warnings.append(msg.record["message"])

    sid = logger.add(_sink, level="WARNING", format="{message}")
    try:
        schema = {k: v for k, v in _prestamype_schema().items() if k != "role_column"}
        result = dds._get_role_column(schema)
    finally:
        logger.remove(sid)

    assert result == "posicion_contractual"
    assert any("role_column" in w and "falling back" in w for w in warnings)


def test_get_authorized_roles_reads_from_schema():
    """authorized_roles is read from doris_schema config and returned as frozenset."""
    schema = _prestamype_schema()
    roles = dds._get_authorized_roles(schema)
    assert roles == frozenset({"SOLICITANTE", "GARANTE", "FIADOR SOLIDARIO"})


def test_get_authorized_roles_fallback_when_missing():
    """Missing authorized_roles triggers a loguru warning and returns the built-in defaults."""
    from loguru import logger

    warnings: list[str] = []

    def _sink(msg):
        if msg.record["level"].name == "WARNING":
            warnings.append(msg.record["message"])

    sid = logger.add(_sink, level="WARNING", format="{message}")
    try:
        schema = {k: v for k, v in _prestamype_schema().items() if k != "authorized_roles"}
        roles = dds._get_authorized_roles(schema)
    finally:
        logger.remove(sid)

    assert roles == dds._AUTHORIZED_ROLES_DEFAULT
    assert any("authorized_roles" in w and "falling back" in w for w in warnings)


def test_get_authorized_roles_custom_set():
    """A custom authorized_roles list produces the expected frozenset (uppercased)."""
    schema = dict(_prestamype_schema())
    schema["authorized_roles"] = ["titular", "aval"]
    roles = dds._get_authorized_roles(schema)
    assert roles == frozenset({"TITULAR", "AVAL"})


# ── cronograma_columns SQL mapping ───────────────────────────────────────────


def test_get_cronograma_sql_uses_configured_columns(monkeypatch):
    """get_cronograma builds SQL with column names from cronograma_columns config."""

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            self._sql = sql
            self._params = params

        def fetchall(self):
            return []

    _cur = _Cursor()

    class _Conn:
        def cursor(self):
            return _cur

        def close(self):
            pass

    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_connect", lambda db: _Conn())

    dds.get_cronograma("P02137", tenant_id=TENANT)

    sql = _cur._sql
    # Configured column names must appear in the SQL.
    assert "nro_cuotas" in sql          # cronograma_columns.n_cuota
    assert "fecha_de_pago_esperada_original" in sql  # cronograma_columns.fecha_venc
    assert "cuota_esperada_mensual" in sql            # cronograma_columns.monto
    assert "fecha_de_pago_del_cliente" in sql         # cronograma_columns.fecha_pago
    # Aliased to canonical names the caller uses.
    assert "AS n_cuota" in sql
    assert "AS fecha_venc" in sql
    assert "AS monto" in sql
    assert "AS fecha_pago_cliente" in sql  # row.get("fecha_pago_cliente") depends on it
    # account_id is parameterized.
    assert "%s" in sql
    assert "P02137" not in sql


def test_get_cronograma_sql_custom_columns(monkeypatch):
    """Custom cronograma_columns produce different SQL identifiers."""

    captured = {}

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            captured["sql"] = sql

        def fetchall(self):
            return []

    class _Conn:
        def cursor(self):
            return _Cursor()

        def close(self):
            pass

    # Patch _load_schema to return a schema with custom column names.
    original_schema = _prestamype_schema()
    custom_schema = dict(original_schema)
    custom_schema["cronograma_columns"] = {
        "n_cuota": "numero_cuota",
        "fecha_venc": "fecha_vencimiento",
        "monto": "monto_cuota",
        "fecha_pago": "fecha_pago_real",
    }
    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_load_schema", lambda tid: custom_schema)
    monkeypatch.setattr(dds, "_connect", lambda db: _Conn())

    dds.get_cronograma("P02137", tenant_id=TENANT)

    sql = captured["sql"]
    assert "numero_cuota" in sql
    assert "fecha_vencimiento" in sql
    assert "monto_cuota" in sql
    assert "fecha_pago_real" in sql
    # Old hardcoded names must NOT appear.
    assert "nro_cuotas" not in sql
    assert "fecha_de_pago_esperada_original" not in sql


def test_get_cronograma_fallback_warns_when_columns_missing(monkeypatch):
    """Missing cronograma_columns emits a loguru warning and uses built-in defaults."""
    from loguru import logger

    original_schema = _prestamype_schema()
    schema_no_cron = {k: v for k, v in original_schema.items() if k != "cronograma_columns"}

    captured: dict = {}
    warnings: list[str] = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            captured["sql"] = sql

        def fetchall(self):
            return []

    class _Conn:
        def cursor(self):
            return _Cursor()

        def close(self):
            pass

    def _sink(msg):
        if msg.record["level"].name == "WARNING":
            warnings.append(msg.record["message"])

    sid = logger.add(_sink, level="WARNING", format="{message}")
    try:
        dds._load_schema.cache_clear()
        dds._warned_missing_cronograma_cols.clear()  # reset once-per-tenant guard
        monkeypatch.setattr(dds, "_load_schema", lambda tid: schema_no_cron)
        monkeypatch.setattr(dds, "_connect", lambda db: _Conn())
        dds.get_cronograma("P02137", tenant_id=TENANT)
        # Second call must NOT warn again (once-per-tenant guard).
        dds.get_cronograma("P02137", tenant_id=TENANT)
    finally:
        logger.remove(sid)
        dds._warned_missing_cronograma_cols.clear()

    cron_warnings = [w for w in warnings if "cronograma_columns" in w]
    assert len(cron_warnings) == 1
    assert "falling back" in cron_warnings[0]
    # Fallback defaults must be present in SQL.
    assert "nro_cuotas" in captured["sql"]


# ── moratoria_columns SQL mapping ────────────────────────────────────────────


def test_get_moratoria_fields_sql_uses_configured_columns(monkeypatch):
    """get_moratoria_fields builds SQL with column names from moratoria_columns config."""

    captured = {}

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            captured["sql"] = sql

        def fetchone(self):
            return None  # triggers _empty return path — we only care about the SQL

    class _Conn:
        def cursor(self):
            return _Cursor()

        def close(self):
            pass

    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_connect", lambda db: _Conn())

    dds.get_moratoria_fields("P02137", tenant_id=TENANT)

    sql = captured["sql"]
    # Configured column names from moratoria_columns and cronograma_columns.
    assert "amortizacion_esperada_original" in sql   # moratoria_columns.amortizacion_cuota
    assert "tasa_de_interes" in sql                  # moratoria_columns.tasa_interes
    # cronograma_columns.fecha_pago used in IS NULL filter
    assert "fecha_de_pago_del_cliente" in sql
    assert "nro_cuotas" in sql                       # cronograma_columns.n_cuota (ORDER BY)
    # account_id is parameterized (appears twice — once for DISTINCT subquery, once for WHERE).
    assert sql.count("%s") == 2
    assert "P02137" not in sql


def test_get_moratoria_fields_fallback_warns_when_columns_missing(monkeypatch):
    """Missing moratoria_columns emits a loguru warning and uses built-in defaults."""
    from loguru import logger

    original_schema = _prestamype_schema()
    schema_no_mort = {k: v for k, v in original_schema.items() if k != "moratoria_columns"}

    captured: dict = {}
    warnings: list[str] = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            captured["sql"] = sql

        def fetchone(self):
            return None

    class _Conn:
        def cursor(self):
            return _Cursor()

        def close(self):
            pass

    def _sink(msg):
        if msg.record["level"].name == "WARNING":
            warnings.append(msg.record["message"])

    sid = logger.add(_sink, level="WARNING", format="{message}")
    try:
        dds._load_schema.cache_clear()
        monkeypatch.setattr(dds, "_load_schema", lambda tid: schema_no_mort)
        monkeypatch.setattr(dds, "_connect", lambda db: _Conn())
        dds.get_moratoria_fields("P02137", tenant_id=TENANT)
    finally:
        logger.remove(sid)

    assert any("moratoria_columns" in w and "falling back" in w for w in warnings)
    assert "amortizacion_esperada_original" in captured["sql"]


# ── _query_contrato_rows uses config role_column + parameterized IN-list ─────


def test_query_contrato_rows_sql_uses_role_column_from_config(monkeypatch):
    """_query_contrato_rows SQL uses role_column from config (not hardcoded)."""

    captured = {}

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        def fetchall(self):
            return []

    class _Conn:
        def cursor(self):
            return _Cursor()

        def close(self):
            pass

    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_connect", lambda db: _Conn())

    dds._query_contrato_rows("C001", tenant_id=TENANT)

    sql = captured["sql"]
    params = captured["params"]

    # role_column from config must appear in PARTITION BY and WHERE.
    assert "posicion_contractual" in sql
    # Roles must be parameterized — NOT interpolated as string literals in SQL.
    assert "SOLICITANTE" not in sql
    assert "GARANTE" not in sql
    assert "FIADOR SOLIDARIO" not in sql
    # Role values appear as bound params (after contrato_id).
    assert "SOLICITANTE" in params
    assert "GARANTE" in params
    assert "FIADOR SOLIDARIO" in params
    # contrato_id is the first param.
    assert params[0] == "C001"
    # %s placeholders for all roles.
    assert sql.count("%s") == 1 + 3  # contrato_id + 3 roles


def test_query_contrato_rows_custom_role_column_and_roles(monkeypatch):
    """Custom role_column and authorized_roles produce different SQL + params."""

    captured = {}

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        def fetchall(self):
            return []

    class _Conn:
        def cursor(self):
            return _Cursor()

        def close(self):
            pass

    original_schema = _prestamype_schema()
    custom_schema = dict(original_schema)
    custom_schema["role_column"] = "tipo_vinculo"
    custom_schema["authorized_roles"] = ["TITULAR", "AVAL"]

    dds._load_schema.cache_clear()
    monkeypatch.setattr(dds, "_load_schema", lambda tid: custom_schema)
    monkeypatch.setattr(dds, "_connect", lambda db: _Conn())

    dds._query_contrato_rows("C002", tenant_id=TENANT)

    sql = captured["sql"]
    params = captured["params"]

    # Custom role_column.
    assert "tipo_vinculo" in sql
    assert "posicion_contractual" not in sql
    # Custom roles as params, not literals.
    assert "TITULAR" not in sql
    assert "AVAL" not in sql
    assert "TITULAR" in params
    assert "AVAL" in params
    assert params[0] == "C002"
    assert sql.count("%s") == 1 + 2  # contrato_id + 2 roles


# ── profile SQL aggregates use cronograma_columns ────────────────────────────


def test_build_sql_aggregates_use_cronograma_columns():
    """days_overdue + pagos_agg aggregates use column names from cronograma_columns."""
    schema = _prestamype_schema()
    sql, _db = dds._build_sql(schema)

    # days_overdue derivation uses cronograma_columns.fecha_venc.
    assert "GREATEST(DATEDIFF(CURDATE(), p.fecha_de_pago_esperada_original), 0)" in sql
    # Aggregate block uses fecha_pago / fecha_venc / monto / n_cuota from config.
    assert "fecha_de_pago_del_cliente IS NULL" in sql
    assert "THEN cuota_esperada_mensual ELSE 0 END) AS monto_vencido" in sql
    assert "MAX(fecha_de_pago_esperada_original) AS fecha_venc_contrato" in sql
    assert "MIN(fecha_de_pago_esperada_original) AS fecha_inicio_prestamo" in sql
    assert "MAX(nro_cuotas) AS plazo" in sql


def test_build_sql_aggregates_custom_cronograma_columns():
    """Custom cronograma_columns change the aggregate SQL identifiers."""
    schema = json.loads(json.dumps(_prestamype_schema()))  # deep copy
    schema["cronograma_columns"] = {
        "n_cuota": "numero_cuota",
        "fecha_venc": "fecha_vencimiento",
        "monto": "monto_cuota",
        "fecha_pago": "fecha_pago_real",
    }
    sql, _db = dds._build_sql(schema)

    assert "GREATEST(DATEDIFF(CURDATE(), p.fecha_vencimiento), 0)" in sql
    assert "SUM(CASE WHEN fecha_pago_real IS NULL" in sql
    assert "THEN monto_cuota ELSE 0 END) AS monto_vencido" in sql
    assert "MAX(fecha_vencimiento) AS fecha_venc_contrato" in sql
    assert "MAX(numero_cuota) AS plazo" in sql
    # Derived/aggregate fragments must not carry the old hardcoded names.
    # (pagos_selection.order_by still references raw columns verbatim — that is
    # its own tenant-owned config block, not a hardcoded literal in code.)
    assert "MAX(nro_cuotas)" not in sql
    assert "SUM(CASE WHEN fecha_de_pago_del_cliente" not in sql
    assert "GREATEST(DATEDIFF(CURDATE(), p.fecha_de_pago_esperada_original" not in sql


def test_build_sql_aggregates_fallback_identical_when_cronograma_missing():
    """Regression: schema WITHOUT cronograma_columns builds string-identical SQL
    (fallback defaults == prestamype's configured values)."""
    schema_with = _prestamype_schema()
    schema_without = {
        k: v for k, v in _prestamype_schema().items() if k != "cronograma_columns"
    }

    dds._warned_missing_cronograma_cols.clear()
    try:
        sql_with, _ = dds._build_sql(schema_with)
        sql_without, _ = dds._build_sql(schema_without)
    finally:
        dds._warned_missing_cronograma_cols.clear()

    assert sql_with == sql_without
