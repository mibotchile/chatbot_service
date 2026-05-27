"""Cobranza domain tools for the PrestaUnion DEMO — consolidated module.

Use cases (Musk-scoped):
  1. consultar_deuda          — debt balance / installments / due date / status
  2. registrar_reclamo        — Libro de Reclamaciones (Indecopi), returns LR-folio
  3. emitir_certificado_no_adeudo — certificate PDF when balance == 0
  4. enviar_documento         — deliver a document (certificado / estado_cuenta)
                                to the borrower by correo or whatsapp

SECURITY CONTRACT (non-negotiable, even in a demo):
  - ``account_id`` / borrower identity NEVER comes from the LLM or the user.
    Every tool reads the *verified* profile (``debt_context``) injected
    server-side by the ToolRegistry from the resolved campaign token or DNI.
  - The borrower's destination email/phone for delivery come from the verified
    profile, NEVER from the LLM.
  - The tool functions here receive the resolved ``profile`` dict directly;
    the registry is responsible for the gate (no profile -> not callable).
"""

from __future__ import annotations

import json
import random
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from config.settings import settings
from integrations.certificate_pdf import generate_certificate
from integrations.doris_debt_source import classify_tipo, normalize_cci

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
        "pdf_path": str(pdf_path),
        "download_url": download_url,
        "message": (
            f"Certificado de no adeudo emitido (folio {folio}). "
            f"Puedes descargarlo desde el enlace."
        ),
    }


# ── 4. Entrega multicanal de documentos (correo / WhatsApp) ────────────────

_CANALES = {"correo", "whatsapp"}
_DOC_TIPOS = {"certificado_no_adeudo", "estado_cuenta"}
_DOC_LABELS = {
    "certificado_no_adeudo": "Certificado de no adeudo",
    "estado_cuenta": "Estado de cuenta",
}


def _estado_cuenta_html(profile: dict) -> str:
    """Formatted account-statement summary (HTML body) from the verified profile."""
    sym = profile.get("currency_symbol", "S/")
    rows = [
        ("Negocio", profile.get("business_name", "")),
        ("Préstamo", profile.get("loan_number", "")),
        ("Estado", profile.get("status_label", "")),
        ("Saldo pendiente", _fmt(profile.get("balance", 0.0), sym)),
        ("Cuotas pagadas", f"{profile.get('installments_paid', 0)} de {profile.get('installments_total', 0)}"),
        ("Cuotas pendientes", str(profile.get("installments_pending", 0))),
    ]
    if profile.get("next_due_date"):
        rows.append(("Próxima cuota", f"{_fmt(profile.get('next_installment_amount', 0.0), sym)} (vence {profile['next_due_date']})"))
    if (profile.get("late_fee", 0.0) or 0.0) > 0:
        rows.append(("Recargo por mora", _fmt(profile.get("late_fee", 0.0), sym)))
        rows.append(("Días de atraso", str(profile.get("days_overdue", 0))))
    if profile.get("tcea_pct") is not None:
        rows.append(("TCEA", f"{profile['tcea_pct']}%"))
    cells = "".join(
        f'<tr><td style="padding:6px 10px;color:#4a5568;">{k}</td>'
        f'<td style="padding:6px 10px;font-weight:600;">{v}</td></tr>'
        for k, v in rows
    )
    return f'<table style="border-collapse:collapse;width:100%;">{cells}</table>'


def _estado_cuenta_text(profile: dict) -> str:
    """Plain-text account-statement summary for WhatsApp (no HTML).

    Mirrors ``_estado_cuenta_html`` but renders a WhatsApp-legible message
    using line breaks and the platform's *bold* markers.
    """
    sym = profile.get("currency_symbol", "S/")
    lines = [
        "*Estado de cuenta — PrestaUnion*",
        "",
        f"Negocio: {profile.get('business_name', '')}",
        f"Préstamo: {profile.get('loan_number', '')}",
        f"Estado: {profile.get('status_label', '')}",
        f"Saldo pendiente: {_fmt(profile.get('balance', 0.0), sym)}",
        f"Cuotas pagadas: {profile.get('installments_paid', 0)} de {profile.get('installments_total', 0)}",
        f"Cuotas pendientes: {profile.get('installments_pending', 0)}",
    ]
    if profile.get("next_due_date"):
        lines.append(
            f"Próxima cuota: {_fmt(profile.get('next_installment_amount', 0.0), sym)} "
            f"(vence {profile['next_due_date']})"
        )
    if (profile.get("late_fee", 0.0) or 0.0) > 0:
        lines.append(f"Recargo por mora: {_fmt(profile.get('late_fee', 0.0), sym)}")
        lines.append(f"Días de atraso: {profile.get('days_overdue', 0)}")
    if profile.get("tcea_pct") is not None:
        lines.append(f"TCEA: {profile['tcea_pct']}%")
    return "\n".join(lines)


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _infer_canal(destino: str) -> str:
    """Infer the channel from the destination string: '@' → correo, digits → whatsapp."""
    d = (destino or "").strip()
    if "@" in d:
        return "correo"
    if sum(c.isdigit() for c in d) >= 8:
        return "whatsapp"
    return ""


def _valid_email(destino: str) -> bool:
    return bool(_EMAIL_RE.match((destino or "").strip()))


def _valid_phone(destino: str) -> bool:
    digits = re.sub(r"\D", "", destino or "")
    # Peru mobile: 9 digits (9XXXXXXXX) or 11 with country code (519XXXXXXXX)
    return len(digits) in (9, 11)


async def enviar_documento(
    profile: dict,
    tipo: str,
    destino: str = "",
    canal: str = "",
    *,
    email_service=None,
    whatsapp_service=None,
    download_base_url: str = "",
) -> dict:
    """Deliver a cobranza document to the destination the USER provided.

    tipo ∈ {certificado_no_adeudo, estado_cuenta}.
    destino = the email or phone the user typed to RECEIVE the document.
    canal ∈ {correo, whatsapp} — inferred from `destino` when omitted.

    The document content and the borrower identity (account_id) ALWAYS come from
    the verified ``profile`` (debt_context), server-side. The ONLY thing that
    comes from the user is the *delivery destination* — deliberate PII the user
    provides to receive their own document, validated for format here.

    TODO PRODUCCIÓN — SEGURIDAD: en producción el destino debe ser el correo /
    teléfono REGISTRADO del cliente (mostrarlo enmascarado y pedir confirmación,
    NO aceptar uno arbitrario) para evitar fuga de documentos a terceros. En la
    DEMO se acepta el destino que ingresa el usuario para que sea tangible.

    WhatsApp is in backlog: if Evolution isn't configured, the service logs an
    honest dry-run (no fake success). Extensible: add new tipos to _DOC_TIPOS.
    """
    tipo_norm = (tipo or "").strip().lower()
    if tipo_norm not in _DOC_TIPOS:
        return {"error": "tipo_invalido", "message": f"Documento no soportado: {tipo}."}

    destino = (destino or "").strip()
    canal_norm = (canal or "").strip().lower() or _infer_canal(destino)

    # Need a destination from the user before sending.
    if not destino:
        return {
            "error": "destino_requerido",
            "message": "¿A qué correo o número de WhatsApp te lo envío?",
        }
    if canal_norm not in _CANALES:
        return {
            "error": "canal_invalido",
            "message": "Indícame un correo (con @) o un número de WhatsApp para enviártelo.",
        }

    # Validate the destination format; ask again cordially if wrong.
    if canal_norm == "correo" and not _valid_email(destino):
        return {
            "error": "email_invalido",
            "message": "Ese correo no parece válido. ¿Me lo confirmas? (ejemplo: nombre@correo.com)",
        }
    if canal_norm == "whatsapp" and not _valid_phone(destino):
        return {
            "error": "telefono_invalido",
            "message": "Ese número no parece válido. ¿Me confirmas tu WhatsApp? (9 dígitos)",
        }

    label = _DOC_LABELS[tipo_norm]
    name = profile.get("borrower_name", "")

    # Resolve the document payload (PDF for certificate, summary for estado).
    pdf_path = None
    summary_html = ""
    if tipo_norm == "certificado_no_adeudo":
        cert = await emitir_certificado_no_adeudo(profile, download_base_url=download_base_url)
        if not cert.get("issued"):
            return cert  # not eligible (balance > 0) — propagate the explanation
        pdf_path = cert.get("pdf_path")
        doc_ref = cert.get("folio")
    else:  # estado_cuenta
        summary_html = _estado_cuenta_html(profile)
        doc_ref = profile.get("loan_number")

    if canal_norm == "correo":
        sent = False
        if email_service:
            sent = await email_service.send_document(
                destino, name, label, pdf_path=pdf_path, summary_html=summary_html,
            )
        return {
            "delivered": bool(sent),
            "canal": "correo",
            "tipo": tipo_norm,
            "doc_label": label,
            "destino": destino,  # the address the user gave (demo: tangible)
            "doc_ref": doc_ref,
            "message": (
                f"Listo, te envié tu {label.lower()} al correo {destino}."
                if sent else
                f"Intenté enviar tu {label.lower()} al correo {destino}; si no llega, escríbenos de nuevo."
            ),
        }

    # canal == whatsapp (dry-run honesto si Evolution no está conectado)
    configured = bool(whatsapp_service and getattr(whatsapp_service, "is_configured", False))
    sent = False
    if tipo_norm == "estado_cuenta":
        # No hay PDF: el estado se envía como TEXTO legible vía send_text.
        if whatsapp_service:
            sent = await whatsapp_service.send_text(destino, _estado_cuenta_text(profile))
    else:  # certificado_no_adeudo → documento PDF adjunto
        media_url = ""
        if pdf_path and download_base_url:
            media_url = f"{download_base_url.rstrip('/')}/api/v1/cobranza/certificate/{Path(pdf_path).name}"
        if whatsapp_service:
            sent = await whatsapp_service.send_document(destino, name, label, media_url=media_url)
    return {
        "delivered": bool(sent),
        "canal": "whatsapp",
        "tipo": tipo_norm,
        "doc_label": label,
        "destino": destino,  # the number the user gave (demo: tangible)
        "doc_ref": doc_ref,
        # Honest status: WhatsApp is backlog unless Evolution is fully wired.
        "channel_status": "configured" if configured else "backlog_or_dry_run",
        "message": (
            f"Listo, te envío tu {label.lower()} a tu WhatsApp {destino}."
        ),
    }


# ── 5. Validar comprobante de pago (PrestamYpe) ────────────────────────────

# Local dedup store: a comprobante's nº de operación seen once is flagged on
# any repeat. JSON list of records so the demo can show the audit trail. Lives
# under COBRANZA_COMPROBANTE_DIR (mounted volume), NOT /tmp, so it persists with
# the uploaded images. Tests override _COMPROBANTES_PATH via monkeypatch.
_COMPROBANTES_PATH = Path(settings.comprobante_dir) / "comprobantes.json"


def _load_comprobantes() -> list[dict]:
    if not _COMPROBANTES_PATH.exists():
        return []
    try:
        return json.loads(_COMPROBANTES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_comprobantes(items: list[dict]) -> None:
    _COMPROBANTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _COMPROBANTES_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


_TIPO_LABELS = {"pago": "Pago", "abono": "Abono", "cancelacion": "Cancelación"}


async def validar_comprobante(
    profile: dict,
    cci: str,
    monto: float,
    nro_operacion: str,
) -> dict:
    """Validate a payment voucher for the verified borrower (PrestamYpe).

    Logic (against the server-injected ``profile`` = verified credit):
      (a) ``cci`` must match the credit's CCI → ``cuenta_valida`` + ``credito``;
          else False with "esa cuenta no corresponde a tu crédito".
      (b) classify ``tipo`` from ``monto`` vs cuota / saldo (±2% tolerance):
          ≈ cuota → "pago"; < cuota → "abono"; ≈ saldo total → "cancelacion".
      (c) dedup ``nro_operacion`` against a local JSON store; if seen before →
          ``dedup_ok = False`` (duplicate flagged), no re-registration.

    Identity/credit ALWAYS come from the verified ``profile`` — only the 3
    voucher fields (cci, monto, nro_operacion) come from the user. The result
    is queued for human reconciliation (the comprobante is an indicio, not an
    auto-conciliation).
    """
    cci_in = normalize_cci(cci)
    if not cci_in:
        return {
            "cuenta_valida": False,
            "credito": None,
            "tipo": None,
            "dedup_ok": None,
            "mensaje": "Indícame el CCI de la cuenta a la que transferiste (20 dígitos).",
        }

    credito = profile.get("account_id")
    credito_cci = normalize_cci(profile.get("cci", ""))

    # (a) ¿la cuenta apunta al crédito del cliente?
    if not credito_cci or cci_in != credito_cci:
        return {
            "cuenta_valida": False,
            "credito": None,
            "tipo": None,
            "dedup_ok": None,
            "mensaje": "Esa cuenta no corresponde a tu crédito.",
        }

    # (b) tipo de operación
    cuota = float(profile.get("cuota_esperada") or profile.get("next_installment_amount") or 0.0)
    saldo = float(profile.get("saldo_por_cancelar") or profile.get("balance") or 0.0)
    tipo = classify_tipo(monto, cuota, saldo)
    tipo_label = _TIPO_LABELS.get(tipo, tipo.upper())

    # (c) dedup por nº de operación (por crédito)
    nro = (nro_operacion or "").strip()
    items = _load_comprobantes()
    duplicate = any(
        r.get("nro_operacion") == nro and r.get("credito") == credito
        for r in items
    )
    dedup_ok = not duplicate

    sym = profile.get("currency_symbol", "S/")
    if duplicate:
        mensaje = (
            f"Este comprobante (operación {nro}) ya lo recibimos antes para tu "
            f"crédito {credito}. No lo registré de nuevo para evitar duplicados."
        )
    else:
        # Register for human reconciliation.
        items.append({
            "credito": credito,
            "dni": profile.get("dni"),
            "cci": cci_in,
            "monto": float(monto or 0.0),
            "nro_operacion": nro,
            "tipo": tipo,
            "estado": "en_revision",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        _save_comprobantes(items)
        mensaje = (
            f"Recibimos tu comprobante de pago. Lo registramos como {tipo_label} "
            f"sobre tu crédito {credito}, cuenta CCI ···{cci_in[-4:]}. "
            f"Será validado y, de estar conforme, se aplicará a tu cuenta."
        )

    return {
        "cuenta_valida": True,
        "credito": credito,
        "tipo": tipo,
        "dedup_ok": dedup_ok,
        "mensaje": mensaje,
    }
