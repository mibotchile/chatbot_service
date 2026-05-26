"""Cobranza debt tools — STUBS for Fase 0.

TODO Fase 1: wire these to the real debt source (read-only API over the
collections core / Postgres bd-intranet). See chatbot-cobranza plan,
"Decisiones abiertas #1". Identity verification MUST gate get_debt_detail
before any debt amount is revealed (Fase 2).
"""


async def get_debt_detail(account_id: str) -> dict:
    """TODO: return debt breakdown (total, capital, interest, late fees).

    Must only be callable AFTER identity verification (Fase 2 gate).
    """
    # TODO Fase 1: replace mock with real read-only debt lookup.
    return {
        "found": False,
        "account_id": account_id,
        "todo": "get_debt_detail not implemented — wire to debt source in Fase 1",
    }


async def get_account_status(account_id: str) -> dict:
    """TODO: return account status (current/overdue, days_overdue, tramo)."""
    # TODO Fase 1: replace mock with real account status lookup.
    return {
        "found": False,
        "account_id": account_id,
        "todo": "get_account_status not implemented — wire to debt source in Fase 1",
    }


async def get_payment_channels(account_id: str = "") -> dict:
    """TODO: return available payment channels (links, transfer, agents)."""
    # TODO Fase 1: load channels from tenant config / payment provider.
    return {
        "channels": [],
        "todo": "get_payment_channels not implemented — load from tenant config in Fase 1",
    }
