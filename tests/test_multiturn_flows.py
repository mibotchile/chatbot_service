"""Multi-turn integration tests — thread the REAL session_state across turns.

These exist because the unit tests pre-armed pending flags (e.g.
``arm_id_contrato_flow(...)`` / ``session_state["compromiso_pago_pending_date"]=True``)
and therefore never exercised the ARMING step. That gap let two production bugs
ship: the compromiso date-reply gate and the id_contrato two-step flow both fell
through to the LLM because nothing armed them on the direct path.

The tests below drive ``SoreliaAgent._try_canned`` turn-by-turn with ONE shared
session_state dict and assert that each turn arms the next — the way a real
conversation does. They would have FAILED before the fixes.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from features.conversation import responses as responses_engine
from features.conversation.agent import SoreliaAgent
from tenancy.responses_spec import ResponsesSpec


def _tenant_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "tenants" / "prestamype"


def _spec() -> ResponsesSpec:
    return ResponsesSpec.from_dir(_tenant_dir(), response_mode="hybrid")


def _build_agent(*, verified: bool, profile: dict | None = None) -> SoreliaAgent:
    registry = MagicMock()
    registry._identity_verified = verified
    registry._debt_context = profile or {}
    registry._tenant_id = "prestamype"

    provider = MagicMock()
    provider.complete = AsyncMock(return_value=MagicMock(text="ninguna", tool_calls=[]))

    tenant = MagicMock()
    tenant.responses = _spec()
    tenant.config = {"cobranza": {"proxima_vencer_window_days": 5}}

    return SoreliaAgent(provider=provider, tool_registry=registry, tenant=tenant)


_VENCIDO_PROFILE = {
    "account_id": "P03871",
    "dni": "08160369",
    "days_overdue": 57,
    "cuotas_vencidas": 2,
    "next_due_date": "2026-04-15",
    "monto_vencido": 4430.54,
    "saldo_por_cancelar": 62063.67,
    "saldo_capital_inicial": 62063.67,
    "balance": 62063.67,
    "inversionista": "TEST INVERSIONISTA",
}


# ── Compromiso: arm the date gate across turns (was the bug) ─────────────────

@pytest.mark.asyncio
async def test_compromiso_directo_arma_el_gate_de_fecha():
    """Turn 1 (verified vencido user asks for compromiso) MUST arm the date gate
    so turn 2's date is intercepted — not pre-set, must happen via the canned path.
    """
    agent = _build_agent(verified=True, profile=dict(_VENCIDO_PROFILE))
    ss: dict = {}

    with patch.object(responses_engine, "get_moratoria_fields", create=True):
        await agent._try_canned("Quiero hacer un compromiso de pago", {}, ss)

    assert ss.get("compromiso_pago_pending_date") is True, (
        "compromiso date-reply gate was NOT armed on the direct path — the next "
        "turn's date would fall through to the LLM (this was the live bug)."
    )


@pytest.mark.asyncio
async def test_compromiso_segundo_turno_intercepta_la_fecha():
    """Turn 2 (the date) MUST reach handle_compromiso_date_reply, not the LLM."""
    agent = _build_agent(verified=True, profile=dict(_VENCIDO_PROFILE))
    ss: dict = {}
    await agent._try_canned("Quiero hacer un compromiso de pago", {}, ss)
    assert ss.get("compromiso_pago_pending_date") is True  # armed by turn 1

    fake_outcome = responses_engine.RouterOutcome(
        handled=True, text="Registramos tu compromiso.", intent="compromiso_pago_confirmado",
        source=responses_engine.SOURCE_KEYWORD,
    )
    with patch.object(
        responses_engine, "handle_compromiso_date_reply",
        new=AsyncMock(return_value=fake_outcome),
    ) as mocked:
        await agent._try_canned("2026-06-12", {}, ss)

    assert mocked.called, (
        "the date reply did NOT reach handle_compromiso_date_reply — the gate "
        "fell through to the LLM."
    )
    assert mocked.call_args.args[0] == "2026-06-12"


# ── ID-contrato dual-factor: two-step arming across turns (was the bug) ──────

async def _enter_id_contrato_flow(agent: SoreliaAgent, ss: dict) -> None:
    """Turn 1: user opts into id-contrato identification. The phrase is resolved
    via LLM classification (no keyword), so we force the classified intent — this
    also exercises the LLM-classification arming path (the specific gap)."""
    with patch.object(
        agent, "_classify_intent", new=AsyncMock(return_value="id_contrato_prompt"),
    ):
        await agent._try_canned("Identificarme con ID de contrato", {}, ss)


@pytest.mark.asyncio
async def test_id_contrato_turno1_arma_la_captura_del_contrato():
    """Turn 1 (user chooses id-contrato identification) MUST arm the
    expecting-contrato state so the next turn is captured as the contract id.
    """
    agent = _build_agent(verified=False)
    ss: dict = {}
    await _enter_id_contrato_flow(agent, ss)

    assert ss.get("id_contrato_expecting_contrato") is True, (
        "id_contrato flow did NOT arm Step 1 — the contract number would be "
        "treated as a DNI lookup (this was the live bug)."
    )


@pytest.mark.asyncio
async def test_id_contrato_turno2_captura_contrato_y_arma_dni():
    """Turn 2 (the contract number) MUST be captured + arm the DNI step."""
    agent = _build_agent(verified=False)
    ss: dict = {}
    await _enter_id_contrato_flow(agent, ss)
    assert ss.get("id_contrato_expecting_contrato") is True

    await agent._try_canned("P03886", {}, ss)

    assert ss.get("id_contrato_pending_contrato_id") == "P03886", (
        "Step 1 did not capture the contract id / arm the DNI step."
    )
    assert "id_contrato_expecting_contrato" not in ss
    assert responses_engine.is_id_contrato_flow_active(ss)


@pytest.mark.asyncio
async def test_id_contrato_turno3_resuelve_con_dni():
    """Turn 3 (the DNI) MUST call resolve_contrato with (contrato, dni)."""
    agent = _build_agent(verified=False)
    ss: dict = {}
    await _enter_id_contrato_flow(agent, ss)
    await agent._try_canned("P03886", {}, ss)
    assert ss.get("id_contrato_pending_contrato_id") == "P03886"

    resolved = {"account_id": "P03886", "borrower_name": "Carlos"}
    with patch(
        "features.cobranza.doris_debt_source.resolve_contrato",
        return_value=resolved,
    ) as mocked:
        await agent._try_canned("46101953", {}, ss)

    assert mocked.called, "the DNI turn did not reach resolve_contrato"
    assert mocked.call_args.args[0] == "P03886"
    assert mocked.call_args.args[1] == "46101953"
    assert ss.get("id_contrato_verified_profile") == resolved
