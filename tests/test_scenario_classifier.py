"""Unit tests for the credit-state classifier (Phase 1, task 1.2).

STRICT TDD — tests written before the implementation.
Terminology: credit_state is the INPUT axis (al_dia / por_vencer / vencido).
NEVER use n1/n2/n3 here — those are gestión OUTPUT typification.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest


def _today_plus(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


@pytest.mark.parametrize(
    "profile, expected",
    [
        # vencido: cuotas_vencidas >= 1
        (
            {"cuotas_vencidas": 2, "days_overdue": 8, "next_due_date": _today_plus(-8)},
            "vencido",
        ),
        # vencido: days_overdue alone triggers it (cuotas_vencidas == 0) — OR branch
        (
            {"cuotas_vencidas": 0, "days_overdue": 1, "next_due_date": _today_plus(-1)},
            "vencido",
        ),
        # por_vencer: boundary — exactly 5 days until due
        (
            {"cuotas_vencidas": 0, "days_overdue": 0, "next_due_date": _today_plus(5)},
            "por_vencer",
        ),
        # por_vencer: inside window (3 days)
        (
            {"cuotas_vencidas": 0, "days_overdue": 0, "next_due_date": _today_plus(3)},
            "por_vencer",
        ),
        # al_dia: next due > window (10 days)
        (
            {"cuotas_vencidas": 0, "days_overdue": 0, "next_due_date": _today_plus(10)},
            "al_dia",
        ),
        # al_dia: missing next_due_date key → safe default
        (
            {"cuotas_vencidas": 0, "days_overdue": 0},
            "al_dia",
        ),
        # al_dia: next_due_date explicitly None → safe default
        (
            {"cuotas_vencidas": 0, "days_overdue": 0, "next_due_date": None},
            "al_dia",
        ),
    ],
    ids=[
        "vencido_cuotas_vencidas_2",
        "vencido_days_overdue_alone",
        "por_vencer_boundary_5d",
        "por_vencer_inside_3d",
        "al_dia_current_10d",
        "al_dia_missing_next_due_date",
        "al_dia_next_due_date_none",
    ],
)
def test_classify_credit_state(profile: dict, expected: str) -> None:
    from features.cobranza.scenario import classify_credit_state

    result = classify_credit_state(profile)
    assert result == expected, f"profile={profile!r} → expected {expected!r}, got {result!r}"
