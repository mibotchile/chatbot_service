"""Credit-state classifier and moratoria calculation for cobranza scenario routing.

Pure functions — no DB access, no side effects, no imports beyond stdlib.

TERMINOLOGY (CRITICAL — do not conflate):
  credit_state = INPUT axis: al_dia / por_vencer / vencido
    Derived from the verified Doris debt profile. Used in scenario routing,
    session_state, and responses.json template key selection.

  n1 / n2 / n3 = OUTPUT axis: gestión typification in GENERAL.mibotair_results.
    RESERVED for gestion_registry.py TIPIFICATION_MAP only. NEVER used here.
"""

from __future__ import annotations

import math
from datetime import date

CREDIT_STATE_LABELS: dict[str, str] = {
    "al_dia": "Al día",
    "por_vencer": "Próximo a vencer",
    "vencido": "Vencido",
}


def classify_credit_state(profile: dict, window_days: int = 5) -> str:
    """Derive the credit state from a verified Doris debt profile.

    Args:
        profile: verified borrower profile dict. Expected keys:
            - cuotas_vencidas (int, default 0)
            - days_overdue (int, default 0)
            - next_due_date (str ISO, optional)
        window_days: days-ahead threshold for "por_vencer" (default 5).

    Returns:
        "vencido"    if cuotas_vencidas >= 1 OR days_overdue > 0
        "por_vencer" if cuotas_vencidas == 0 AND 0 < days_until_due <= window_days
        "al_dia"     otherwise (including missing/None next_due_date)
    """
    cuotas_vencidas = int(profile.get("cuotas_vencidas") or 0)
    days_overdue = int(profile.get("days_overdue") or 0)

    if cuotas_vencidas >= 1 or days_overdue > 0:
        return "vencido"

    next_due_raw = profile.get("next_due_date")
    if next_due_raw:
        try:
            next_due = date.fromisoformat(str(next_due_raw))
            days_until_due = (next_due - date.today()).days
            if 0 < days_until_due <= window_days:
                return "por_vencer"
        except ValueError:
            pass  # unparseable date → al_dia (safe default)

    return "al_dia"


# ── Moratoria calculation (INF-12) ──────────────────────────────────────────


def calcular_penalidad(saldo_capital_inicial: float, dias_overdue: int) -> float:
    """Compute the weekly overdue penalty (penalidad por mora) — inductive rule.

    Formula (confirmed Ricky 2026-06-10):
        semana = max(1, ceil(dias_overdue / 7))
        raw    = saldo_capital_inicial * 0.00008 * semana   # 0.008% per week
        result = ceil(raw * 10) / 10                        # ceil to nearest 0.1 sol

    No cap on semana — sem1=0.008%, sem2=0.016%, sem3=0.024%, … indefinitely.

    Args:
        saldo_capital_inicial: outstanding principal balance (saldo pendiente).
        dias_overdue: days the credit is overdue (>= 0).

    Returns:
        Penalty amount in soles, ceiled to nearest tenth (e.g. 0.56 → 0.60).
    """
    semana = max(1, math.ceil(dias_overdue / 7))
    raw = saldo_capital_inicial * 0.00008 * semana
    return math.ceil(raw * 10) / 10


def calcular_interes_compensatorio(
    amortizacion_cuota: float,
    tasa_interes_mensual: float,
    dias_transcurridos: int,
) -> float:
    """Compute the compensatory interest for the overdue period.

    Formula (confirmed Naomi 2026-06-10):
        amortizacion_cuota * (tasa_interes_mensual / 30) * dias_transcurridos

    Args:
        amortizacion_cuota: expected principal amortization for the installment.
            Source: batch_pagos_v2_bronze.amortizacion_esperada_original.
        tasa_interes_mensual: monthly interest rate as a decimal (e.g. 0.03 = 3%).
            Source: batch_asignacion_review_bronze.tasa_de_interes after parsing.
        dias_transcurridos: days elapsed since the due date (>= 0).

    Returns:
        Compensatory interest amount (float, no rounding applied).
    """
    return amortizacion_cuota * (tasa_interes_mensual / 30) * dias_transcurridos
