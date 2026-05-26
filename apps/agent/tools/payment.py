"""Cobranza payment tools — STUBS for Fase 0.

TODO Fase 1: implement payment-plan simulation, discount eligibility, and
payment-promise registration. Discounts/quitas are regulatory-sensitive —
what can be promised must be validated against tenant rules + guardrails.
"""


async def simulate_payment_plan(account_id: str, installments: int | None = None) -> dict:
    """TODO: simulate an installment payment plan for a debt."""
    # TODO Fase 1: compute real plan (installments, amounts, due dates).
    return {
        "found": False,
        "account_id": account_id,
        "installments": installments,
        "todo": "simulate_payment_plan not implemented — Fase 1",
    }


async def check_discount_eligibility(account_id: str) -> dict:
    """TODO: check eligibility for a discount/quita on early payment.

    Regulatory: any discount offered must respect tenant policy + guardrails.
    """
    # TODO Fase 1: evaluate eligibility against tenant discount rules.
    return {
        "eligible": False,
        "account_id": account_id,
        "todo": "check_discount_eligibility not implemented — Fase 1",
    }


async def register_payment_promise(account_id: str, amount: float, promise_date: str) -> dict:
    """TODO: register a payment promise (PTP) with amount and committed date."""
    # TODO Fase 1: persist PTP to the collections core.
    return {
        "registered": False,
        "account_id": account_id,
        "amount": amount,
        "promise_date": promise_date,
        "todo": "register_payment_promise not implemented — Fase 1",
    }
