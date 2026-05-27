"""Doris debt source for the PrestamYpe tenant (REAL data, fixture fallback).

Same interface as ``mock_debt_source`` (``resolve_token`` / ``resolve_dni``)
returning the standard borrower *profile dict*, so the existing
``consultar_deuda`` tool works unchanged. Adds the PrestamYpe-specific extra
fields (``cci``, ``banco``, ``inversionista``, ``cuota_esperada``,
``saldo_por_cancelar``) and a ``validate_comprobante`` helper used by the
``validar_comprobante`` tool.

Data lives in Apache Doris (MySQL wire protocol) at
``project_QUIdI0iwQY0l3pJwRKLB`` (bronze layer):
  - debt    : ``batch_asignacion_review_bronze``
  - payments : ``batch_pagos_v2_bronze`` (join ``codigo_contrato = id_credito``)

FALLBACK CONTRACT (non-negotiable for the demo): on ANY connection/query error
this module falls back to the seeded fixture
(``tenants/prestamype/mock/borrowers.json``) so the demo never breaks. The
fixture is a real sample taken from Doris on 2026-05-27.

SCHEMA NOTES (verified, see CLAUDE.md / engram prestamype/build-backend):
  - ``monto_total`` is the MONTHLY INSTALLMENT (cuota), NOT the full debt.
  - the real outstanding balance is ``saldo_por_cancelar`` (payments table).
  - ``codigo_de_cuenta_cci`` is 100% clean; ``numero_de_cuenta`` is ~10%
    corrupt (E+12) and is NEVER used.
  - the pagos join is 1:many — aggregate (MAX) per credit.
"""

from __future__ import annotations

import re
from functools import lru_cache

from config.settings import settings
from integrations import mock_debt_source

_TENANT = "prestamype"

# Single representative row per credit (cuota/saldo aggregated), enriched with
# the asignacion columns. DNI is bound as a parameter (never string-formatted).
_PROFILE_SQL = """
SELECT
  a.id_credito, a.dni_ruc, a.nombre_completo, a.correo_electronico, a.telefono,
  a.capital, a.dias_mora, a.fecha_vencimiento, a.moneda, a.banco,
  a.codigo_de_cuenta_cci, a.inversionista,
  MAX(p.cuota_esperada_actualizada) AS cuota_esperada,
  MAX(p.saldo_por_cancelar)         AS saldo_por_cancelar,
  MAX(p.monto_total_pagado_al_credito) AS monto_pagado
FROM {db}.batch_asignacion_review_bronze a
JOIN {db}.batch_pagos_v2_bronze p
  ON p.codigo_contrato = a.id_credito
WHERE a.dni_ruc = %s
GROUP BY a.id_credito, a.dni_ruc, a.nombre_completo, a.correo_electronico,
  a.telefono, a.capital, a.dias_mora, a.fecha_vencimiento, a.moneda, a.banco,
  a.codigo_de_cuenta_cci, a.inversionista
ORDER BY a.dias_mora DESC
"""


def _normalize_dni(dni: str) -> str:
    """Keep only digits — tolerates spaces/dots the user might type."""
    return re.sub(r"\D", "", dni or "")


def normalize_cci(cci: str) -> str:
    """Strip everything but digits from a CCI (Doris stores some with spaces)."""
    return re.sub(r"\D", "", cci or "")


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
    """Map a Doris result row to the standard borrower profile dict.

    balance ← saldo_por_cancelar (real outstanding), next_installment_amount ←
    monto_total/cuota, status from dias_mora. Keeps the PrestamYpe extras.
    """
    code, sym = _currency(row.get("moneda"))
    dias_mora = int(row.get("dias_mora") or 0)
    saldo = _to_float(row.get("saldo_por_cancelar"))
    cuota = _to_float(row.get("cuota_esperada"))
    return {
        "account_id": row.get("id_credito"),
        "borrower_name": row.get("nombre_completo"),
        "dni": row.get("dni_ruc"),
        "email": row.get("correo_electronico") or "",
        "phone": str(row.get("telefono") or ""),
        "loan_number": row.get("id_credito"),
        "currency": code,
        "currency_symbol": sym,
        "principal_original": _to_float(row.get("capital")),
        "balance": saldo,
        "next_due_date": str(row.get("fecha_vencimiento") or "") or None,
        "next_installment_amount": cuota,
        "days_overdue": dias_mora,
        "status": "al_dia" if dias_mora == 0 else "en_mora",
        "status_label": "Al día" if dias_mora == 0 else "En mora",
        # ── PrestamYpe extras ──
        "cci": row.get("codigo_de_cuenta_cci") or "",
        "banco": row.get("banco") or "",
        "inversionista": row.get("inversionista") or "",
        "cuota_esperada": cuota,
        "saldo_por_cancelar": saldo,
    }


@lru_cache(maxsize=1)
def _import_pymysql():
    """Lazy import so the dependency is only required when Doris is used."""
    import pymysql  # noqa: PLC0415

    return pymysql


def _connect():
    """Open a short-lived Doris connection. Raises on failure (caller falls back)."""
    pymysql = _import_pymysql()
    return pymysql.connect(
        host=settings.doris_host,
        port=int(settings.doris_port),
        user=settings.doris_user,
        password=settings.doris_password,
        database=settings.doris_db,
        connect_timeout=5,
        read_timeout=10,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def _query_dni(dni: str) -> list[dict]:
    """Run the profile query for a DNI against Doris. May raise — caller catches."""
    sql = _PROFILE_SQL.format(db=settings.doris_db)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (dni,))
            return list(cur.fetchall())
    finally:
        conn.close()


def _resolve_dni_credits(dni: str, tenant_id: str = _TENANT) -> list[dict]:
    """Return ALL credit profiles for a DNI (Doris first, fixture fallback).

    Handles multicrédito (a DNI with >1 credit). On any Doris error falls back
    to the seeded fixture (single profile per DNI there).
    """
    norm = _normalize_dni(dni)
    if not norm:
        return []
    try:
        rows = _query_dni(norm)
        if rows:
            return [_row_to_profile(r) for r in rows]
        # No rows in Doris for this DNI → fall through to fixture.
    except Exception:  # noqa: BLE001 — any driver/connection error → fixture
        pass
    # Fixture fallback: the mock source resolves a single profile by DNI.
    profile = mock_debt_source.resolve_dni(norm, tenant_id=tenant_id)
    return [profile] if profile else []


# ── Public interface (mirrors mock_debt_source) ────────────────────────────


def resolve_token(token: str, tenant_id: str = _TENANT) -> dict | None:
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


def resolve_dni(dni: str, tenant_id: str = _TENANT) -> dict | None:
    """Resolve a DNI/RUC to a single borrower profile (first credit).

    For multicrédito the first (highest mora) credit is returned to keep the
    same single-profile contract as the mock source. Returns ``None`` if not
    found in Doris nor the fixture.
    """
    credits = _resolve_dni_credits(dni, tenant_id=tenant_id)
    return credits[0] if credits else None


# ── Comprobante validation (used by the validar_comprobante tool) ───────────


def pick_credit_for_dni(dni: str, tenant_id: str = _TENANT) -> dict | None:
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
    tenant_id: str = _TENANT,
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


def classify_tipo(monto: float, cuota: float, saldo: float, tol: float = 0.02) -> str:
    """Classify a payment amount as pago / abono / cancelacion.

    - ``monto`` ≈ ``saldo`` (±tol) → ``cancelacion`` (pays the whole balance)
    - ``monto`` ≈ ``cuota`` (±tol) → ``pago`` (regular installment)
    - ``monto`` < ``cuota``        → ``abono`` (partial)
    - otherwise (monto > cuota but not full saldo) → ``abono`` (extra partial)

    Cancelación is checked first so a single-installment loan (cuota == saldo)
    is reported as a full cancelation.
    """
    monto = _to_float(monto)
    if saldo > 0 and abs(monto - saldo) <= tol * saldo:
        return "cancelacion"
    if cuota > 0 and abs(monto - cuota) <= tol * cuota:
        return "pago"
    if cuota > 0 and monto < cuota:
        return "abono"
    return "abono"
