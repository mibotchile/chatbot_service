"""Unit tests for moratoria calculation functions (Phase 7, task 7.2).

STRICT TDD — written before implementation (RED phase).
Tests: calcular_penalidad (inductive, ceil-al-décimo) + calcular_interes_compensatorio.

Formulas locked (Ricky 2026-06-10):
  penalidad:
    semana = max(1, ceil(dias_overdue / 7))
    raw    = saldo_capital_inicial * 0.00008 * semana
    result = ceil(raw * 10) / 10    # ceil al décimo de sol

  interes_compensatorio:
    amortizacion_cuota * (tasa_interes_mensual / 30) * dias_transcurridos

No cap at semana 2 — inductive (sem3 = 0.024%, sem4 = 0.032%, etc.)
"""

from __future__ import annotations

import math

import pytest


# ---------------------------------------------------------------------------
# calcular_penalidad
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "saldo, dias_overdue, expected",
    [
        # (a) saldo=7000, dias=3 → semana 1: 7000 * 0.00008 = 0.56 → ceil(0.56*10)/10 = 0.60
        (7000.0, 3, 0.60),
        # (b) Naomi example: saldo that gives raw=5.66 at sem1 → ceil to 5.70
        #     raw = saldo * 0.00008 → saldo = 5.66 / 0.00008 = 70750
        #     ceil(5.66 * 10) / 10 = ceil(56.6) / 10 = 57 / 10 = 5.70
        (70750.0, 3, 5.70),
        # (c) saldo=7000, dias=10 → semana 2: 7000 * 0.00016 = 1.12 → ceil(1.12*10)/10 = 1.20
        (7000.0, 10, 1.20),
        # (c-edges) week-2 boundary: dias=8 (first day) and dias=14 (last day)
        (7000.0, 8, 1.20),
        (7000.0, 14, 1.20),
        # (c2) saldo=7000, dias=16 → semana 3: flat 0.00016 from week 2 onward
        #      (Naomi 2026-06-11: NOT progressive) → 7000 * 0.00016 = 1.12 → 1.20
        (7000.0, 16, 1.20),
        # (c3) saldo=7000, dias=60 → semana 9: still flat 0.00016 → 1.20
        (7000.0, 60, 1.20),
    ],
    ids=[
        "sem1_saldo7000_dias3",
        "sem1_naomi_example_5_66_to_5_70",
        "sem2_saldo7000_dias10",
        "sem2_boundary_dias8",
        "sem2_boundary_dias14",
        "sem3_flat_rate_not_progressive",
        "sem9_flat_rate_far_overdue",
    ],
)
def test_calcular_penalidad(saldo: float, dias_overdue: int, expected: float) -> None:
    from features.cobranza.scenario import calcular_penalidad

    result = calcular_penalidad(saldo, dias_overdue)
    assert math.isclose(result, expected, rel_tol=1e-9), (
        f"calcular_penalidad({saldo}, {dias_overdue}) = {result}, expected {expected}"
    )


# ---------------------------------------------------------------------------
# calcular_interes_compensatorio
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "amortizacion, tasa, dias, expected",
    [
        # (d) amort=1000, tasa=0.03, dias=10 → 1000 * (0.03/30) * 10 = 10.0
        (1000.0, 0.03, 10, 10.0),
        # (e) dias_transcurridos=0 → interés = 0
        (1000.0, 0.03, 0, 0.0),
    ],
    ids=[
        "interes_amort1000_tasa003_dias10",
        "interes_dias_cero",
    ],
)
def test_calcular_interes_compensatorio(
    amortizacion: float, tasa: float, dias: int, expected: float
) -> None:
    from features.cobranza.scenario import calcular_interes_compensatorio

    result = calcular_interes_compensatorio(amortizacion, tasa, dias)
    assert math.isclose(result, expected, rel_tol=1e-9), (
        f"calcular_interes_compensatorio({amortizacion}, {tasa}, {dias}) = {result}, "
        f"expected {expected}"
    )
