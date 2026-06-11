"""Generic Doris debt source — schema comes from the tenant config.

Same interface as ``mock_debt_source`` (``resolve_token`` / ``resolve_dni``)
returning the standard borrower *profile dict*, so the existing
``consultar_deuda`` tool works unchanged. Adds whatever extra profile fields the
tenant declares (e.g. PrestamYpe's ``cci``, ``banco``, ``inversionista``,
``cuota_esperada``, ``saldo_por_cancelar``) and a ``validate_comprobante``
helper used by the ``validar_comprobante`` tool.

The module is TENANT-AGNOSTIC: the SQL and the row→profile mapping are built at
runtime from a ``doris_schema`` block in the tenant's ``tenant.config.json``.
See ``_load_schema`` for the expected format.

Data lives in Apache Doris (MySQL wire protocol). On ANY connection/query error
this module falls back to the tenant's seeded fixture
(``tenants/<tenant_id>/mock/borrowers.json``) so the demo never breaks
(FALLBACK CONTRACT — non-negotiable).

SECURITY: table/column identifiers come from the (trusted) config but are
whitelist-sanitized (``^[A-Za-z0-9_]+$``) before interpolation, so a corrupt
config can never inject SQL. The DNI *value* is always bound as a parameter
(``%s``), never interpolated.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from shared.config.settings import settings
from shared.debt_math import classify_tipo
from features.cobranza import mock_debt_source

# Profile fields that must be coerced to float when mapped from Doris.
_NUMERIC_FIELDS = frozenset(
    {
        "principal_original",
        "balance",
        "next_installment_amount",
        "cuota_esperada",
        "saldo_por_cancelar",
        "monto_pagado",
    }
)
_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")


# ── Schema loading + SQL build ──────────────────────────────────────────────


def _tenants_root() -> Path:
    """Locate the tenants/ directory in both Docker and local-dev layouts."""
    docker_path = Path("/app/tenants")
    if docker_path.exists():
        return docker_path
    # apps/agent/features/cobranza/ -> repo root -> tenants/
    return Path(__file__).resolve().parent.parent.parent.parent.parent / "tenants"


def _safe_ident(value: str, *, what: str) -> str:
    """Whitelist-validate a SQL identifier from config. Raise on anything weird."""
    if not isinstance(value, str) or not _IDENT_RE.match(value):
        raise ValueError(
            f"doris_schema: invalid {what} identifier {value!r} "
            "(must match ^[A-Za-z0-9_]+$)"
        )
    return value


@lru_cache(maxsize=16)
def _load_schema(tenant_id: str) -> dict:
    """Read + validate the ``doris_schema`` block for a tenant.

    Raises a clear error when the tenant declares ``data_source: "doris"`` but
    has no ``doris_schema`` (or it's malformed). The result is cached per tenant.
    """
    path = _tenants_root() / tenant_id / "tenant.config.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"doris_debt_source: cannot read tenant config for {tenant_id!r}: {exc}"
        ) from exc

    schema = config.get("doris_schema")
    if not isinstance(schema, dict) or not schema:
        raise ValueError(
            f"doris_debt_source: tenant {tenant_id!r} has data_source 'doris' but "
            "no 'doris_schema' block in tenant.config.json"
        )
    return schema


def _build_sql(schema: dict) -> tuple[str, str]:
    """Build the (parameterized) profile SQL + the DB name from a schema dict.

    Returns ``(sql, db)``. All identifiers are whitelist-sanitized. The DNI value
    is a ``%s`` placeholder — never interpolated.
    """
    db = _safe_ident(schema.get("db") or settings.doris_db, what="db")
    debt = _safe_ident(schema["debt_table"], what="debt_table")
    pagos = _safe_ident(schema["pagos_table"], what="pagos_table")
    join = schema["join"]
    debt_key = _safe_ident(join["debt_key"], what="join.debt_key")
    pagos_key = _safe_ident(join["pagos_key"], what="join.pagos_key")
    dni_col = _safe_ident(schema["dni_column"], what="dni_column")

    column_map: dict = schema["column_map"]
    if not isinstance(column_map, dict) or not column_map:
        raise ValueError("doris_schema: 'column_map' must be a non-empty object")

    select_parts: list[str] = []
    group_parts: list[str] = []
    for field, spec in column_map.items():
        _safe_ident(field, what=f"column_map key {field!r}")
        source = spec.get("source", "debt")
        col = _safe_ident(spec["column"], what=f"column_map[{field}].column")
        alias = "a" if source == "debt" else "p"
        agg = spec.get("agg")
        if agg:
            agg = _safe_ident(agg, what=f"column_map[{field}].agg").upper()
            select_parts.append(f"{agg}({alias}.{col}) AS {field}")
        else:
            select_parts.append(f"{alias}.{col} AS {field}")
            group_parts.append(f"{alias}.{col}")

    if not group_parts:
        raise ValueError(
            "doris_schema: at least one non-aggregated column is required "
            "(GROUP BY would be empty)"
        )

    select_clause = ",\n  ".join(select_parts)
    group_clause = ", ".join(group_parts)
    sql = (
        f"SELECT\n  {select_clause}\n"
        f"FROM {db}.{debt} a\n"
        f"JOIN {db}.{pagos} p\n"
        f"  ON p.{pagos_key} = a.{debt_key}\n"
        f"WHERE a.{dni_col} = %s\n"
        f"GROUP BY {group_clause}\n"
        f"ORDER BY days_overdue DESC"
    )
    return sql, db


def _normalize_dni(dni: str) -> str:
    """Keep only digits — tolerates spaces/dots the user might type."""
    return re.sub(r"\D", "", dni or "")


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _currency(moneda: str) -> tuple[str, str]:
    """Map Doris ``moneda`` (SOLES/DOLARES) to (currency_code, symbol)."""
    m = (moneda or "").strip().upper()
    if m.startswith("DOLAR") or m in ("USD", "US$"):
        return "USD", "US$"
    return "PEN", "S/"


def _row_to_profile(row: dict) -> dict:
    """Map a Doris result row (keyed by profile field) to a borrower profile.

    The query already aliases every column to its profile field name, so this is
    a thin coercion + derivation layer: currency code/symbol, status from
    ``days_overdue``, numeric coercion. Any extra mapped fields pass through.
    """
    code, sym = _currency(row.get("currency"))
    dias_mora = int(_to_float(row.get("days_overdue")))

    profile: dict = {}
    for key, value in row.items():
        if key in _NUMERIC_FIELDS:
            profile[key] = _to_float(value)
        else:
            profile[key] = value

    profile["currency"] = code
    profile["currency_symbol"] = sym
    profile["days_overdue"] = dias_mora
    profile["next_due_date"] = str(row.get("next_due_date") or "") or None
    profile["phone"] = str(row.get("phone") or "")
    profile["email"] = row.get("email") or ""
    profile["status"] = "al_dia" if dias_mora == 0 else "en_mora"
    profile["status_label"] = "Al día" if dias_mora == 0 else "En mora"

    # Phase 3 — INF-03 / INF-02: cuotas counts + contract end date.
    # These come from the pagos table aggregates; they may be pre-mapped in the
    # column_map (future-proof) or computed here from the raw counts when absent.
    cuotas_pagadas = row.get("cuotas_pagadas")
    cuotas_pendientes = row.get("cuotas_pendientes")
    fecha_venc_contrato = row.get("fecha_venc_contrato")

    profile["cuotas_pagadas"] = (
        int(_to_float(cuotas_pagadas)) if cuotas_pagadas is not None else None
    )
    profile["cuotas_pendientes"] = (
        int(_to_float(cuotas_pendientes)) if cuotas_pendientes is not None else None
    )
    profile["fecha_venc_contrato"] = str(fecha_venc_contrato) if fecha_venc_contrato else None

    # Phase 8 — MCD-01: 7 per-credit fields for multi-credit selector.
    # Columns sourced from the verified Doris column names (confirmed 2026-06-10):
    #   valor_cuota         ← cuota_esperada_mensual (pagos)
    #   cuenta_bancaria     ← numero_de_cuenta (asignacion/debt)
    #   cci                 ← codigo_de_cuenta_cci (debt; already mapped as 'cci')
    #   inversionista       ← inversionista (debt; already in column_map)
    #   plazo               ← MAX(nro_cuotas) per contract (pagos)
    #   fecha_vencimiento_contrato ← MAX(fecha_de_pago_esperada_original) (pagos)
    #   fecha_inicio_prestamo      ← MIN(fecha_de_pago_esperada_original) (pagos)
    # All 7 pass through from the column_map; coerce types and normalise here.
    valor_cuota_raw = row.get("valor_cuota")
    profile["valor_cuota"] = _to_float(valor_cuota_raw) if valor_cuota_raw is not None else None

    cuenta_bancaria_raw = row.get("numero_de_cuenta") or row.get("cuenta_bancaria")
    profile["cuenta_bancaria"] = str(cuenta_bancaria_raw) if cuenta_bancaria_raw else None

    # cci is already in profile from the main loop above (mapped as 'cci').
    # Mirror it under 'cuenta_bancaria' alias for legacy callers when absent.

    plazo_raw = row.get("plazo")
    profile["plazo"] = int(_to_float(plazo_raw)) if plazo_raw is not None else None

    fvc_raw = row.get("fecha_vencimiento_contrato")
    profile["fecha_vencimiento_contrato"] = str(fvc_raw) if fvc_raw else None

    fip_raw = row.get("fecha_inicio_prestamo")
    profile["fecha_inicio_prestamo"] = str(fip_raw) if fip_raw else None

    return profile


@lru_cache(maxsize=1)
def _import_pymysql():
    """Lazy import so the dependency is only required when Doris is used."""
    import pymysql  # noqa: PLC0415

    return pymysql


def _connect(db: str):
    """Open a short-lived Doris connection. Raises on failure (caller falls back)."""
    pymysql = _import_pymysql()
    return pymysql.connect(
        host=settings.doris_host,
        port=int(settings.doris_port),
        user=settings.doris_user,
        password=settings.doris_password,
        database=db,
        connect_timeout=5,
        read_timeout=10,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def _query_dni(dni: str, tenant_id: str) -> list[dict]:
    """Run the profile query for a DNI against Doris. May raise — caller catches."""
    schema = _load_schema(tenant_id)
    sql, db = _build_sql(schema)
    conn = _connect(db)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (dni,))
            return list(cur.fetchall())
    finally:
        conn.close()


@lru_cache(maxsize=16)
def _allow_fixture_fallback(tenant_id: str) -> bool:
    """Return the per-tenant fixture-fallback policy (default: False = fail-closed).

    Reads ``allow_fixture_fallback`` from ``tenant.config.json`` directly,
    mirroring the ``_load_schema`` pattern. Result is cached per tenant.

    Cache bleed between tests: callers that toggle this via monkeypatch MUST
    call ``_allow_fixture_fallback.cache_clear()`` before and after each test
    case that modifies the underlying config or patches _tenants_root.
    """
    path = _tenants_root() / tenant_id / "tenant.config.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(config.get("allow_fixture_fallback", False))


def _resolve_dni_credits(dni: str, tenant_id: str) -> list[dict]:
    """Return ALL credit profiles for a DNI (Doris first, fixture fallback).

    Handles multicrédito (a DNI with >1 credit).

    Control flow:
      - Doris OK + rows  → return mapped profiles (fixture NOT consulted)
      - Doris OK + empty → return []              (fixture NOT consulted)
      - Doris raises     → if _allow_fixture_fallback(tenant_id): fixture
                           else: return []  (prod fail-closed)
    """
    norm = _normalize_dni(dni)
    if not norm:
        return []
    try:
        rows = _query_dni(norm, tenant_id)
    except Exception:  # noqa: BLE001 — any driver/connection error
        if _allow_fixture_fallback(tenant_id):
            profile = mock_debt_source.resolve_dni(norm, tenant_id=tenant_id)
            return [profile] if profile else []
        return []
    return [_row_to_profile(r) for r in rows]


# ── Public interface (mirrors mock_debt_source) ────────────────────────────


def resolve_token(token: str, tenant_id: str) -> dict | None:
    """Resolve a demo campaign token to a borrower profile.

    Tokens are a DEMO affordance only — they live in the fixture. We resolve the
    token to its account_id there, then look the borrower up in Doris by DNI so
    the data is live; if Doris is unreachable, the fixture profile is returned.
    """
    if not token:
        return None
    fixture_profile = mock_debt_source.resolve_token(token, tenant_id=tenant_id)
    if not fixture_profile:
        return None
    credits = _resolve_dni_credits(fixture_profile.get("dni", ""), tenant_id=tenant_id)
    if credits:
        # Prefer the credit matching the token's account_id; else first.
        acc = fixture_profile.get("account_id")
        for c in credits:
            if c.get("account_id") == acc:
                return c
        return credits[0]
    # credits is empty: Doris returned no rows (either Doris OK+empty, or
    # Doris raised and _resolve_dni_credits already handled the fallback).
    # Mirror the resolve_dni fail-closed contract: only fall back to the
    # fixture profile when the tenant explicitly allows it.
    if _allow_fixture_fallback(tenant_id):
        return fixture_profile
    return None


def resolve_dni(dni: str, tenant_id: str) -> dict | None:
    """Resolve a DNI/RUC to a single borrower profile (first credit).

    For multicrédito the first (highest mora) credit is returned to keep the
    same single-profile contract as the mock source. Returns ``None`` if not
    found in Doris nor the fixture.
    """
    credits = _resolve_dni_credits(dni, tenant_id=tenant_id)
    return credits[0] if credits else None


# ── Comprobante validation (used by the validar_comprobante tool) ───────────


def pick_credit_for_dni(dni: str, tenant_id: str) -> dict | None:
    """Pick the credit to classify a voucher against for a DNI (Doris/fixture).

    Normal case is 1 DNI → 1 credit (returned directly). For the marginal
    multicrédito case we choose DETERMINISTICALLY the credit with the largest
    ``saldo_por_cancelar`` (ties broken by ``account_id``) — we never fail.
    The CCI is NOT used to select the credit; it is a voucher attribute only.
    """
    credits = _resolve_dni_credits(dni, tenant_id=tenant_id)
    if not credits:
        return None
    if len(credits) == 1:
        return credits[0]
    return max(
        credits,
        key=lambda c: (_to_float(c.get("saldo_por_cancelar")), str(c.get("account_id") or "")),
    )


def get_cronograma(account_id: str, tenant_id: str) -> list[dict]:
    """Return the installment schedule for a credit from Doris.

    Queries ``batch_pagos_v2_bronze`` directly by ``codigo_contrato`` (the
    per-installment table; same join key used in the profile query).

    Returns a list of dicts with keys ``n_cuota``, ``fecha_venc``, ``monto``,
    ``estado`` ordered by ``n_cuota``. Empty list when no rows or on error.
    """
    schema = _load_schema(tenant_id)
    db = _safe_ident(schema.get("db") or settings.doris_db, what="db")
    pagos = _safe_ident(schema["pagos_table"], what="pagos_table")
    pagos_key = _safe_ident(schema["join"]["pagos_key"], what="join.pagos_key")

    sql = (
        f"SELECT\n"
        f"  nro_cuotas AS n_cuota,\n"
        f"  fecha_de_pago_esperada_original AS fecha_venc,\n"
        f"  cuota_esperada_mensual AS monto,\n"
        f"  fecha_de_pago_del_cliente\n"
        f"FROM {db}.{pagos}\n"
        f"WHERE {pagos_key} = %s\n"
        f"ORDER BY nro_cuotas"
    )
    try:
        conn = _connect(db)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (account_id,))
                rows = list(cur.fetchall())
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return []

    result: list[dict] = []
    for row in rows:
        fecha_raw = row.get("fecha_venc")
        fecha_str = str(fecha_raw) if fecha_raw else None
        paid = row.get("fecha_de_pago_del_cliente") is not None
        monto_raw = row.get("monto")
        result.append({
            "n_cuota": int(row["n_cuota"]) if row.get("n_cuota") is not None else None,
            "fecha_venc": fecha_str,
            "monto": _to_float(monto_raw),
            "estado": "pagada" if paid else "pendiente",
        })
    return result


def get_moratoria_fields(account_id: str, tenant_id: str) -> dict:
    """Return moratoria-specific fields for an overdue credit.

    Fetches from Doris:
      - amortizacion_cuota: amortizacion_esperada_original of the first unpaid
        installment (NOT amortizacion_esperada_actualizado — 95% null).
      - tasa_interes_mensual: tasa_de_interes from batch_asignacion_review_bronze,
        parsed from varchar "X.XX%" to decimal (e.g. "3.50%" → 0.035). Uses
        DISTINCT to collapse raw dupes in the debt table.

    JOIN: batch_pagos_v2_bronze.codigo_contrato = batch_asignacion_review_bronze.id_credito

    Returns a dict with keys:
      amortizacion_cuota (float | None)
      tasa_interes_mensual (float | None)

    Returns {"amortizacion_cuota": None, "tasa_interes_mensual": None} on any
    error or missing data — callers must handle None (omit moratoria display).
    """
    _empty: dict = {"amortizacion_cuota": None, "tasa_interes_mensual": None}
    if not account_id:
        return _empty

    try:
        schema = _load_schema(tenant_id)
        db = _safe_ident(schema.get("db") or settings.doris_db, what="db")
        pagos = _safe_ident(schema["pagos_table"], what="pagos_table")
        debt = _safe_ident(schema["debt_table"], what="debt_table")
        pagos_key = _safe_ident(schema["join"]["pagos_key"], what="join.pagos_key")
        debt_key = _safe_ident(schema["join"]["debt_key"], what="join.debt_key")

        # First unpaid installment's amortization + credit's interest rate.
        # DISTINCT on debt table prevents raw-dupe inflation of tasa_de_interes.
        sql = (
            f"SELECT\n"
            f"  p.amortizacion_esperada_original AS amortizacion_cuota,\n"
            f"  d.tasa_de_interes AS tasa_de_interes_raw\n"
            f"FROM {db}.{pagos} p\n"
            f"JOIN (\n"
            f"  SELECT DISTINCT {debt_key}, tasa_de_interes\n"
            f"  FROM {db}.{debt}\n"
            f"  WHERE {debt_key} = %s\n"
            f") d ON p.{pagos_key} = d.{debt_key}\n"
            f"WHERE p.{pagos_key} = %s\n"
            f"  AND p.fecha_de_pago_del_cliente IS NULL\n"
            f"ORDER BY p.nro_cuotas\n"
            f"LIMIT 1"
        )
        conn = _connect(db)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (account_id, account_id))
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return _empty

    if not row:
        return _empty

    # Parse tasa_de_interes varchar "X.XX%" → decimal
    tasa_raw = row.get("tasa_de_interes_raw")
    tasa: float | None = None
    if tasa_raw is not None:
        try:
            tasa_str = str(tasa_raw).replace("%", "").strip()
            tasa = float(tasa_str) / 100
        except (ValueError, TypeError):
            tasa = None

    amort_raw = row.get("amortizacion_cuota")
    amort: float | None = _to_float(amort_raw) if amort_raw is not None else None

    return {"amortizacion_cuota": amort, "tasa_interes_mensual": tasa}


# ── IDC-01: Contract + DNI dual-factor identification ─────────────────────────

# Roles that constitute an obligated party of the credit — authorized to access.
# TESTIGO DE IDENTIDAD is explicitly excluded (witness only, not financially bound).
# FIADOR SOLIDARIO included by default (solidary guarantor = obligated party)
# — PENDING Naomi confirmation (2026-06-11).
_AUTHORIZED_ROLES = frozenset({"SOLICITANTE", "GARANTE", "FIADOR SOLIDARIO"})


def _query_contrato_rows(contrato_id: str, tenant_id: str) -> list[dict]:
    """Query batch_asignacion_review_bronze for all person-rows of a contract.

    Uses ROW_NUMBER() OVER (PARTITION BY id_credito ORDER BY creado_el DESC) to
    collapse raw duplicates — callers receive at most one row per (id_credito,
    posicion_contractual) combination. Returns the raw rows keyed by column name.
    May raise on connection/query failure — caller must catch.
    """
    schema = _load_schema(tenant_id)
    db = _safe_ident(schema.get("db") or settings.doris_db, what="db")
    debt = _safe_ident(schema["debt_table"], what="debt_table")

    # Read contrato_column from tenant cobranza config (default "id_contrato").
    import json as _json  # noqa: PLC0415
    _cfg_path = _tenants_root() / tenant_id / "tenant.config.json"
    try:
        _tcfg = _json.loads(_cfg_path.read_text(encoding="utf-8"))
        _contrato_col_raw = (_tcfg.get("cobranza") or {}).get("contrato_column", "id_contrato")
    except (OSError, _json.JSONDecodeError):
        _contrato_col_raw = "id_contrato"
    contrato_col = _safe_ident(_contrato_col_raw, what="contrato_column")

    # Dedup: keep the most-recent row per (id_credito, posicion_contractual).
    # batch_asignacion_review_bronze has ~3-6x raw duplicates (confirmed 2026-06-11).
    sql = (
        f"SELECT t.*\n"
        f"FROM (\n"
        f"  SELECT *,\n"
        f"    ROW_NUMBER() OVER (\n"
        f"      PARTITION BY id_credito, posicion_contractual\n"
        f"      ORDER BY creado_el DESC\n"
        f"    ) AS _rn\n"
        f"  FROM {db}.{debt}\n"
        f"  WHERE {contrato_col} = %s\n"
        f"    AND posicion_contractual IN (\n"
        f"      'SOLICITANTE', 'GARANTE', 'FIADOR SOLIDARIO'\n"
        f"    )\n"
        f") t\n"
        f"WHERE t._rn = 1"
    )
    conn = _connect(db)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (contrato_id,))
            return list(cur.fetchall())
    finally:
        conn.close()


def resolve_contrato(contrato_id: str, dni: str, tenant_id: str) -> dict | None:
    """IDC-01: Dual-factor contract identification — contract ID + DNI.

    Looks up all authorized parties (SOLICITANTE / GARANTE / FIADOR SOLIDARIO)
    for ``contrato_id`` in batch_asignacion_review_bronze. Returns a borrower
    profile dict ONLY IF ``dni`` matches one of those parties. Returns None on
    any of: contract not found, DNI not in authorized set, or any DB exception.

    Fail-closed: the same None is returned for "contract not found" and "DNI
    mismatch" so the caller cannot distinguish the two (no information leak).

    The profile shape is identical to resolve_dni for downstream compatibility.
    """
    if not contrato_id or not dni:
        return None

    norm_dni = _normalize_dni(dni)
    if not norm_dni:
        return None

    try:
        rows = _query_contrato_rows(contrato_id, tenant_id)
    except Exception:  # noqa: BLE001
        return None

    if not rows:
        return None

    # Verify the provided DNI is an authorized party.
    # Filter on posicion_contractual in Python as defense-in-depth (the SQL
    # WHERE clause already restricts to authorized roles, but this layer ensures
    # correctness even when rows come from mocks or partial queries).
    authorized_dnis: set[str] = set()
    for row in rows:
        role = str(row.get("posicion_contractual") or "").strip().upper()
        if role not in _AUTHORIZED_ROLES:
            continue
        row_dni = _normalize_dni(str(row.get("dni_ruc") or ""))
        if row_dni:
            authorized_dnis.add(row_dni)

    if norm_dni not in authorized_dnis:
        return None  # fail-closed: no reveal of contract existence

    # DNI is authorized — build a single profile from the first row.
    # All rows share the same id_credito; the profile is credit-level, not person-level.
    # Map the raw debt row through _row_to_profile using the standard column_map aliases.
    # Since _query_contrato_rows returns raw DB column names (not aliased), we build
    # the profile from the raw row using the known field mapping.
    first_row = rows[0]
    profile = _row_from_contrato_raw(first_row, tenant_id)
    return profile


def _row_from_contrato_raw(raw: dict, tenant_id: str) -> dict:
    """Map a raw batch_asignacion_review_bronze row to a borrower profile.

    The contrato query returns raw DB column names (not the aliased column_map
    names that the main profile SQL produces). This function bridges the gap by
    iterating the column_map forward: for each debt-sourced non-aggregated field,
    if the source column is present in the raw row, write the value under the
    profile field name. A single raw column may map to multiple profile fields
    (e.g. id_credito → account_id AND loan_number) — handled by forward iteration.
    """
    try:
        schema = _load_schema(tenant_id)
        column_map: dict = schema.get("column_map") or {}
    except Exception:  # noqa: BLE001
        column_map = {}

    aliased: dict = {}

    # Forward pass: copy raw columns that don't need aliasing first (pass-through).
    for db_col, value in raw.items():
        if not db_col.startswith("_"):
            aliased[db_col] = value

    # Forward pass: apply column_map — debt-sourced, non-aggregated fields only.
    # Multiple profile fields may share the same source column (id_credito →
    # account_id + loan_number); iterate all entries to handle this correctly.
    for field, spec in column_map.items():
        if spec.get("source", "debt") == "debt" and not spec.get("agg"):
            db_col = spec["column"]
            if db_col in raw:
                aliased[field] = raw[db_col]

    return _row_to_profile(aliased)


def validate_comprobante(
    dni: str,
    cci: str,
    monto: float,
    nro_operacion: str,
    tenant_id: str,
) -> dict:
    """Classify a payment voucher against the DNI's credit in Doris/fixture.

    The CCI is NO LONGER validated for pertenencia — it is a voucher attribute
    stored as-is (bank reconciliation is done later by a human). We never reject
    by CCI. Classification (pago/abono/cancelación) is done against the DNI's
    credit (max ``saldo_por_cancelar`` for the marginal multicrédito case).

    Returns a raw payload (no dedup — the tool layer owns the local dedup
    store). Keys: ``cuenta_valida`` (always ``True`` when the DNI resolves),
    ``credito``, ``tipo``, ``cuota_esperada``, ``saldo_por_cancelar``,
    ``mensaje``.
    """
    match = pick_credit_for_dni(dni, tenant_id=tenant_id)
    if not match:
        return {
            "cuenta_valida": False,
            "credito": None,
            "tipo": None,
            "mensaje": "No encontré créditos asociados a ese DNI.",
        }

    cuota = _to_float(match.get("cuota_esperada"))
    saldo = _to_float(match.get("saldo_por_cancelar"))
    tipo = classify_tipo(_to_float(monto), cuota, saldo)
    return {
        "cuenta_valida": True,
        "credito": match.get("account_id"),
        "tipo": tipo,
        "cuota_esperada": cuota,
        "saldo_por_cancelar": saldo,
        "mensaje": "Comprobante recibido.",
    }


