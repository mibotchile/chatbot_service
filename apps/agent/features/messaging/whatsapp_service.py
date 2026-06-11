"""WhatsApp messaging via Evolution API.

Sends messages, media, and lead notifications to sales agents.
Gracefully degrades to logging when WHATSAPP_API_URL is not configured.
"""

import asyncio
import random
import re

import httpx
from loguru import logger


def markdown_to_whatsapp(text: str) -> str:
    """Convert markdown formatting to WhatsApp formatting.

    Markdown → WhatsApp:
      **bold** → *bold*
      *italic* → _italic_ (only single *)
      - bullet → bullet with emoji
      ## heading → *heading* (bold)
      [link](url) → url
      \\n\\n → \\n (WhatsApp doesn't need double newlines)
    """
    # Headers → bold
    text = re.sub(r"^#{1,3}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    # Bold **text** → *text*
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    # Links [text](url) → text: url
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1: \2", text)
    # Bullet points - text → • text
    text = re.sub(r"^[\-\*]\s+", "• ", text, flags=re.MULTILINE)
    # Numbered lists keep as-is
    # Remove excessive newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_phone(phone: str) -> str:
    """Normalize phone to country_code + number format (no +, no spaces).

    Examples:
        "925797402"       -> "51925797402"
        "+51 925 797 402" -> "51925797402"
        "51925797402"     -> "51925797402"
        "9 2579-7402"     -> "51925797402"
    """
    cleaned = re.sub(r"[^0-9]", "", phone)
    if cleaned.startswith("9") and len(cleaned) == 9:
        cleaned = f"51{cleaned}"
    return cleaned


class WhatsAppService:
    """WhatsApp delivery via Evolution API with graceful fallback to logging."""

    def __init__(self, api_url: str, api_key: str, instance_name: str):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.instance_name = instance_name
        self._enabled = bool(api_url and api_key and instance_name)
        if not self._enabled:
            logger.warning("WhatsAppService: not fully configured -- messages will be logged only")

    @property
    def is_configured(self) -> bool:
        """True when Evolution is fully wired (vs demo dry-run/backlog)."""
        return self._enabled

    async def _mark_as_read(self, phone: str, message_id: str | None = None) -> None:
        """Mark messages as read (blue ticks)."""
        if not self._enabled:
            return
        try:
            url = f"{self.api_url}/chat/markMessageAsRead/{self.instance_name}"
            payload = {
                "readMessages": [{"remoteJid": f"{phone}@s.whatsapp.net", "id": message_id or ""}]
            }
            async with httpx.AsyncClient(verify=False) as client:
                await client.put(url, json=payload, headers={"apikey": self.api_key}, timeout=5.0)
        except Exception:
            pass  # non-critical

    async def _set_presence(self, phone: str, presence: str = "composing") -> None:
        """Set typing/recording presence. presence: 'composing' or 'paused'."""
        if not self._enabled:
            return
        try:
            url = f"{self.api_url}/chat/sendPresence/{self.instance_name}"
            payload = {"number": f"{phone}@s.whatsapp.net", "presence": presence}
            async with httpx.AsyncClient(verify=False) as client:
                await client.post(url, json=payload, headers={"apikey": self.api_key}, timeout=5.0)
        except Exception:
            pass  # non-critical

    async def _send_with_retry(
        self,
        url: str,
        payload: dict,
        *,
        max_retries: int = 3,
        timeout: float = 15.0,
    ) -> bool:
        base_delay = 1.0
        for attempt in range(1 + max_retries):
            try:
                async with httpx.AsyncClient(verify=False) as client:
                    resp = await client.post(
                        url,
                        json=payload,
                        headers={"apikey": self.api_key, "Content-Type": "application/json"},
                        timeout=timeout,
                    )
                if resp.status_code in (200, 201):
                    return True
                if 400 <= resp.status_code < 500:
                    logger.error(
                        "WhatsApp API client error status={} body={} (no retry)",
                        resp.status_code,
                        resp.text[:300],
                    )
                    return False
                logger.warning(
                    "WhatsApp API server error status={} attempt={}/{}",
                    resp.status_code,
                    attempt + 1,
                    1 + max_retries,
                )
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                logger.warning(
                    "WhatsApp request error={} attempt={}/{}",
                    exc,
                    attempt + 1,
                    1 + max_retries,
                )

            if attempt < max_retries:
                delay = base_delay * (2**attempt)
                logger.info("Retrying in {}s...", delay)
                await asyncio.sleep(delay)

        logger.error("WhatsApp send failed after {} attempts", 1 + max_retries)
        return False

    async def _simulate_human(
        self, phone: str, message: str, incoming_id: str | None = None
    ) -> None:
        """Simulate human behavior: read → pause → typing → send."""
        # Mark as read
        await self._mark_as_read(phone, incoming_id)
        # Small pause before typing (humans read first)
        await asyncio.sleep(random.uniform(0.5, 1.5))
        # Start typing
        await self._set_presence(phone, "composing")
        # Typing duration proportional to message length (humans type ~40 chars/sec on phone)
        typing_time = min(max(len(message) / 40, 1.0), 4.0)
        await asyncio.sleep(typing_time)

    async def is_saved_contact(self, phone: str) -> bool:
        """Check if a phone number is a saved contact in the WhatsApp instance.

        Uses Evolution API /chat/findContacts to query the synced address book.
        Returns False on error (fail-open: don't block messages if API is down).
        """
        phone = normalize_phone(phone)
        if not self._enabled:
            return False

        try:
            url = f"{self.api_url}/chat/findContacts/{self.instance_name}"
            payload = {"where": {"remoteJid": f"{phone}@s.whatsapp.net"}}
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={"apikey": self.api_key},
                    timeout=5.0,
                )
            if resp.status_code in (200, 201):
                contacts = resp.json()
                if isinstance(contacts, list) and len(contacts) > 0:
                    logger.debug(
                        "Phone {} is a saved contact (found {} records)", phone, len(contacts)
                    )
                    return True
            return False
        except Exception as exc:
            logger.warning("Contact check failed for {}: {}", phone, exc)
            return False

    async def send_text(self, phone: str, message: str, incoming_id: str | None = None) -> bool:
        """Send a text message with human-like behavior."""
        phone = normalize_phone(phone)

        if not self._enabled:
            logger.info("[WA-DRY-RUN] to={} message={}", phone, message[:100])
            return True

        # Format markdown → WhatsApp
        message = markdown_to_whatsapp(message)

        # Human behavior: read → type → send
        await self._simulate_human(phone, message, incoming_id)

        url = f"{self.api_url}/message/sendText/{self.instance_name}"
        payload = {"number": phone, "text": message}

        try:
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={"apikey": self.api_key, "Content-Type": "application/json"},
                    timeout=15.0,
                )
            if resp.status_code in (200, 201):
                logger.info("WhatsApp text sent to={}", phone)
                return True
            logger.error(
                "WhatsApp API error status={} body={}",
                resp.status_code,
                resp.text[:300],
            )
            return False
        except httpx.RequestError as exc:
            logger.error("WhatsApp request failed to={} error={}", phone, exc)
            return False

    async def send_media(
        self,
        phone: str,
        media_url: str,
        caption: str = "",
        media_type: str = "document",
    ) -> bool:
        """Send media (PDF, image) via WhatsApp."""
        phone = normalize_phone(phone)

        if not self._enabled:
            logger.info("[WA-DRY-RUN] media to={} type={} url={}", phone, media_type, media_url)
            return True

        url = f"{self.api_url}/message/sendMedia/{self.instance_name}"
        payload = {
            "number": phone,
            "mediatype": media_type,
            "media": media_url,
            "caption": caption,
        }

        try:
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={"apikey": self.api_key, "Content-Type": "application/json"},
                    timeout=30.0,
                )
            if resp.status_code in (200, 201):
                logger.info("WhatsApp media sent to={} type={}", phone, media_type)
                return True
            logger.error(
                "WhatsApp media API error status={} body={}",
                resp.status_code,
                resp.text[:300],
            )
            return False
        except httpx.RequestError as exc:
            logger.error("WhatsApp media request failed to={} error={}", phone, exc)
            return False

    async def send_document(
        self,
        phone: str,
        customer_name: str,
        doc_label: str,
        media_url: str = "",
        caption: str = "",
        company_name: str = "",
    ) -> bool:
        """Send a cobranza document (PDF) to the borrower via WhatsApp.

        Uses Evolution /message/sendMedia with the PDF URL + caption. When
        Evolution is not configured (demo default), send_media logs an honest
        dry-run — we never fake a successful delivery.
        ``company_name`` comes from tenant config; falls back to a generic
        phrase when not provided.
        """
        _suffix = f" de {company_name}" if company_name else ""
        cap = caption or f"Hola {customer_name}, aquí tienes tu {doc_label}{_suffix}."
        if not media_url:
            # No URL to attach yet — log honestly and report not-sent.
            logger.info(
                "[WA-DRY-RUN] document to={} doc={} (sin media_url, no se adjunta)",
                normalize_phone(phone),
                doc_label,
            )
            return False
        return await self.send_media(phone, media_url, caption=cap, media_type="document")

    async def send_buttons(
        self,
        phone: str,
        payload: dict,
    ) -> bool:
        """Send interactive button message (≤3 buttons) via Evolution API."""
        phone = normalize_phone(phone)

        if not self._enabled:
            logger.info("[WA-DRY-RUN] buttons to={} payload={}", phone, str(payload)[:200])
            return True

        url = f"{self.api_url}/message/sendButtons/{self.instance_name}"
        payload = {**payload, "number": phone}

        try:
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={"apikey": self.api_key, "Content-Type": "application/json"},
                    timeout=15.0,
                )
            if resp.status_code in (200, 201):
                logger.info("WhatsApp buttons sent to={}", phone)
                return True
            logger.error(
                "WhatsApp buttons API error status={} body={}", resp.status_code, resp.text[:300]
            )
            return False
        except httpx.RequestError as exc:
            logger.error("WhatsApp buttons request failed to={} error={}", phone, exc)
            return False

    async def send_list(
        self,
        phone: str,
        payload: dict,
    ) -> bool:
        """Send interactive list message (4-10 rows) via Evolution API."""
        phone = normalize_phone(phone)

        if not self._enabled:
            logger.info("[WA-DRY-RUN] list to={} payload={}", phone, str(payload)[:200])
            return True

        url = f"{self.api_url}/message/sendList/{self.instance_name}"
        payload = {**payload, "number": phone}

        try:
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={"apikey": self.api_key, "Content-Type": "application/json"},
                    timeout=15.0,
                )
            if resp.status_code in (200, 201):
                logger.info("WhatsApp list sent to={}", phone)
                return True
            logger.error(
                "WhatsApp list API error status={} body={}", resp.status_code, resp.text[:300]
            )
            return False
        except httpx.RequestError as exc:
            logger.error("WhatsApp list request failed to={} error={}", phone, exc)
            return False

    async def send_formatted(self, messages: list[dict]) -> None:
        """Dispatch a list of formatter payloads to the appropriate send method."""
        for msg in messages:
            msg_type = msg.get("type")
            phone = msg.get("phone", "")
            payload = msg.get("payload", {})

            if msg_type == "text":
                await self.send_text(phone, payload.get("text", ""))
            elif msg_type == "media":
                media_url = payload.get("media", "")
                if not media_url.startswith("http"):
                    media_url = f"https://demos.mibot.cl{media_url}"
                await self.send_media(
                    phone,
                    media_url,
                    caption=payload.get("caption", ""),
                    media_type=payload.get("mediatype", "image"),
                )
            elif msg_type == "buttons":
                await self.send_buttons(phone, payload)
            elif msg_type == "list":
                await self.send_list(phone, payload)

            # Pace between messages to avoid rate limits
            await asyncio.sleep(1.0)
