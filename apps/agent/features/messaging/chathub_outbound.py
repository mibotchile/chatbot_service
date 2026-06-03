"""ChatHub outbound client — REAL WhatsApp delivery for the cobranza agent.

WHY this exists
---------------
WhatsApp used to go through Evolution (``whatsapp_service``). That path is being
RETIRED: outbound WhatsApp now goes through ChatHub's ``/messages/send`` endpoint
(Movistar/ChatHub pattern). ChatHub requires Firebase auth + a provisioned number,
which are PENDING for prestamype. So this adapter is the seam:

  - When ``COBRANZA_CHATHUB_OUTBOUND_URL`` (and any required auth) is configured,
    ``send_text`` POSTs the message to ChatHub for real delivery.
  - When it is NOT configured (the current state) OR the caller is in demo mode,
    the send is SIMULATED — logged honestly, never faked as a real ChatHub call.

This keeps the engine free of Evolution and gives a single, swappable place to
activate real WhatsApp once ChatHub has the number + auth. The cobranza tool
(``enviar_info``) only ever asks this client to ``send_text`` to the borrower's
REGISTERED phone; it never depends on Evolution.

Activation checklist (when ChatHub is ready):
  1. Provision the WhatsApp number in ChatHub for the prestamype channel.
  2. Set ``COBRANZA_CHATHUB_OUTBOUND_URL`` to the ``/messages/send`` URL.
  3. Set ``COBRANZA_CHATHUB_OUTBOUND_TOKEN`` (Firebase / bearer) if required.
  4. Flip the tenant to ``data_source: doris`` (production) so delivery_mode=real.
"""

from __future__ import annotations

import httpx
from loguru import logger


class ChathubOutboundClient:
    """Outbound WhatsApp via ChatHub ``/messages/send`` (replaces Evolution).

    ``is_configured`` is True only when a URL is set. An unconfigured client is a
    no-op that reports an honest dry-run (the caller decides whether to simulate),
    so the chat never breaks and we never fake a real send.
    """

    def __init__(
        self,
        url: str = "",
        *,
        token: str = "",
        channel_id: str = "",
        timeout: float = 10.0,
        verify_ssl: bool = False,
    ):
        self.url = (url or "").rstrip("/")
        self._token = token
        self.channel_id = channel_id
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self._enabled = bool(self.url)
        if not self._enabled:
            logger.info(
                "ChathubOutboundClient: COBRANZA_CHATHUB_OUTBOUND_URL not set — "
                "outbound WhatsApp will be SIMULATED (activate when ChatHub has "
                "the number + auth)."
            )

    @property
    def is_configured(self) -> bool:
        """True when ChatHub outbound is wired (URL present)."""
        return self._enabled

    async def send_text(self, phone: str, message: str) -> bool:
        """Send a plain-text WhatsApp message to ``phone`` via ChatHub.

        Returns True on a 2xx ChatHub response. When not configured, logs an
        honest dry-run and returns False (the caller treats it as "not really
        delivered" / simulate). Never raises into the chat flow.
        """
        if not self._enabled:
            logger.info(
                "[CHATHUB-OUTBOUND-DRY-RUN] to={} message={}", phone, message[:120]
            )
            return False

        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        payload: dict = {"to": phone, "message": message}
        if self.channel_id:
            payload["channelId"] = self.channel_id

        try:
            async with httpx.AsyncClient(verify=self.verify_ssl) as client:
                resp = await client.post(
                    self.url, json=payload, headers=headers, timeout=self.timeout
                )
            if resp.status_code in (200, 201, 202):
                logger.info("ChatHub outbound sent to={}", phone)
                return True
            logger.error(
                "ChatHub outbound error status={} body={}",
                resp.status_code, resp.text[:300],
            )
            return False
        except httpx.RequestError as exc:
            logger.error("ChatHub outbound request failed to={} error={}", phone, exc)
            return False
