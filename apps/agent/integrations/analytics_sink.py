"""Analytics sink — fire-and-forget writes to Doris (pydoris Stream Load).

Two OLAP tables in ``cobranza_analytics`` (Doris, DUPLICATE KEY):
  - ``bot_interactions`` : one row per chat turn message (role user / assistant).
  - ``bot_llm_usage``    : one row per turn with token usage + computed cost.

Transport: **pydoris** ``DorisClient.write`` (Stream Load over the FE *HTTP*
port 8030, NOT the 9030 MySQL wire port the read path uses). pydoris.write is
synchronous (``requests``) and prints on success, so we run it in a thread via
``asyncio.to_thread`` and silence its stdout.

CONTRACT (non-negotiable): every public function is fire-and-forget. ALL work is
wrapped in try/except — on ANY error we log and return. An analytics failure
must NEVER surface to the chat request. If analytics is not configured
(``settings.analytics_host`` empty) every call is a cheap no-op.

datetime_utc is ALWAYS ``datetime.now(timezone.utc)`` formatted ``%Y-%m-%d
%H:%M:%S`` (Doris DATETIME has no tz; we standardize on UTC).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from loguru import logger

from config.pricing import compute_cost_usd
from config.settings import settings

_INTERACTIONS_TABLE = "bot_interactions"
_LLM_USAGE_TABLE = "bot_llm_usage"


def _now_utc() -> str:
    """Current UTC timestamp formatted for a Doris DATETIME column."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def analytics_enabled() -> bool:
    """True when a Doris analytics host is configured (else every call no-ops)."""
    return bool((settings.analytics_host or "").strip())


@lru_cache(maxsize=1)
def _client():
    """Build the pydoris client once (lazy import so the dep is optional).

    pydoris ``DorisClient`` takes the FE HTTP port and uses it for Stream Load.
    Cached because it holds a ``requests.Session``.
    """
    from pydoris.doris_client import DorisClient  # noqa: PLC0415

    return DorisClient(
        fe_host=settings.analytics_host,
        fe_http_port=str(settings.analytics_port),
        username=settings.analytics_user,
        password=settings.analytics_password,
    )


def _write_rows(table: str, rows: list[dict[str, Any]]) -> None:
    """Stream-load ``rows`` into ``table`` (BLOCKING — call via asyncio.to_thread).

    Uses JSON-lines format with an auto UUID label per load. pydoris prints on
    success; we swallow that stdout. Raises on transport error — the async
    wrapper catches it.
    """
    client = _client()
    # Fresh options per load: JSON-by-line + a unique label for idempotency.
    client.options.set_json_format().set_auto_uuid_label()
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    with contextlib.redirect_stdout(io.StringIO()):
        ok = client.write(settings.analytics_db, table, payload)
    if not ok:
        raise RuntimeError(f"Doris stream load reported failure for {table}")


async def _async_write(table: str, rows: list[dict[str, Any]]) -> None:
    """Fire-and-forget async wrapper — never raises."""
    if not rows or not analytics_enabled():
        return
    try:
        await asyncio.to_thread(_write_rows, table, rows)
    except Exception as exc:  # noqa: BLE001 — analytics must never break chat
        logger.warning("analytics_sink write to {} failed (ignored): {}", table, exc)


async def record_interaction(
    *,
    project_uid: str | None,
    tenant_id: str,
    session_id: str,
    channel: str,
    interaction_id: str,
    user_text: str,
    assistant_text: str,
    tools_called: list[str] | None = None,
    latency_ms: int | None = None,
) -> None:
    """Record both messages of a turn (user + assistant) into bot_interactions.

    content is stored RAW (not masked). Two rows share the same datetime_utc and
    interaction_id so the turn is reconstructable. Fire-and-forget.
    """
    ts = _now_utc()
    tools_str = ",".join(tools_called or [])
    base = {
        "datetime_utc": ts,
        "project_uid": project_uid or "",
        "tenant_id": tenant_id,
        "session_id": session_id,
        "channel": channel,
        "interaction_id": interaction_id,
    }
    rows = [
        {
            **base,
            "role": "user",
            "content": user_text or "",
            "tools_called": "",
            "latency_ms": None,
        },
        {
            **base,
            "role": "assistant",
            "content": assistant_text or "",
            "tools_called": tools_str,
            "latency_ms": int(latency_ms) if latency_ms is not None else None,
        },
    ]
    await _async_write(_INTERACTIONS_TABLE, rows)


async def record_llm_usage(
    *,
    project_uid: str | None,
    tenant_id: str,
    session_id: str,
    interaction_id: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Record token usage + computed cost for a turn into bot_llm_usage.

    cost_usd is computed from the pricing table. Fire-and-forget.
    """
    row = {
        "datetime_utc": _now_utc(),
        "project_uid": project_uid or "",
        "tenant_id": tenant_id,
        "session_id": session_id,
        "interaction_id": interaction_id,
        "provider": provider,
        "model": model,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "cost_usd": compute_cost_usd(model, input_tokens or 0, output_tokens or 0),
    }
    await _async_write(_LLM_USAGE_TABLE, [row])
