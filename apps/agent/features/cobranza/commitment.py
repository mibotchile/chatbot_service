"""Bot-owned payment commitment — CMP-01/CMP-02.

Provides:
  parse_commitment_date(text) -> date | None
      Accepts YYYY-MM-DD or DD/MM/YYYY. Returns None for unparseable or past dates.

  within_commitment_window(d) -> bool
      True when d is today..today+2 (inclusive). >+2 → False.

  register_commitment(pool, schema, conversation_id, *, date_str, amount, profile)
      -> CommitmentResult
      Validates window, writes gestiones snapshot + gestion_event.
      On out-of-window, unparseable, or DB failure → escalate=True, registered=False.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from loguru import logger


@dataclass
class CommitmentResult:
    """Result of a register_commitment call."""

    registered: bool
    escalate: bool
    commitment_date: date | None = None
    reason: str = ""


def parse_commitment_date(text: str) -> date | None:
    """Parse a date string in YYYY-MM-DD or DD/MM/YYYY format.

    Returns None when:
    - The text is empty or does not match either format.
    - The parsed date is strictly in the past (before today).
    """
    if not text or not text.strip():
        return None
    raw = text.strip()
    d: date | None = None
    # Try ISO format first (YYYY-MM-DD)
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        pass
    # Try DD/MM/YYYY
    if d is None:
        try:
            d = datetime.strptime(raw, "%d/%m/%Y").date()
        except ValueError:
            return None
    # Reject past dates
    if d < date.today():
        return None
    return d


def within_commitment_window(d: date, *, window_days: int = 2) -> bool:
    """Return True when d is within today..today+window_days (the bot-owned window).

    Dates beyond window_days in the future are out of window → escalate to asesor.

    Args:
        d: the proposed commitment date.
        window_days: max days ahead (inclusive). Read from
            ``tenant.config.json → cobranza.commitment_window_days``.
            Default 2 preserves the original Prestamype behaviour.
    """
    today = date.today()
    return today <= d <= today + timedelta(days=window_days)


async def register_commitment(
    pool: Any,
    schema: str,
    conversation_id: str,
    *,
    date_str: str,
    amount: float,
    profile: dict,
    window_days: int = 2,
) -> CommitmentResult:
    """Validate the commitment window and persist to gestiones if in range.

    Validation:
    - parse_commitment_date must succeed (not None / not past).
    - within_commitment_window must be True.

    On any validation failure → CommitmentResult(registered=False, escalate=True).

    On DB failure → CommitmentResult(registered=False, escalate=True).
    Exceptions are NEVER re-raised into the call path.

    Args:
        window_days: bot-owned commitment window in days. Read from
            ``tenant.config.json → cobranza.commitment_window_days``.
            Default 2 preserves the original Prestamype behaviour.

    Writes:
    - upsert_gestion: commitment_date, commitment_amount, outcome=payment_commitment_registered
    - append_gestion_event: event_type=commitment, intent=compromiso_pago
    """
    # -- Validate date --
    d = parse_commitment_date(date_str)
    if d is None:
        return CommitmentResult(
            registered=False, escalate=True, reason="unparseable_or_past"
        )

    if not within_commitment_window(d, window_days=window_days):
        return CommitmentResult(
            registered=False,
            escalate=True,
            commitment_date=d,
            reason="beyond_window",
        )

    # -- Persist --
    try:
        from shared.persistence.persistence import (  # noqa: PLC0415
            append_gestion_event,
            upsert_gestion,
        )
        from features.analytics.gestion_catalog import (  # noqa: PLC0415
            EventType,
            Outcome,
        )

        await upsert_gestion(
            pool,
            schema,
            conversation_id,
            fields={
                "commitment_date": d,
                "commitment_amount": amount,
                "outcome": Outcome.payment_commitment_registered.value,
            },
        )
        await append_gestion_event(
            pool,
            schema,
            conversation_id,
            event_type=EventType.commitment.value,
            intent="compromiso_pago",
            payload={
                "commitment_date": d.isoformat(),
                "commitment_amount": amount,
                "document": profile.get("document") or profile.get("dni"),
            },
        )
        logger.info(
            "commitment registered conv={} date={} amount={}",
            conversation_id,
            d.isoformat(),
            amount,
        )
        return CommitmentResult(registered=True, escalate=False, commitment_date=d)

    except Exception:
        logger.opt(exception=True).warning(
            "register_commitment DB write failed (conv={}) → escalating",
            conversation_id,
        )
        return CommitmentResult(
            registered=False, escalate=True, commitment_date=d, reason="db_error"
        )
