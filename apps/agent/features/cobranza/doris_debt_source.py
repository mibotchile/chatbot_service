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
# Note: monto_vencido and cuotas_vencidas are handled explicitly in _row_to_profile
# (always defaulted to 0 even when absent from the row) and are NOT in this set.
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

# Profile fields that are bank/payment strings — passed through as-is.
_STRING_FIELDS = frozenset({"inversionista", "cci", "banco"})

# Profile fields that must be presented as clean integer-string identifiers.
# These originate from DOUBLE columns in Doris (e.g. numero_de_cuenta) that are
# CAST AS DECIMAL(38,0) at the SQL source to recover the full integer before the
# window CTE promotes them to scientific notation. At the Python layer we format
# them as str(int(value)) — no decimal point, no scientific notation, no leading
# zeros (which were already lost at ETL since the Doris column type is DOUBLE).
_ID_NUMBER_FIELDS = frozenset({"cuenta_bancaria"})
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

    When the schema declares ``pagos_selection`` and/or ``batch_selection`` blocks,
    the builder emits ROW_NUMBER() OVER window CTEs instead of GROUP BY / MAX
    aggregates. This is required for correct first-unpaid-installment semantics
    (Spec: cobranza-balance). Legacy schemas without these blocks fall back to the
    previous GROUP BY strategy for backward compatibility.

    Window strategy (when pagos_selection + batch_selection are present):
      WITH pagos_sel AS (
        SELECT *, ROW_NUMBER() OVER (
          PARTITION BY <pagos_selection.partition_by>
          ORDER BY <pagos_selection.order_by>
        ) AS rn
        FROM <db>.<pagos_table>
      ),
      asig_sel AS (
        SELECT *, ROW_NUMBER() OVER (
          PARTITION BY <batch_selection.partition_by>
          ORDER BY <batch_selection.order_by>
        ) AS rn
        FROM <db>.<debt_table>
        WHERE <dni_column> = %s
      )
      SELECT <columns>
      FROM asig_sel a
      JOIN pagos_sel p ON p.<pagos_key> = a.<debt_key>
        AND p.rn = 1
      WHERE a.rn = 1

    days_overdue is always DERIVED via GREATEST(DATEDIFF(CURDATE(),
    fecha_de_pago_esperada_original), 0) — never read from the stale batch
    column dias_de_atraso_de_pago (confirmed live: column value 44 vs derived 94
    for P04197 on 2026-06-04).
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

    pagos_sel_cfg = schema.get("pagos_selection")
    batch_sel_cfg = schema.get("batch_selection")

    if pagos_sel_cfg and batch_sel_cfg:
        return _build_sql_window(
            schema=schema,
            db=db, debt=debt, pagos=pagos,
            debt_key=debt_key, pagos_key=pagos_key,
            dni_col=dni_col,
            column_map=column_map,
            pagos_sel_cfg=pagos_sel_cfg,
            batch_sel_cfg=batch_sel_cfg,
        )
    return _build_sql_legacy(
        db=db, debt=debt, pagos=pagos,
        debt_key=debt_key, pagos_key=pagos_key,
        dni_col=dni_col,
        column_map=column_map,
    )


def _build_sql_window(
    *,
    schema: dict,
    db: str,
    debt: str,
    pagos: str,
    debt_key: str,
    pagos_key: str,
    dni_col: str,
    column_map: dict,
    pagos_sel_cfg: dict,
    batch_sel_cfg: dict,
) -> tuple[str, str]:
    """Emit ROW_NUMBER() OVER CTE SQL for first-unpaid + latest-batch strategy.

    Called when the schema declares both ``pagos_selection`` and
    ``batch_selection`` config blocks. All identifiers are pre-sanitized by
    the caller. The partition/order expressions come verbatim from config
    (they are trusted tenant-operator config, not user input — the whitelist
    guard covers table/column names but not raw ORDER BY expressions).
    """
    pagos_partition = pagos_sel_cfg["partition_by"]
    pagos_order = pagos_sel_cfg["order_by"]
    batch_partition = batch_sel_cfg["partition_by"]
    batch_order = batch_sel_cfg["order_by"]

    # Build SELECT columns from the column_map.
    # Columns from pagos use alias "p"; from debt use alias "a".
    # "coalesce" key → COALESCE(p.col1, p.col2) AS field
    # "from_selected_row" → just p.col (no GROUP BY needed — window handles it)
    select_parts: list[str] = []
    for field, spec in column_map.items():
        _safe_ident(field, what=f"column_map key {field!r}")
        source = spec.get("source", "debt")
        alias = "a" if source == "debt" else "p"

        coalesce_cols = spec.get("coalesce")
        if coalesce_cols:
            # COALESCE(p.col1, p.col2) — e.g. cuota_esperada_actualizada, cuota_esperada_mensual
            if not isinstance(coalesce_cols, list) or len(coalesce_cols) < 2:
                raise ValueError(
                    f"doris_schema: column_map[{field!r}].coalesce must be a list of >=2 column names"
                )
            coalesce_refs = ", ".join(
                f"{alias}.{_safe_ident(c, what=f'coalesce[{field}]')}"
                for c in coalesce_cols
            )
            select_parts.append(f"COALESCE({coalesce_refs}) AS {field}")
        else:
            col = _safe_ident(spec["column"], what=f"column_map[{field}].column")
            cast = spec.get("cast")
            if cast == "id_number" and alias == "a":
                # DOUBLE columns (e.g. numero_de_cuenta) must be cast at the SOURCE —
                # i.e. inside the asig_sel CTE before the ROW_NUMBER window promotes
                # the value to scientific notation (8.98348E+12). Casting after the
                # window CTE returns NULL (verified live on Doris). DECIMAL(38,0)
                # recovers the full integer (also BIGINT works, but DECIMAL is safer
                # for very large account numbers). Note: any leading zeros were lost
                # at ETL since the upstream column type is DOUBLE — acceptable for now;
                # numero_de_cuenta should ideally be a STRING column upstream.
                select_parts.append(f"CAST({alias}.{col} AS DECIMAL(38,0)) AS {field}")
            else:
                select_parts.append(f"{alias}.{col} AS {field}")

    # days_overdue is derived from date arithmetic (always correct, never stale).
    # Injected here regardless of column_map entry — it overwrites any mapped value.
    select_parts.append(
        "GREATEST(DATEDIFF(CURDATE(), p.fecha_de_pago_esperada_original), 0) AS days_overdue"
    )
    # Overdue aggregates: monto_vencido = sum of overdue unpaid installments;
    # cuotas_vencidas = count of same. These drive the PRIMARY "cuánto debo" display
    # for the cobranza bot (what the borrower owes to GET CURRENT), while balance
    # (total remaining) is secondary context. Joined from the pagos_agg CTE below.
    select_parts.append("COALESCE(agg.monto_vencido, 0) AS monto_vencido")
    select_parts.append("COALESCE(agg.cuotas_vencidas, 0) AS cuotas_vencidas")

    select_clause = ",\n    ".join(select_parts)

    sql = (
        f"WITH pagos_sel AS (\n"
        f"  SELECT *,\n"
        f"    ROW_NUMBER() OVER (\n"
        f"      PARTITION BY {pagos_partition}\n"
        f"      ORDER BY {pagos_order}\n"
        f"    ) AS rn\n"
        f"  FROM {db}.{pagos}\n"
        f"),\n"
        f"pagos_agg AS (\n"
        f"  -- Overdue aggregate: unpaid installments whose expected date has passed.\n"
        f"  -- monto_vencido = what the borrower owes to get current (PRIMARY display).\n"
        f"  SELECT\n"
        f"    {pagos_key} AS {pagos_key},\n"
        f"    SUM(cuota_esperada_mensual) AS monto_vencido,\n"
        f"    COUNT(*) AS cuotas_vencidas\n"
        f"  FROM {db}.{pagos}\n"
        f"  WHERE fecha_de_pago_del_cliente IS NULL\n"
        f"    AND fecha_de_pago_esperada_original <= CURDATE()\n"
        f"  GROUP BY {pagos_key}\n"
        f"),\n"
        f"asig_sel AS (\n"
        f"  SELECT *,\n"
        f"    ROW_NUMBER() OVER (\n"
        f"      PARTITION BY {batch_partition}\n"
        f"      ORDER BY {batch_order}\n"
        f"    ) AS rn\n"
        f"  FROM {db}.{debt}\n"
        f"  WHERE {dni_col} = %s\n"
        f")\n"
        f"SELECT\n"
        f"    {select_clause}\n"
        f"FROM asig_sel a\n"
        f"JOIN pagos_sel p\n"
        f"  ON p.{pagos_key} = a.{debt_key}\n"
        f"  AND p.rn = 1\n"
        f"LEFT JOIN pagos_agg agg\n"
        f"  ON agg.{pagos_key} = a.{debt_key}\n"
        f"WHERE a.rn = 1"
    )
    return sql, db


def _build_sql_legacy(
    *,
    db: str,
    debt: str,
    pagos: str,
    debt_key: str,
    pagos_key: str,
    dni_col: str,
    column_map: dict,
) -> tuple[str, str]:
    """Legacy GROUP BY / MAX strategy for schemas without window-selection blocks.

    Retained for backward compatibility with tenants that do not declare
    ``pagos_selection`` + ``batch_selection``. New tenants should use the window
    strategy.
    """
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
        elif key in _ID_NUMBER_FIELDS:
            # Format as a clean digit string: no scientific notation, no trailing .0.
            # Value arrives as int or Decimal from the DECIMAL(38,0) cast applied at
            # the SQL source. None/empty → None (field absent or NULL in Doris).
            if value is None or value == "":
                profile[key] = None
            else:
                try:
                    profile[key] = str(int(value))
                except (TypeError, ValueError):
                    profile[key] = None
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
    # Overdue aggregates: always present in profile, defaulting to 0 when the
    # row does not include them (e.g. legacy schemas without the pagos_agg CTE).
    # monto_vencido is a float (currency amount); cuotas_vencidas is a count (int).
    profile["monto_vencido"] = _to_float(row.get("monto_vencido", 0))
    profile["cuotas_vencidas"] = int(_to_float(row.get("cuotas_vencidas", 0)))
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


