"""Unit tests for horario + feriados gating (Phase 9, task 9.2).

STRICT TDD — written before implementation (RED phase).
Tests: is_feriado() + is_business_hours() reading from feriados_peru_2026.json
       and the tenant cobranza.horario config.

Business rules (confirmed Naomi 2026-06-10):
  - Horario: Lun-Vie 09:00–18:30
  - Refrigerio: 13:00–14:00 → is_business_hours False (asesores no disponibles)
  - Weekends → False
  - Feriados: canonical source = feriados_peru_2026.json
  - Timezone: America/Lima (per feriados_peru_2026.json business_hours.timezone)
"""

from __future__ import annotations

from datetime import date, datetime


# ---------------------------------------------------------------------------
# is_feriado
# ---------------------------------------------------------------------------


def test_feriado_fiestas_patrias_true() -> None:
    """(a) 2026-07-28 (Fiestas Patrias) → is_feriado True."""
    from features.cobranza.horario import is_feriado

    assert is_feriado(date(2026, 7, 28)) is True


def test_feriado_normal_weekday_false() -> None:
    """(b) A normal weekday (2026-06-11 — no holiday) → is_feriado False."""
    from features.cobranza.horario import is_feriado

    assert is_feriado(date(2026, 6, 11)) is False


# ---------------------------------------------------------------------------
# is_business_hours
# ---------------------------------------------------------------------------


def test_business_hours_monday_noon_true() -> None:
    """(c) 12:00 Monday (Lima) → is_business_hours True."""
    from features.cobranza.horario import is_business_hours

    # 2026-06-15 is a Monday; 12:00 Lima time = UTC-5 → 17:00 UTC
    # We pass a timezone-naive datetime that is interpreted as Lima local time.
    dt = datetime(2026, 6, 15, 12, 0, 0)
    assert is_business_hours(dt) is True


def test_business_hours_monday_refrigerio_false() -> None:
    """(d) 13:30 Monday → is_business_hours False (refrigerio 13:00–14:00)."""
    from features.cobranza.horario import is_business_hours

    dt = datetime(2026, 6, 15, 13, 30, 0)
    assert is_business_hours(dt) is False


def test_business_hours_monday_after_hours_false() -> None:
    """(e) 18:31 Monday → is_business_hours False (after 18:30 close)."""
    from features.cobranza.horario import is_business_hours

    dt = datetime(2026, 6, 15, 18, 31, 0)
    assert is_business_hours(dt) is False


def test_business_hours_saturday_morning_false() -> None:
    """(f) 09:00 Saturday → is_business_hours False (weekend)."""
    from features.cobranza.horario import is_business_hours

    # 2026-06-20 is a Saturday
    dt = datetime(2026, 6, 20, 9, 0, 0)
    assert is_business_hours(dt) is False
