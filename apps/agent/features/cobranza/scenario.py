"""Credit-state classifier for cobranza scenario routing.

Pure function — no DB access, no side effects, no imports beyond stdlib.

TERMINOLOGY (CRITICAL — do not conflate):
  credit_state = INPUT axis: al_dia / por_vencer / vencido
    Derived from the verified Doris debt profile. Used in scenario routing,
    session_state, and responses.json template key selection.

  n1 / n2 / n3 = OUTPUT axis: gestión typification in GENERAL.mibotair_results.
    RESERVED for gestion_registry.py TIPIFICATION_MAP only. NEVER used here.
"""

from __future__ import annotations

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
