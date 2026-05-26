"""Cobranza domain tools for the PrestaUnion DEMO — consolidated module.

Three use cases only (Musk-scoped):
  1. consultar_deuda          — debt balance / installments / due date / status
  2. registrar_reclamo        — Libro de Reclamaciones (Indecopi), returns LR-folio
  3. emitir_certificado_no_adeudo — certificate PDF when balance == 0

SECURITY CONTRACT (non-negotiable, even in a demo):
  - ``account_id`` / borrower identity NEVER comes from the LLM or the user.
    Every tool reads the *verified* profile (``debt_context``) injected
    server-side by the ToolRegistry from the resolved campaign token.
  - The tool functions here receive the resolved ``profile`` dict directly;
    the registry is responsible for the gate (no profile -> not callable).
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from integrations.certificate_pdf import generate_certificate

# ── Reclamos persistence (mock JSON so the demo can show registered claims) ──
_RECLAMOS_PATH = Path("/tmp/prestaunion_reclamos.json")

_RECLAMO_TIPOS = {"queja", "reclamo"}


def _load_reclamos() -> list[dict]:
    if not _RECLAMOS_PATH.exists():
        return []
    try:
        return json.loads(_RECLAMOS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_reclamos(items: list[dict]) -> None:
    _RECLAMOS_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _next_folio() -> str:
    """Sequential Libro de Reclamaciones folio: LR-YYYY-NNNNN."""
    items = _load_reclamos()
    year = date.today().year
    seq = sum(1 for r in items if str(r.get("folio", "")).startswith(f"LR-{year}-")) + 1
    return f"LR-{year}-{seq:05d}"


def _add_business_days(start: date, days: int) -> date:
    """Add N business days (Mon-Fri) — used for the 15-day Indecopi deadline."""
    d = start
    added = 0
    while added < days:
        d += timedelta(days=1)
        if d.weekday() < 5:  # 0-4 = Mon-Fri
            added += 1
    return d


def _fmt(amount: float, sym: str = "S/") -> str:
    return f"{sym} {amount:,.2f}"


# ── 1. Consultar deuda ───────────────────────────────────────────────────

async def consultar_deuda(profile: dict) -> dict:
    """Return the debt summary for the verified borrower profile.

    Reads ONLY from the server-injected profile. Returns a structured payload
    the LLM can narrate (it must not invent any number not present here).
    """
    sym = profile.get("currency_symbol", "S/")
    status = profile.get("status", "")
    pending = profile.get("installments_pending", 0)

    summary = {
        "account_id": profile["account_id"],
        "loan_number": profile.get("loan_number"),
        "business_name": profile.get("business_name"),
        "currency": profile.get("currency", "PEN"),
        "currency_symbol": sym,
        "balance": profile.get("balance", 0.0),
        "balance_formatted": _fmt(profile.get("balance", 0.0), sym),
        "principal_original": profile.get("principal_original"),
        "installments_total": profile.get("installments_total"),
        "installments_paid": profile.get("installments_paid"),
        "installments_pending": pending,
        "tcea_pct": profile.get("tcea_pct"),
        "status": status,
        "status_label": profile.get("status_label"),
        "next_due_date": profile.get("next_due_date"),
        "next_installment_amount": profile.get("next_installment_amount"),
        "next_installment_formatted": _fmt(profile.get("next_installment_amount", 0.0), sym),
        "late_fee": profile.get("late_fee", 0.0),
        "days_overdue": profile.get("days_overdue", 0),
        "installment_history": profile.get("installment_history", []),
        "has_debt": (profile.get("balance", 0.0) or 0.0) > 0,
    }
    return summary


# ── 2. Registrar reclamo (Libro de Reclamaciones — Indecopi) ───────────────

async def registrar_reclamo(profile: dict, tipo: str, descripcion: str) -> dict:
    """Register a claim/complaint in the (mock) Libro de Reclamaciones.

    ``tipo`` is "reclamo" (disconformity with product/service) or "queja"
    (disconformity with attention). Returns a folio + the 15-business-day
    response deadline mandated by Indecopi (Perú).
    """
    tipo_norm = (tipo or "").strip().lower()
    if tipo_norm not in _RECLAMO_TIPOS:
        tipo_norm = "reclamo"

    desc = (descripcion or "").strip()
    if not desc:
        return {
            "error": "descripcion_required",
            "message": "Se requiere una descripción del reclamo.",
        }

    folio = _next_folio()
    today = date.today()
    deadline = _add_business_days(today, 15)
    record = {
        "folio": folio,
        "tipo": tipo_norm,
        "descripcion": desc,
        "account_id": profile["account_id"],
        "business_name": profile.get("business_name"),
        "borrower_name": profile.get("borrower_name"),
        "loan_number": profile.get("loan_number"),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "response_deadline": deadline.isoformat(),
        "status": "registrado",
    }
    items = _load_reclamos()
    items.append(record)
    _save_reclamos(items)

    return {
        "registered": True,
        "folio": folio,
        "tipo": tipo_norm,
        "response_deadline": deadline.isoformat(),
        "response_business_days": 15,
        "message": (
            f"Reclamo registrado con el folio {folio}. "
            f"Recibirá respuesta en un plazo máximo de 15 días hábiles "
            f"(hasta el {deadline.isoformat()})."
        ),
    }


# ── 3. Emitir certificado de no adeudo ─────────────────────────────────────

async def emitir_certificado_no_adeudo(profile: dict, download_base_url: str = "") -> dict:
    """Issue a no-debt certificate PDF if the verified account has zero balance.

    If balance > 0 the certificate does NOT proceed and the tool explains why.
    """
    balance = profile.get("balance", 0.0) or 0.0
    sym = profile.get("currency_symbol", "S/")

    if balance > 0:
        return {
            "issued": False,
            "reason": "outstanding_balance",
            "balance": balance,
            "balance_formatted": _fmt(balance, sym),
            "message": (
                f"No procede emitir el certificado de no adeudo: la cuenta registra "
                f"un saldo pendiente de {_fmt(balance, sym)}. El certificado solo se "
                f"emite una vez cancelada la totalidad del préstamo."
            ),
        }

    folio = f"CNA-{date.today().year}-{random.randint(10000, 99999)}"
    pdf_path = generate_certificate(
        folio=folio,
        borrower_name=profile.get("borrower_name", ""),
        business_name=profile.get("business_name", ""),
        loan_number=profile.get("loan_number", ""),
        company_name="PrestaUnion",
        cancelled_at=profile.get("cancelled_at"),
    )
    filename = pdf_path.name
    base = (download_base_url or "").rstrip("/")
    download_url = f"{base}/api/v1/cobranza/certificate/{filename}"

    return {
        "issued": True,
        "folio": folio,
        "borrower_name": profile.get("borrower_name"),
        "business_name": profile.get("business_name"),
        "loan_number": profile.get("loan_number"),
        "issued_at": date.today().isoformat(),
        "filename": filename,
        "download_url": download_url,
        "message": (
            f"Certificado de no adeudo emitido (folio {folio}). "
            f"Puede descargarlo desde el enlace."
        ),
    }
