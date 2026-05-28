"""Chathub inbound adapter — translates chathub's ``/chat`` contract to the
cobranza engine and back.

chathub (NestJS, WhatsApp transport) calls ``POST ${OLIMPO_URL}/<botPath>/chat``
with an Olimpo-style payload and expects one of three response shapes (text /
interactive / redirect). This module is the ONLY translation layer: it reuses
the existing engine (SoreliaAgent + ToolRegistry + identity gate + analytics
sink) unchanged, behind a thin request/response mapping.

Contract reference: reports/chathub-cobranza-integration-spec-2026-05-26.md
(sections A, B, E). Quirks honored:
  · ``message`` may carry several client bubbles joined by ``\n`` (chathub
    debounce) — normalized into one engine turn.
  · ``response`` (text path) is run through TurndownService (HTML→md) by
    chathub, so we emit plain text / simple markdown, NEVER HTML.
  · identity: a ``CT-<token>`` in the first message resolves debtor + cartera
    (the second factor that closes the WhatsApp gate); DNI-first is the engine
    fallback. The binding ``chathub_conversation_id → conversation`` lives in
    the engine STATE across turns (WhatsApp is async).
  · handoff: the engine's ``escalate_to_human`` tool surfaces in ``tool_pairs``
    → mapped to ``type:"redirect"`` with a ``receiver`` (agent email / group id).

This module deliberately keeps NO LLM/business logic of its own — it composes
``run_engine_turn`` (injected by the API layer) so tests can mock the engine
without touching Doris/Anthropic.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Awaitable, Callable

from loguru import logger
from pydantic import BaseModel, Field, field_validator

# ── Request / response models (chathub Olimpo contract) ──────────────────────


class ChathubChatRequest(BaseModel):
    """Body chathub POSTs to ``/<botPath>/chat`` (spec A.2)."""

    channel_id: str = ""
    message: str = Field(..., max_length=4000)
    unique_id: str = ""
    platform: str = "chathub"
    chathub_conversation_id: str = ""
    chathub_project_id: str = ""
    url: str | None = None

    @field_validator("message")
    @classmethod
    def _strip(cls, v: str) -> str:
        # Keep newlines (debounced bubbles), just trim outer whitespace.
        return (v or "").strip()


# Token format: CT-<alnum/_/-/.>. Matched anywhere in the (possibly multi-line)
# first message; the campaign deep-link is wa.me/<num>?text=CT-<token>.
_CT_TOKEN_RE = re.compile(r"\bCT-([A-Za-z0-9_\-.]{1,64})\b")


def extract_ct_token(message: str) -> str | None:
    """Return the ``CT-...`` campaign token from the message, else None.

    The returned value is the FULL ``CT-<token>`` (the resolver receives the raw
    token; what maps to a borrower is tenant-defined in the debt source)."""
    if not message:
        return None
    m = _CT_TOKEN_RE.search(message)
    return f"CT-{m.group(1)}" if m else None


def normalize_message(message: str) -> str:
    """Collapse chathub's debounced multi-bubble message into one turn.

    chathub joins several client bubbles with ``\n`` (spec A.5). We keep the
    content but normalize whitespace per line and drop empty lines so the engine
    sees a single coherent user turn."""
    if not message:
        return ""
    # Split on any newline, drop empty lines, then collapse all runs of
    # whitespace (incl. intra-line doubles) into single spaces.
    lines = [ln for ln in (ln.strip() for ln in message.replace("\r\n", "\n").split("\n")) if ln]
    return " ".join(" ".join(lines).split())


# ── Tenant routing (bot_path → tenant_id) ────────────────────────────────────

_SLUG_RE = re.compile(r"[a-z0-9_\-]{1,64}")


def _botpath_map() -> dict[str, str]:
    """Parse COBRANZA_CHATHUB_BOTPATH_MAP (JSON {botPath: tenant_id}). Empty on
    error — routing then falls back to sanitized-slug == tenant_id."""
    raw = os.environ.get("COBRANZA_CHATHUB_BOTPATH_MAP", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        logger.warning("COBRANZA_CHATHUB_BOTPATH_MAP is not valid JSON — ignoring")
        return {}


def sanitize_bot_path(bot_path: str) -> str:
    """Reduce a raw bot_path to a safe slug (strip slashes/whitespace, lower)."""
    return (bot_path or "").strip().strip("/").strip().lower()


def resolve_tenant(bot_path: str, tenant_exists: Callable[[str], bool]) -> str | None:
    """Resolve the tenant_id for a bot_path.

    Order: explicit COBRANZA_CHATHUB_BOTPATH_MAP entry (keyed by raw bot_path OR
    by its sanitized slug), then fallback where the sanitized slug IS the tenant
    slug. Returns None when nothing resolves to a real tenant (caller → 404)."""
    slug = sanitize_bot_path(bot_path)
    mapping = _botpath_map()
    # Map may be keyed by the raw path ("/prestamype/") or the slug ("prestamype").
    candidate = mapping.get(bot_path) or mapping.get(slug)
    if candidate and tenant_exists(candidate):
        return candidate
    # Fallback: sanitized slug == tenant slug (e.g. /prestamype/ → "prestamype").
    if slug and _SLUG_RE.fullmatch(slug) and tenant_exists(slug):
        return slug
    return None


# ── Auth (shared secret, optional — spec G.5) ────────────────────────────────

CHATHUB_TOKEN_HEADER = "X-Chathub-Token"


def check_auth(header_value: str | None) -> bool:
    """Validate the chathub shared-secret header.

    If COBRANZA_CHATHUB_TOKEN is unset/empty the endpoint is OPEN (compat with
    chathub's current no-auth client). If set, the header MUST match it."""
    expected = (os.environ.get("COBRANZA_CHATHUB_TOKEN") or "").strip()
    if not expected:
        return True
    return bool(header_value) and header_value.strip() == expected


# ── Handoff receiver resolution (spec B) ─────────────────────────────────────


def resolve_handoff_receiver(tenant_cfg: dict | None) -> dict:
    """Build the chathub ``receiver`` for a handoff.

    Reads the tenant's ``handoff`` block; falls back to a default group queue.
    Shapes (spec B.1):
      · agent → {"type":"agent","identifier":"<email>"}   (chathub resolves to user.<id>)
      · group → {"type":"group","identifier":"<id>"}       (queue; "1" is chathub's default)

    Resolution order:
      1. tenant.config.json "handoff": {"type":"agent"|"group","identifier":"..."}
      2. COBRANZA_CHATHUB_HANDOFF_RECEIVER env (JSON, same shape)
      3. default group "1"
    """
    cfg_handoff = (tenant_cfg or {}).get("handoff") if isinstance(tenant_cfg, dict) else None
    candidate = cfg_handoff
    if not candidate:
        raw = (os.environ.get("COBRANZA_CHATHUB_HANDOFF_RECEIVER") or "").strip()
        if raw:
            try:
                candidate = json.loads(raw)
            except (ValueError, TypeError):
                candidate = None
    if isinstance(candidate, dict):
        rtype = str(candidate.get("type", "")).lower()
        identifier = str(candidate.get("identifier", "")).strip()
        if rtype in ("agent", "group") and identifier:
            return {"type": rtype, "identifier": identifier}
    return {"type": "group", "identifier": "1"}


# ── Response building ────────────────────────────────────────────────────────


def was_escalated(tool_pairs: list[tuple[str, dict]]) -> bool:
    """True when the engine ran ``escalate_to_human`` (handoff signal)."""
    for name, result in tool_pairs or []:
        if name == "escalate_to_human" and isinstance(result, dict) and result.get("escalated"):
            return True
    return False


def _interactive_content(ui_actions: dict) -> dict | None:
    """If the engine produced a chathub-shaped interactive payload, return it.

    The engine's ``ui_actions`` may carry an ``interactive`` block already in the
    chathub InteractiveMessage shape (header?/body.text/footer?/action). We pass
    it through verbatim. None when there's nothing interactive to send (most
    turns are plain text)."""
    if not isinstance(ui_actions, dict):
        return None
    interactive = ui_actions.get("interactive")
    if isinstance(interactive, dict) and interactive.get("body"):
        return interactive
    return None


def build_chathub_response(
    *,
    engine_result: dict,
    unique_id: str,
    tenant_cfg: dict | None,
) -> dict:
    """Map an engine turn result to one of the three chathub shapes (spec A.3/B.1).

    Priority: handoff (redirect) > interactive > text. ``response`` and
    ``unique_id`` are always present; ``thought`` is omitted (chathub ignores it)
    unless the engine surfaced one."""
    content = (engine_result.get("content") or "").strip()
    tool_pairs = engine_result.get("tool_pairs") or []
    ui_actions = engine_result.get("ui_actions") or {}

    # 1) Handoff → redirect.
    if was_escalated(tool_pairs):
        receiver = resolve_handoff_receiver(tenant_cfg)
        farewell = content or "Te derivo con un asesor. Aguarda un momento, por favor."
        return {
            "type": "redirect",
            "response": farewell,
            "content": {"receiver": receiver},
            "unique_id": unique_id,
        }

    # 2) Interactive → buttons / list.
    interactive = _interactive_content(ui_actions)
    if interactive is not None:
        return {
            "type": "interactive",
            "response": content,
            "content": interactive,
            "unique_id": unique_id,
        }

    # 3) Plain text (default).
    return {
        "type": "text",
        "response": content,
        "unique_id": unique_id,
    }


# ── Adapter ──────────────────────────────────────────────────────────────────

# An engine runner: given the normalized turn it runs the full cobranza engine
# (store → identity gate → SoreliaAgent → analytics) and returns the engine
# result dict (the same shape SoreliaAgent.process_message produces, augmented
# with the conversation_id). Injected by the API layer so tests can mock it.
EngineRunner = Callable[..., Awaitable[dict]]


class ChathubChatAdapter:
    """Translate a chathub ``/chat`` request to an engine turn and back.

    The heavy lifting (LLM, tools, gate, analytics, state) is delegated to the
    injected ``engine_runner``; this class owns ONLY the chathub-specific
    translation: identity-token extraction, multi-line normalization, conversation
    id binding, and response shaping."""

    def __init__(self, engine_runner: EngineRunner):
        self._run_engine = engine_runner

    @staticmethod
    def conversation_id_for(tenant_id: str, chathub_conversation_id: str) -> str:
        """Deterministic, stable conversation id per (tenant, chathub conv).

        WhatsApp is asynchronous: the same ``chathub_conversation_id`` maps to the
        same engine conversation across turns, so the identity gate (token/DNI)
        stays resolved. Namespaced by tenant to avoid cross-tenant collisions."""
        return f"chathub-{tenant_id}-{chathub_conversation_id or 'anon'}"

    async def handle(
        self,
        *,
        body: ChathubChatRequest,
        tenant_id: str,
        tenant_cfg: dict | None,
    ) -> dict:
        """Process one inbound chathub turn → chathub response dict."""
        text = normalize_message(body.message)
        token = extract_ct_token(body.message)
        conversation_id = self.conversation_id_for(tenant_id, body.chathub_conversation_id)

        logger.info(
            "chathub turn | tenant={} conv={} token={} chars={}",
            tenant_id, conversation_id, "yes" if token else "no", len(text),
        )

        engine_result = await self._run_engine(
            text=text,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            campaign_token=token,
            channel="whatsapp",
            chathub_conversation_id=body.chathub_conversation_id,
            chathub_project_id=body.chathub_project_id,
            channel_id=body.channel_id,
        )

        return build_chathub_response(
            engine_result=engine_result,
            unique_id=body.unique_id,
            tenant_cfg=tenant_cfg,
        )
