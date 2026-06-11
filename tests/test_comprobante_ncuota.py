"""Tests for CPR-01: comprobante pre-question gate + n_cuota payload + abono chain.

STRICT TDD — tests define behaviour; implementation must satisfy all 3 cases.

Case (a): pre-question 'Sí' → flow proceeds; n_cuota is passed through in payload.
Case (b): pre-question 'No' → asesor escalation; no comprobante collected.
Case (c): tipo == 'abono' after comprobante completion →
          session_state['pending_intent'] == 'compromiso_pago'.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from features.conversation import responses as responses_engine
from tenancy.responses_spec import ResponsesSpec


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_spec(tenant: str = "prestamype") -> ResponsesSpec:
    root = Path(__file__).resolve().parent.parent / "tenants" / tenant / "responses.json"
    data = json.loads(root.read_text(encoding="utf-8"))
    return ResponsesSpec(data, response_mode="hybrid")


def _base_profile() -> dict:
    return {
        "account_id": "P02137",
        "dni": "44218903",
        "borrower_name": "Luis Demo",
        "balance": 23800.0,
        "saldo_por_cancelar": 23800.0,
        "cuota_esperada": 462.14,
        "next_installment_amount": 462.14,
        "next_due_date": "2026-07-15",
        "status": "al_dia",
        "days_overdue": 0,
        "cuotas_vencidas": 0,
    }


def _trigger_prequestion(spec: ResponsesSpec, profile: dict, session_state: dict) -> responses_engine.RouterOutcome:
    """Simulate the agent-level pre-question gate for comprobante_reportar.

    In production the agent calls apply_comprobante_prequestion_gate after the
    router resolves comprobante_reportar (which renders empty → handled=False).
    We replicate that logic here: build a minimal handled=False outcome, then
    run the gate to get the pre-question.
    """
    base_outcome = responses_engine.RouterOutcome(
        handled=False,
        intent="comprobante_reportar",
        source=responses_engine.SOURCE_INTENT,
    )
    return responses_engine.apply_comprobante_prequestion_gate(
        base_outcome, spec, profile, session_state=session_state,
    )


# ── Case (a): pre-question 'Sí' → comprobante flow continues ─────────────────


def test_prequestion_si_sets_answered_flag():
    """When the user answers 'Sí' to the pre-question, the gate marks the session
    and clears pending_intent so the comprobante flow can proceed.
    """
    spec = _load_spec()
    profile = _base_profile()
    session_state: dict = {}

    # First: classify comprobante_reportar → gate fires, pre-question emitted
    outcome = _trigger_prequestion(spec, profile, session_state)
    assert outcome.handled
    assert outcome.intent == "comprobante_proxima_cuota_pregunta"
    assert session_state.get("pending_intent") == "comprobante"

    # Now user answers 'Sí'
    outcome2 = responses_engine.route_layer1(
        "Sí", spec, profile,
        session_state=session_state,
        identity_verified=True,
    )
    # Key contract: flag set, pending_intent no longer 'comprobante'
    assert session_state.get("comprobante_prequestion_answered") is True
    assert session_state.get("pending_intent") != "comprobante"


@pytest.mark.asyncio
async def test_prequestion_si_n_cuota_in_payload(tmp_path, monkeypatch):
    """n_cuota passed to validar_comprobante appears in the returned payload."""
    import features.comprobantes.validator as validator

    monkeypatch.setattr(validator, "_COMPROBANTES_PATH", tmp_path / "comprobantes.json")

    from features.comprobantes.validator import validar_comprobante

    profile = _base_profile()
    result = await validar_comprobante(
        profile,
        monto=200.0,   # < cuota (462.14) → abono
        n_cuota="3",
    )
    assert result["n_cuota"] == "3"
    assert result["tipo"] == "abono"


# ── Case (b): pre-question 'No' → asesor escalation ──────────────────────────


def test_prequestion_no_escalates_to_asesor():
    """When the user answers 'No' to the pre-question, the bot escalates to asesor
    and does NOT proceed with comprobante collection.
    """
    spec = _load_spec()
    profile = _base_profile()
    session_state: dict = {}

    # Trigger pre-question via layer-2 classification
    outcome = _trigger_prequestion(spec, profile, session_state)
    assert outcome.intent == "comprobante_proxima_cuota_pregunta"
    assert session_state.get("pending_intent") == "comprobante"

    # User answers 'No'
    outcome2 = responses_engine.route_layer1(
        "No", spec, profile,
        session_state=session_state,
        identity_verified=True,
    )
    assert outcome2.handled
    assert outcome2.intent == "derivar_asesor"
    # pending_intent cleared; comprobante_prequestion_answered NOT set
    assert session_state.get("pending_intent") != "comprobante"
    assert not session_state.get("comprobante_prequestion_answered")


# ── Case (c): tipo == 'abono' → pending_intent = 'compromiso_pago' ───────────


def test_abono_after_comprobante_chains_to_compromiso():
    """When a comprobante tool completes with tipo == 'abono', the agent sets
    session_state['pending_intent'] = 'compromiso_pago' (no intermediate menu).
    """
    from features.conversation.agent import SoreliaAgent
    from shared.llm import LLMProvider, LLMResponse

    class _FakeProvider(LLMProvider):
        @property
        def model(self):
            return "fake"

        @property
        def name(self):
            return "fake"

        async def complete(self, *, system, messages, tools, model=None, max_tokens=1024, force_tool=None):
            return LLMResponse(text="ok", tool_calls=[])

    agent = SoreliaAgent(provider=_FakeProvider())
    session_state: dict = {}

    # Minimal outcome object (simulates a completed comprobante canned intent)
    outcome = MagicMock()
    outcome.intent = "comprobante_resultado"
    outcome.source = "canned_keyword"
    outcome.run_tool = None
    outcome.rerender_with_result = False
    outcome.text = "Recibí tu comprobante."

    tool_result = {
        "cuenta_valida": True,
        "credito": "P02137",
        "tipo": "abono",
        "dedup_ok": True,
        "n_cuota": "3",
        "mensaje": "Registramos tu abono.",
    }

    spec = MagicMock()
    spec.intents = {}

    agent._content_after_tool(
        outcome, tool_result, spec,
        fallback="Recibí tu comprobante.",
        session_state=session_state,
    )
    assert session_state.get("pending_intent") == "compromiso_pago"
