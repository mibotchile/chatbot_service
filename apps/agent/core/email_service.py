"""Email delivery via internal SendGrid proxy at apiintranet.mibot.cl.

Sends brochure emails to customers and lead notifications to the sales team.
Gracefully degrades to logging when MAIL_API_URL is not configured.
"""

import httpx
from loguru import logger

DEFAULT_MAIL_API = "https://apiintranet.mibot.cl:8085/api/v2/mail_sengrid/send"


class EmailService:
    """Email delivery via internal mail API with graceful fallback to logging."""

    def __init__(self, api_url: str = "", from_email: str = "Sorelia Nova 🏠<sorelia@novainmobiliaria.pe>"):
        self.api_url = api_url or DEFAULT_MAIL_API
        self.from_email = from_email
        self._enabled = bool(api_url)
        if not self._enabled:
            logger.warning("EmailService: MAIL_API_URL not set -- emails will be logged only")

    async def send_brochure(
        self,
        to_email: str,
        customer_name: str,
        project_name: str,
        brochure_url: str,
        sales_agent: dict,
    ) -> bool:
        """Send brochure email to customer with project info and download link.

        Returns True if sent (or logged), False on failure.
        """
        agent_name = sales_agent.get("name", "Equipo Nova")
        agent_phone = sales_agent.get("phone", "908887233")

        subject = f"{project_name} — Tu brochure de Nova Inmobiliaria"
        html = _brochure_html(customer_name, project_name, brochure_url, agent_name, agent_phone)

        return await self._send(to_email, subject, html, event="brochure")

    async def notify_sales_agent(
        self,
        agent_email: str,
        lead: dict,
        project_name: str,
        conversation_id: str = "",
    ) -> bool:
        """Notify sales team about a new lead with contact info.

        Returns True if sent (or logged), False on failure.
        """
        lead_name = lead.get("name", "Sin nombre")
        subject = f"Nuevo lead: {lead_name} interesado en {project_name}"
        html = _sales_notification_html(lead, project_name, conversation_id)

        return await self._send(agent_email, subject, html, event="sales_notification")

    async def _send(self, to_email: str, subject: str, html_content: str, event: str) -> bool:
        """Send email via internal mail API or log if not configured."""
        if not self._enabled:
            logger.info(
                "[EMAIL-DRY-RUN] event={} to={} subject={}",
                event, to_email, subject,
            )
            logger.debug("[EMAIL-DRY-RUN] body:\n{}", html_content[:500])
            return True

        payload = {
            "from": self.from_email,
            "to": [to_email],
            "cc": "",
            "bcc": "",
            "subject": subject,
            "data": html_content,
            "attachments": [],
            "origin": "sorelia-agent",
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
                event, resp.status_code, resp.text[:300],
            )
            return False
        except httpx.RequestError as exc:
            logger.error("Mail API request failed event={} error={}", event, exc)
            return False


def _brochure_html(
    customer_name: str,
    project_name: str,
    brochure_url: str,
    agent_name: str,
    agent_phone: str,
) -> str:
    """HTML template for brochure delivery email."""
    return f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
  <div style="background: #1a1a2e; padding: 24px; text-align: center;">
    <h1 style="color: #fff; margin: 0; font-size: 22px;">Nova Inmobiliaria</h1>
  </div>
  <div style="padding: 24px;">
    <p>Hola <strong>{customer_name}</strong>,</p>
    <p>Gracias por tu interes en <strong>{project_name}</strong>. Aqui tienes el brochure
       con toda la informacion del proyecto:</p>
    <ul style="line-height: 1.8;">
      <li>Planos y tipologias disponibles</li>
      <li>Precios y opciones de financiamiento</li>
      <li>Amenities y acabados</li>
    </ul>
    <div style="text-align: center; margin: 24px 0;">
      <a href="{brochure_url}"
         style="background: #e63946; color: #fff; padding: 14px 32px; text-decoration: none;
                border-radius: 6px; font-weight: bold; font-size: 16px;">
        Descargar Brochure
      </a>
    </div>
    <p>Si tienes alguna consulta, puedes contactar directamente a tu asesor:</p>
    <p style="background: #f8f9fa; padding: 12px; border-radius: 6px;">
      <strong>{agent_name}</strong><br>
      WhatsApp: <a href="https://wa.me/51{agent_phone}">{agent_phone}</a>
    </p>
    <p style="color: #888; font-size: 12px; margin-top: 32px;">
      Este correo fue enviado por Sorelia, asistente virtual de Nova Inmobiliaria.
    </p>
  </div>
</body>
</html>"""


def _sales_notification_html(
    lead: dict,
    project_name: str,
    conversation_id: str,
) -> str:
    """HTML template for sales team notification."""
    name = lead.get("name", "Sin nombre")
    email = lead.get("email", "No proporcionado")
    phone = lead.get("phone", "No proporcionado")
    district = lead.get("district", "No especificado")
    purpose = lead.get("purpose", "No especificado")
    budget = lead.get("budget", "No especificado")
    bedrooms = lead.get("bedrooms", "No especificado")

    purpose_display = {
        "investment": "Inversion",
        "primary_home": "Vivienda propia",
    }.get(str(purpose), str(purpose))

    return f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
  <div style="background: #2d6a4f; padding: 24px; text-align: center;">
    <h1 style="color: #fff; margin: 0; font-size: 22px;">Nuevo Lead Capturado</h1>
  </div>
  <div style="padding: 24px;">
    <p>Se ha capturado un nuevo lead interesado en <strong>{project_name}</strong>
       a traves de Sorelia.</p>
    <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
      <tr style="border-bottom: 1px solid #eee;">
        <td style="padding: 8px; font-weight: bold;">Nombre</td>
        <td style="padding: 8px;">{name}</td>
      </tr>
      <tr style="border-bottom: 1px solid #eee;">
        <td style="padding: 8px; font-weight: bold;">Email</td>
        <td style="padding: 8px;">{email}</td>
      </tr>
      <tr style="border-bottom: 1px solid #eee;">
        <td style="padding: 8px; font-weight: bold;">Telefono</td>
        <td style="padding: 8px;">{phone}</td>
      </tr>
      <tr style="border-bottom: 1px solid #eee;">
        <td style="padding: 8px; font-weight: bold;">Distrito</td>
        <td style="padding: 8px;">{district}</td>
      </tr>
      <tr style="border-bottom: 1px solid #eee;">
        <td style="padding: 8px; font-weight: bold;">Proposito</td>
        <td style="padding: 8px;">{purpose_display}</td>
      </tr>
      <tr style="border-bottom: 1px solid #eee;">
        <td style="padding: 8px; font-weight: bold;">Presupuesto</td>
        <td style="padding: 8px;">{budget}</td>
      </tr>
      <tr style="border-bottom: 1px solid #eee;">
        <td style="padding: 8px; font-weight: bold;">Dormitorios</td>
        <td style="padding: 8px;">{bedrooms}</td>
      </tr>
      <tr>
        <td style="padding: 8px; font-weight: bold;">Proyecto</td>
        <td style="padding: 8px;">{project_name}</td>
      </tr>
    </table>
    <p style="color: #888; font-size: 12px;">
      Conversation ID: {conversation_id}<br>
      Capturado via Sorelia (chat web)
    </p>
  </div>
</body>
</html>"""
