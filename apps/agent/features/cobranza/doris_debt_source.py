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


def _resolve_dni_credits(dni: str, tenant_id: str) -> list[dict]:
    """Return ALL credit profiles for a DNI (Doris first, fixture fallback).

    Handles multicrédito (a DNI with >1 credit). On any Doris error falls back
    to the seeded fixture (single profile per DNI there).
    """
    norm = _normalize_dni(dni)
    if not norm:
        return []
    try:
        rows = _query_dni(norm, tenant_id)
        if rows:
            return [_row_to_profile(r) for r in rows]
        # No rows in Doris for this DNI → fall through to fixture.
    except Exception:  # noqa: BLE001 — any driver/connection error → fixture
        pass
    # Fixture fallback: the mock source resolves a single profile by DNI.
    profile = mock_debt_source.resolve_dni(norm, tenant_id=tenant_id)
    return [profile] if profile else []


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
    return fixture_profile


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


