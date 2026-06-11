"""Email delivery via internal SendGrid proxy at apiintranet.mibot.cl.

Sends cobranza documents (certificado de no adeudo, estado de cuenta) to
borrowers and lead notifications to the team. Documents are attached as base64
in the wrapper's ``attachments`` field. Gracefully degrades to logging when
MAIL_API_URL is not configured.
"""

import base64
from pathlib import Path

import httpx
from loguru import logger

DEFAULT_MAIL_API = "https://apiintranet.mibot.cl:8085/api/v2/mail_sengrid/send"

# Sentinel: no from_email configured — callers must pass company_name + from_email
# via send_document(). This is never used as an actual address.
_NO_FROM = ""


class EmailService:
    """Email delivery via internal mail API with graceful fallback to logging.

    ``from_email`` is intentionally left empty by default — each send_document
    call must supply the tenant-specific sender address (resolved from
    tenant.config.json → contact.email or a brand-specific key). This prevents
    any hardcoded brand from leaking through a forgotten constructor default.
    """

    def __init__(self, api_url: str = "", from_email: str = _NO_FROM):
        self.api_url = api_url or DEFAULT_MAIL_API
        self.from_email = from_email
        self._enabled = bool(api_url)
        if not self._enabled:
            logger.warning("EmailService: MAIL_API_URL not set -- emails will be logged only")

    async def send_document(
        self,
        to_email: str,
        customer_name: str,
        doc_label: str,
        *,
        pdf_path: str | Path | None = None,
        summary_html: str = "",
        company_name: str = "",
        agent_name: str = "",
        from_email: str = "",
        tenant_slug: str = "",
    ) -> bool:
        """Send a cobranza document to the borrower.

        ``doc_label`` is human text, e.g. "Certificado de no adeudo" or
        "Estado de cuenta". If ``pdf_path`` is given the PDF is attached as
        base64; otherwise ``summary_html`` carries the info in the body.
        ``company_name`` and ``agent_name`` come from tenant config (name +
        agent.agent_name). ``from_email`` overrides self.from_email when given.
        ``tenant_slug`` builds the mail-API ``origin`` field
        (``{slug}-cobranza``) so the proxy keeps per-tenant routing.
        Returns True if sent (or logged in dry-run).
        """
        _company = company_name or self.from_email or "Cobranza"
        if not company_name:
            logger.warning(
                "EmailService.send_document: company_name not provided; "
                "falling back to '{}'. Set tenant config name.",
                _company,
            )
        subject = f"{doc_label} — {_company}"
        body = _document_html(
            customer_name, doc_label, summary_html,
            company_name=_company, agent_name=agent_name,
        )

        attachments = []
        if pdf_path:
            p = Path(pdf_path)
            if p.exists():
                attachments.append(
                    {
                        "filename": p.name,
                        "content": base64.b64encode(p.read_bytes()).decode("ascii"),
                        "type": "application/pdf",
                    }
                )
            else:
                logger.warning("send_document: PDF not found at {}", p)

        return await self._send(
            to_email, subject, body,
            event="document", attachments=attachments, from_email=from_email,
            tenant_slug=tenant_slug,
        )

    async def _send(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        event: str,
        attachments: list[dict] | None = None,
        from_email: str = "",
        tenant_slug: str = "",
    ) -> bool:
        """Send email via internal mail API or log if not configured."""
        attachments = attachments or []
        if not self._enabled:
            logger.info(
                "[EMAIL-DRY-RUN] event={} to={} subject={} attachments={}",
                event,
                to_email,
                subject,
                [a.get("filename") for a in attachments],
            )
            logger.debug("[EMAIL-DRY-RUN] body:\n{}", html_content[:500])
            return True

        _effective_from = from_email or self.from_email
        if not _effective_from:
            logger.warning(
                "EmailService._send: no from_email configured; "
                "email may be rejected by the mail API."
            )
        payload = {
            "from": _effective_from,
            "to": [to_email],
            "cc": "",
            "bcc": "",
            "subject": subject,
            "data": html_content,
            "attachments": attachments,
            # Per-tenant origin so the mail proxy keeps routing/filtering by it
            # (prestaunion emits exactly the historical "prestaunion-cobranza").
            "origin": f"{tenant_slug}-cobranza" if tenant_slug else "cobranza",
        }

        try:
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.post(
                    self.api_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=15.0,
                )
            if resp.status_code in (200, 201, 202):
                logger.info("Email sent event={} to={}", event, to_email)
                return True
            logger.error(
                "Mail API error event={} status={} body={}",
                event,
                resp.status_code,
                resp.text[:300],
            )
            return False
        except httpx.RequestError as exc:
            logger.error("Mail API request failed event={} error={}", event, exc)
            return False


def _document_html(
    customer_name: str,
    doc_label: str,
    summary_html: str = "",
    company_name: str = "",
    agent_name: str = "",
) -> str:
    """HTML body for a cobranza document email (Peruvian Spanish).

    ``company_name`` and ``agent_name`` come from tenant config — no brand
    is hardcoded here. Falls back to generic labels when not provided (with a
    loguru warning emitted by the caller).
    """
    _co = company_name or "la entidad"
    _agent = agent_name or "el asistente virtual"
    extra = f'<div style="margin: 16px 0;">{summary_html}</div>' if summary_html else ""
    attach_note = (
        "<p>Adjuntamos el documento solicitado en formato PDF.</p>" if not summary_html else ""
    )
    return f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; color: #1A1A1C; max-width: 600px; margin: 0 auto;">
  <div style="background: #0083E0; padding: 24px; text-align: center;">
    <h1 style="color: #fff; margin: 0; font-size: 22px;">{_co}</h1>
  </div>
  <div style="padding: 24px;">
    <p>Hola <strong>{customer_name}</strong>,</p>
    <p>Gracias por tu confianza. Te enviamos tu <strong>{doc_label}</strong>.</p>
    {attach_note}
    {extra}
    <p>Si necesitas algo más, escríbenos por este medio y con gusto te ayudamos.</p>
    <p style="color: #888; font-size: 12px; margin-top: 32px;">
      Este correo fue enviado por {_agent}, asistente virtual de {_co}. Datos de demostración.
    </p>
  </div>
</body>
</html>"""
