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

from loguru import logger

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


def _title(s: str) -> str:
    """Title-case a name (CARLOS MENDOZA -> Carlos Mendoza)."""
    return " ".join(w.capitalize() for w in str(s or "").split())


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
        # Bank + payment account for the side-panel card. ``cci`` is the FULL
        # destination account the borrower must transfer to (shown complete in
        # the panel so they can pay); ``cci_masked`` is kept for any consumer
        # that still wants the masked form (e.g. chat copy).
        "banco": profile.get("banco"),
        "cci": profile.get("cci"),
        "cci_masked": _mask_cci(profile.get("cci")),
    }

    # PrestamYpe casuística: un mismo DNI puede tener VARIOS créditos vigentes.
    # Se exponen aquí para que el asistente los liste (saldo y estado de c/u).
    extra = profile.get("additional_credits") or []
    if extra:
        credits = [_credit_brief(profile, sym)]
        for c in extra:
            credits.append(_credit_brief(c, c.get("currency_symbol", sym)))
        summary["has_multiple_credits"] = True
        summary["credits_count"] = len(credits)
        summary["credits"] = credits

    # PrestamYpe casuística: un crédito GRUPAL es compartido por varios codeudores.
    # Se indica que el crédito es compartido y quiénes son los codeudores.
    if profile.get("is_grupal") and profile.get("codeudores"):
        summary["is_grupal"] = True
        summary["codeudores"] = [
            {
                "borrower_name": c.get("borrower_name"),
                "dni_masked": _mask_dni(c.get("dni")),
                "rol": c.get("rol", "codeudor"),
            }
            for c in profile["codeudores"]
        ]

    return summary


def _credit_brief(c: dict, sym: str) -> dict:
    """Compact view of a single credit (for the multi-credit casuística)."""
    bal = c.get("balance", 0.0) or 0.0
    return {
        "account_id": c.get("account_id"),
        "loan_number": c.get("loan_number"),
        "currency": c.get("currency", "PEN"),
        "currency_symbol": sym,
        "balance": bal,
        "balance_formatted": _fmt(bal, sym),
        "status": c.get("status"),
        "status_label": c.get("status_label"),
        "days_overdue": c.get("days_overdue", 0),
        "next_due_date": c.get("next_due_date"),
        "next_installment_amount": c.get("next_installment_amount"),
        "next_installment_formatted": _fmt(c.get("next_installment_amount", 0.0) or 0.0, sym),
        "banco": c.get("banco"),
        "cci": c.get("cci"),
        "cci_masked": _mask_cci(c.get("cci")),
    }


def _mask_cci(cci: str | None) -> str:
    """Mask a CCI to its last 4 digits (···7048) — never expose the full 20."""
    d = re.sub(r"\D", "", str(cci or ""))
    if len(d) < 4:
        return ""
    return f"···{d[-4:]}"


def _mask_dni(dni: str) -> str:
    """Mask a DNI to first 2 + last 1 digit (e.g. 40****4) — never expose full."""
    d = str(dni or "")
    if len(d) < 4:
        return ""
    return f"{d[:2]}{'*' * (len(d) - 3)}{d[-1]}"


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


# ── 6. Envío de información bajo demanda (CORE, data-driven) ───────────────
#
# Tenant-agnostic: the SENDABLE info types + their copy live in the tenant's
# responses.json under a ``_deliverables`` block (subject/body for correo, text
# for whatsapp), with {variables} from the verified profile — exactly like the
# curated responses. The engine fills them; zero tenant hardcode here.
#
# Demo vs prod is decided by ``delivery_mode``:
#   - "simulate" (tenant data_source == "mock") → NO real send. Ada confirms with
#     a MASKED destination ("…a tu correo c···@···.com"). Logged as simulated.
#   - "real" (tenant data_source == "doris")    → real send (SendGrid / ChatHub).
# The destination is ALWAYS the borrower's REGISTERED email/phone from the
# verified profile — never typed by the user (no document-leak vector).

_CANALES_INFO = {"correo", "whatsapp"}


def _normalize_canal(canal: str) -> str:
    """Map the many ways a user names a channel to {correo, whatsapp}.

    The data-driven ``elegir_canal`` intent captures the raw word the user typed
    (correo/email/whatsapp/wsp/…); normalize it here so the spec stays readable
    and the tool contract stays {correo, whatsapp}.
    """
    c = (canal or "").strip().lower()
    if c in ("correo", "email", "e-mail", "mail"):
        return "correo"
    if c in ("whatsapp", "whatsap", "wasap", "wsp", "wpp", "wa"):
        return "whatsapp"
    if "correo" in c or "mail" in c:
        return "correo"
    if "whats" in c or "wsp" in c or "wasap" in c:
        return "whatsapp"
    return c


def mask_email(addr: str) -> str:
    """Mask an email for confirmation copy: ``carlos@gmail.com`` → ``c···@···.com``.

    Shows the first char of the local part and the TLD only — enough for the
    borrower to recognize their own address without exposing it in full.
    """
    a = (addr or "").strip()
    if "@" not in a:
        return "···"
    local, _, domain = a.partition("@")
    first = local[:1] or "·"
    tld = domain.rsplit(".", 1)[-1] if "." in domain else domain
    return f"{first}···@···.{tld}" if tld else f"{first}···@···"


def mask_phone(phone: str) -> str:
    """Mask a phone for confirmation copy: ``951234567`` → ``···4567`` (last 4)."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 4:
        return "···"
    return f"···{digits[-4:]}"


def render_deliverable(spec: dict, channel: str, profile: dict) -> dict:
    """Render a deliverable's copy for a channel from its data-driven ``spec``.

    ``spec`` is one entry of the tenant's ``_deliverables`` block, e.g.::

        {"label": "estado de cuenta",
         "correo": {"subject": "...", "body": "...{saldo}..."},
         "whatsapp": {"text": "...{saldo}..."}}

    Returns ``{"subject", "body", "text", "label"}`` with {variables} filled from
    the verified profile via the responses engine (single + list multi-deuda).
    Imported lazily to avoid a hard dependency cycle with the responses module.
    """
    from core.responses import render_template

    label = spec.get("label", "información")
    out = {"label": label, "subject": "", "body": "", "text": ""}
    if channel == "correo":
        correo = spec.get("correo") or {}
        out["subject"] = render_template(correo.get("subject", ""), profile) or label
        out["body"] = render_template(correo.get("body") or correo.get("template"), profile)
    else:  # whatsapp
        wa = spec.get("whatsapp") or {}
        out["text"] = render_template(wa.get("text") or wa.get("template"), profile)
    return out


async def enviar_info(
    profile: dict,
    tipo: str,
    canal: str,
    *,
    deliverables: dict | None = None,
    delivery_mode: str = "simulate",
    email_service=None,
    chathub_outbound=None,
) -> dict:
    """Send a data-driven deliverable to the borrower's REGISTERED destination.

    CORE feature, tenant-agnostic. ``tipo`` is a key in the tenant's
    ``_deliverables`` spec; ``canal`` ∈ {correo, whatsapp}. The copy is rendered
    from that spec with the verified profile's data. The destination is the
    profile's own email/phone — masked in the confirmation, NEVER user-typed.

    ``delivery_mode``:
      - ``"simulate"`` (demo / mock fixture) → NO SendGrid/ChatHub call; Ada
        confirms with the masked destination; logged as simulated.
      - ``"real"`` → real send (SendGrid for correo, ChatHub outbound for WhatsApp).

    Returns a structured result the canned layer narrates. Never raises.
    """
    tipo_norm = (tipo or "").strip().lower()
    canal_norm = _normalize_canal(canal)
    spec_all = deliverables or {}
    spec = spec_all.get(tipo_norm)
    if not spec:
        return {"error": "tipo_no_disponible", "message": f"No tengo ese tipo de envío disponible: {tipo}."}
    if canal_norm not in _CANALES_INFO:
        return {
            "error": "canal_requerido",
            "message": "¿Te lo envío a tu correo o por WhatsApp?",
        }

    label = spec.get("label", "información")
    rendered = render_deliverable(spec, canal_norm, profile)
    name = _title(profile.get("borrower_name", ""))

    if canal_norm == "correo":
        destino = (profile.get("email") or "").strip()
        if not destino:
            return {
                "error": "sin_correo",
                "message": "No tengo un correo registrado en tu cuenta. ¿Quieres que te lo envíe por WhatsApp?",
            }
        masked = mask_email(destino)
        if delivery_mode == "real":
            sent = False
            if email_service:
                sent = await email_service.send_document(
                    destino, name, label,
                    summary_html=rendered.get("body", ""),
                )
            logger.info("enviar_info REAL correo tipo={} to_masked={} sent={}", tipo_norm, masked, sent)
        else:  # simulate (demo)
            sent = True
            logger.info("enviar_info SIMULADO correo tipo={} to_masked={} (sin SendGrid)", tipo_norm, masked)
        return {
            "delivered": bool(sent),
            "simulated": delivery_mode != "real",
            "canal": "correo",
            "tipo": tipo_norm,
            "doc_label": label,
            "destino_masked": masked,
            "message": (
                f"Listo, te enviamos tu {label} a tu correo {masked}."
                if sent else
                f"Intenté enviar tu {label} a tu correo {masked}; si no llega, dímelo y reintento."
            ),
        }

    # canal == whatsapp
    destino = (profile.get("phone") or "").strip()
    if not destino:
        return {
            "error": "sin_telefono",
            "message": "No tengo un número de WhatsApp registrado en tu cuenta. ¿Quieres que te lo envíe por correo?",
        }
    masked = mask_phone(destino)
    if delivery_mode == "real" and chathub_outbound and getattr(chathub_outbound, "is_configured", False):
        sent = await chathub_outbound.send_text(destino, rendered.get("text", ""))
        logger.info("enviar_info REAL whatsapp tipo={} to_masked={} sent={}", tipo_norm, masked, sent)
        channel_status = "configured"
    else:
        # demo OR ChatHub outbound not yet provisioned → simulate (honest).
        sent = True
        logger.info(
            "enviar_info SIMULADO whatsapp tipo={} to_masked={} (ChatHub outbound pendiente número+auth)",
            tipo_norm, masked,
        )
        channel_status = "configured" if (chathub_outbound and getattr(chathub_outbound, "is_configured", False)) else "chathub_pending"
    return {
        "delivered": bool(sent),
        "simulated": delivery_mode != "real" or channel_status == "chathub_pending",
        "canal": "whatsapp",
        "tipo": tipo_norm,
        "doc_label": label,
        "destino_masked": masked,
        "channel_status": channel_status,
        "message": f"Listo, te enviamos tu {label} a tu WhatsApp {masked}.",
    }


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

# Account-type labels for the destination account the user paid into. In Perú a
# CCI (Código de Cuenta Interbancario) has exactly 20 digits and is used for
# inter-bank transfers; a plain número de cuenta is shorter and used for
# same-bank transfers. We store the type so the human reconciler knows which.
_ACCOUNT_TYPE_LABELS = {"cci": "CCI", "cuenta": "Número de cuenta"}


def _normalize_account_type(account_type: str | None) -> str:
    """Coerce the destination-account type to 'cci' | 'cuenta' (default 'cci')."""
    t = (account_type or "").strip().lower()
    return t if t in _ACCOUNT_TYPE_LABELS else "cci"


def registrar_comprobante_foto(profile: dict, media_url: str) -> dict:
    """Register a voucher PHOTO (no OCR) for the verified borrower (PrestamYpe).

    Camino B del flujo "subir pago por WhatsApp": el deudor manda la FOTO del
    voucher en lugar de tipear los datos. NO hay OCR, así que NO clasificamos
    monto ni tipo: solo dejamos constancia de la imagen recibida, asociada al
    crédito verificado, para conciliación MANUAL posterior. ``estado`` queda en
    ``en_revision`` igual que el camino tipeado.

    Identity/credit ALWAYS come from the verified ``profile``. The ONLY input is
    the ``media_url`` (the chathub-hosted image). Dedup por media_url + crédito
    para no duplicar si el deudor reenvía la misma foto. Never raises.
    """
    credito = profile.get("account_id")
    url = (media_url or "").strip()
    items = _load_comprobantes()
    duplicate = any(
        r.get("media_url") == url and r.get("credito") == credito and url
        for r in items
    )
    if not duplicate:
        items.append({
            "credito": credito,
            "dni": profile.get("dni"),
            "media_url": url,
            "source": "foto",
            "monto": None,
            "nro_operacion": None,
            "tipo": None,
            "estado": "en_revision",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        _save_comprobantes(items)
    return {
        "registered": not duplicate,
        "duplicate": duplicate,
        "credito": credito,
        "media_url": url,
        "estado": "en_revision",
    }


async def validar_comprobante(
    profile: dict,
    monto: float,
    nro_operacion: str,
    cuenta_destino: str | None = None,
    account_type: str = "cci",
    cci: str | None = None,  # backward-compat alias for cuenta_destino
) -> dict:
    """Register a payment voucher for the verified borrower (PrestamYpe).

    The destination account is NOT validated for pertenencia: it is stored
    as-is as a voucher attribute (the real bank reconciliation is done later by
    a human). We never reject by account number.

    The account the user paid INTO can be entered as either a full CCI (Código
    de Cuenta Interbancario, exactly 20 digits — inter-bank transfer) or a plain
    número de cuenta (shorter — same-bank transfer). ``account_type`` records
    which one it is; ``cuenta_destino`` is the number (digits only). The legacy
    ``cci`` kwarg is still accepted as an alias for ``cuenta_destino``.

    Logic (against the server-injected ``profile`` = verified credit):
      (a) classify ``tipo`` from ``monto`` vs the DNI's credit cuota / saldo
          (±2% tolerance): ≈ cuota → "pago"; < cuota → "abono"; ≈ saldo total
          → "cancelacion". The classification depends ONLY on the monto.
      (b) dedup ``nro_operacion`` against a local JSON store; if seen before →
          ``dedup_ok = False`` (duplicate flagged), no re-registration.

    Identity/credit ALWAYS come from the verified ``profile`` — only the voucher
    fields (account_type, cuenta_destino, monto, nro_operacion) come from the
    user. The result is queued for human reconciliation. ``cuenta_valida`` is
    always ``True`` (kept for the widget contract); the only soft failure is a
    duplicate ``nro_operacion``.
    """
    account_type = _normalize_account_type(account_type)
    cuenta_in = normalize_cci(cuenta_destino if cuenta_destino is not None else (cci or ""))
    credito = profile.get("account_id")

    # (a) tipo de operación — contra el crédito del DNI (NO depende de la CCI)
    cuota = float(profile.get("cuota_esperada") or profile.get("next_installment_amount") or 0.0)
    saldo = float(profile.get("saldo_por_cancelar") or profile.get("balance") or 0.0)
    tipo = classify_tipo(monto, cuota, saldo)
    tipo_label = _TIPO_LABELS.get(tipo, tipo.upper())

    # (b) dedup por nº de operación (por crédito)
    nro = (nro_operacion or "").strip()
    items = _load_comprobantes()
    duplicate = any(
        r.get("nro_operacion") == nro and r.get("credito") == credito
        for r in items
    )
    dedup_ok = not duplicate

    if duplicate:
        mensaje = (
            f"Este comprobante (operación {nro}) ya lo recibimos antes para tu "
            f"crédito {credito}. No lo registré de nuevo para evitar duplicados."
        )
    else:
        # Register for human reconciliation. We store BOTH the account type
        # (cci | cuenta) and the number; ``cci`` is kept for backward compat
        # with any reader that still expects that key.
        items.append({
            "credito": credito,
            "dni": profile.get("dni"),
            "account_type": account_type,
            "cuenta_destino": cuenta_in,
            "cci": cuenta_in,  # legacy alias (same value as cuenta_destino)
            "monto": float(monto or 0.0),
            "nro_operacion": nro,
            "tipo": tipo,
            "estado": "en_revision",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        _save_comprobantes(items)
        cuenta_label = _ACCOUNT_TYPE_LABELS.get(account_type, "cuenta")
        mensaje = (
            f"Recibimos tu comprobante de pago. Lo registramos como {tipo_label} "
            f"sobre tu crédito {credito}, {cuenta_label} ···{cuenta_in[-4:]}. "
            f"Será validado y, de estar conforme, se aplicará a tu cuenta."
        )

    return {
        "cuenta_valida": True,
        "credito": credito,
        "tipo": tipo,
        "account_type": account_type,
        "cuenta_destino": cuenta_in,
        "dedup_ok": dedup_ok,
        "mensaje": mensaje,
    }
