"""Tests for PR-2: cobranza business params moved to tenant config.

Covers:
  (A) calcular_penalidad uses config rate — different rate changes result
  (B) calcular_penalidad unknown rounding raises ValueError
  (C) within_commitment_window honours config window_days (e.g. 5)
  (D) horario functions require tenant_id (TypeError without it)
  (E) vencido_only set derived from responses.json flags
  (F) Missing-key fallback: penalidad_rate_per_week absent from profile logs warning
      and falls back to default value
  (G) tenant configs carry the new keys
"""

from __future__ import annotations

import pytest
from datetime import date, timedelta


# ---------------------------------------------------------------------------
# (A) calcular_penalidad uses config rate
# ---------------------------------------------------------------------------


def test_penalidad_custom_rate_changes_result():
    """Different week-1 / week-2+ rates produce different penalties."""
    from features.cobranza.scenario import calcular_penalidad

    saldo = 1000.0

    # Week 1: rate_week1 applies.
    default_w1 = calcular_penalidad(saldo, 7)  # rate_week1=0.00008 → 0.1
    custom_w1 = calcular_penalidad(saldo, 7, rate_week1=0.00016)  # → 0.2
    assert default_w1 == pytest.approx(0.1)
    assert custom_w1 == pytest.approx(0.2)

    # Week 3: flat rate_week2_plus applies (NOT progressive — Naomi 2026-06-11).
    default_w3 = calcular_penalidad(saldo, 16)  # 0.00016 flat → 0.2
    custom_w3 = calcular_penalidad(saldo, 16, rate_week2_plus=0.00032)  # → 0.4
    assert default_w3 == pytest.approx(0.2)
    assert custom_w3 == pytest.approx(0.4)


def test_penalidad_config_rate_from_prestamype_config():
    """Rate read from prestamype tenant config matches the hardcoded engine default."""
    import json
    from pathlib import Path

    cfg_path = (
        Path(__file__).resolve().parent.parent
        / "tenants" / "prestamype" / "tenant.config.json"
    )
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    rate_w1 = cfg["cobranza"]["penalidad_rate_week1"]
    rate_w2 = cfg["cobranza"]["penalidad_rate_week2_plus"]
    rounding = cfg["cobranza"]["penalidad_rounding"]

    from features.cobranza.scenario import calcular_penalidad

    saldo = 5000.0
    dias = 14  # semana = 2

    result_config = calcular_penalidad(
        saldo, dias, rate_week1=rate_w1, rate_week2_plus=rate_w2, rounding=rounding
    )
    result_default = calcular_penalidad(saldo, dias)  # engine defaults

    assert result_config == result_default


# ---------------------------------------------------------------------------
# (B) Unknown rounding raises ValueError
# ---------------------------------------------------------------------------


def test_penalidad_unknown_rounding_raises():
    """Passing an unknown rounding strategy raises ValueError at call time."""
    from features.cobranza.scenario import calcular_penalidad

    with pytest.raises(ValueError, match="Unknown penalidad rounding strategy"):
        calcular_penalidad(1000.0, 7, rounding="round_half_up")


# ---------------------------------------------------------------------------
# (C) within_commitment_window honours config window_days
# ---------------------------------------------------------------------------


def test_commitment_window_honours_custom_days():
    """window_days=5 allows dates up to +5 days that would be rejected at default +2."""
    from features.cobranza.commitment import within_commitment_window

    d_plus_3 = date.today() + timedelta(days=3)
    d_plus_5 = date.today() + timedelta(days=5)
    d_plus_6 = date.today() + timedelta(days=6)

    # Default window (2): +3 is out
    assert within_commitment_window(d_plus_3) is False

    # Custom window (5): +3 and +5 are in, +6 is out
    assert within_commitment_window(d_plus_3, window_days=5) is True
    assert within_commitment_window(d_plus_5, window_days=5) is True
    assert within_commitment_window(d_plus_6, window_days=5) is False


def test_commitment_window_config_value_from_prestamype():
    """commitment_window_days in prestamype config is 2 (matches existing window tests)."""
    import json
    from pathlib import Path

    cfg_path = (
        Path(__file__).resolve().parent.parent
        / "tenants" / "prestamype" / "tenant.config.json"
    )
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert cfg["cobranza"]["commitment_window_days"] == 2


# ---------------------------------------------------------------------------
# (D) horario functions require tenant_id (TypeError without it)
# ---------------------------------------------------------------------------


def test_is_feriado_requires_tenant_id():
    """is_feriado raises TypeError when tenant_id is omitted (no default)."""
    from features.cobranza.horario import is_feriado

    with pytest.raises(TypeError):
        is_feriado(date(2026, 7, 28))  # type: ignore[call-arg]


def test_is_business_hours_requires_tenant_id():
    """is_business_hours raises TypeError when tenant_id is omitted (no default)."""
    from datetime import datetime
    from features.cobranza.horario import is_business_hours

    with pytest.raises(TypeError):
        is_business_hours(datetime(2026, 6, 15, 12, 0, 0))  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# (E) vencido_only set derived from responses.json flags
# ---------------------------------------------------------------------------


def test_vencido_only_intents_from_spec_flags():
    """spec.vencido_only_intents returns intents with vencido_only=true in responses.json."""
    from tenancy.responses_spec import ResponsesSpec
    from pathlib import Path

    spec_dir = (
        Path(__file__).resolve().parent.parent / "tenants" / "prestamype"
    )
    spec = ResponsesSpec.from_dir(str(spec_dir))

    vencido_only = spec.vencido_only_intents
    assert "compromiso_pago" in vencido_only
    assert "realizar_pago_vencido" in vencido_only


def test_vencido_only_intents_empty_spec_returns_empty():
    """spec.vencido_only_intents returns empty frozenset when no intents have the flag."""
    from tenancy.responses_spec import ResponsesSpec

    spec = ResponsesSpec(intents={"some_intent": {"mode": "verbatim"}}, response_mode="hybrid")
    assert spec.vencido_only_intents == frozenset()


def test_handle_vencido_only_uses_spec_flags():
    """handle_vencido_only_intent gates based on spec flags, not hardcoded set."""
    from features.conversation.responses import handle_vencido_only_intent
    from tenancy.responses_spec import ResponsesSpec

    # Spec with a custom vencido-only intent AND a consulta_deuda intent so the
    # redirect (handle_consulta_deuda) can produce an outcome.
    spec = ResponsesSpec(
        intents={
            "custom_vencido_action": {
                "mode": "verbatim",
                "vencido_only": True,
                "template": "Acción solo para vencidos.",
                "keywords": [],
            },
            "consulta_deuda": {
                "mode": "verbatim",
                "template": "Consulta tu deuda aquí.",
                "credit_state_branches": {},
            },
        },
        response_mode="hybrid",
    )
    # al_dia → should be blocked (redirected to consulta_deuda menu)
    outcome = handle_vencido_only_intent(
        "custom_vencido_action", spec, {},
        session_state={"credit_state": "al_dia"},
        source="canned_keyword",
    )
    assert outcome is not None
    assert outcome.handled is True
    assert outcome.intent != "custom_vencido_action"

    # non-flagged intent → should pass through (None)
    outcome_pass = handle_vencido_only_intent(
        "consulta_deuda", spec, {},
        session_state={"credit_state": "al_dia"},
        source="canned_keyword",
    )
    assert outcome_pass is None

    # vencido with flagged intent → allowed (None)
    outcome_allowed = handle_vencido_only_intent(
        "custom_vencido_action", spec, {},
        session_state={"credit_state": "vencido"},
        source="canned_keyword",
    )
    assert outcome_allowed is None


# ---------------------------------------------------------------------------
# (F) Missing-key fallback: logs warning and preserves old value
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_penalidad_fallback_when_rate_missing_from_profile():
    """consultar_deuda falls back to the engine default rate when profile lacks the key.

    Verifies: (a) the result is correct using the default 0.00008 rate, and
              (b) a warning is emitted via loguru (captured via sink injection).
    """
    from features.cobranza.tools import consultar_deuda
    from loguru import logger

    warning_messages: list[str] = []

    def _capture_sink(message):
        if message.record["level"].name == "WARNING":
            warning_messages.append(message.record["message"])

    sink_id = logger.add(_capture_sink, level="WARNING", format="{message}")
    try:
        profile = {
            "account_id": "TEST-001",
            "credit_state": "vencido",
            "days_overdue": 7,
            "saldo_capital_inicial": 1000.0,
            # penalidad_rate_week1 / week2_plus intentionally absent
        }
        summary = await consultar_deuda(profile)
    finally:
        logger.remove(sink_id)

    # Warning was emitted about the missing keys
    assert any("penalidad_rate_week1" in msg for msg in warning_messages)

    # Fallback still produces a result (0.1 for 1000 * 0.00008, week 1)
    assert summary.get("penalidad") == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# (G) Tenant configs carry the new keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", ["prestamype", "prestaunion", "_template"])
def test_tenant_config_has_cobranza_keys(slug):
    """All three tenant configs carry the new cobranza keys."""
    import json
    from pathlib import Path

    cfg_path = (
        Path(__file__).resolve().parent.parent
        / "tenants" / slug / "tenant.config.json"
    )
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cobranza = cfg.get("cobranza", {})

    assert "penalidad_rate_week1" in cobranza, f"{slug}: missing penalidad_rate_week1"
    assert "penalidad_rate_week2_plus" in cobranza, f"{slug}: missing penalidad_rate_week2_plus"
    assert "penalidad_rounding" in cobranza, f"{slug}: missing penalidad_rounding"
    assert "commitment_window_days" in cobranza, f"{slug}: missing commitment_window_days"
    assert "credit_state_labels" in cobranza, f"{slug}: missing credit_state_labels"
    assert "timezone" in cobranza.get("horario", {}), f"{slug}: missing horario.timezone"


def test_saldo_capital_inicial_mapped_to_capital_column():
    """Penalty base = ORIGINAL disbursed capital (Naomi 2026-06-11): the
    prestamype column_map maps saldo_capital_inicial to the asignación
    ``capital`` column, and the built SQL selects it."""
    import json
    from pathlib import Path

    from features.cobranza import doris_debt_source as dds

    cfg_path = (
        Path(__file__).resolve().parent.parent
        / "tenants" / "prestamype" / "tenant.config.json"
    )
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cm = cfg["doris_schema"]["column_map"]
    assert cm.get("saldo_capital_inicial", {}).get("column") == "capital"
    assert cm["saldo_capital_inicial"].get("source") == "debt"

    sql, _db = dds._build_sql(cfg["doris_schema"])
    assert "capital AS saldo_capital_inicial" in sql
