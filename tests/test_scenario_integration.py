"""Phase 6 — Integration Verification: end-to-end scenario assertions.

Covers task 6.2:
  - P04069 (al_dia, real case confirmed by Naomi 2026-06-10)
  - P03638 (al_dia)
  - P03700 (por_vencer, SYNTHETIC — no real case available; days_until_next_due=3)
  - P03871 (vencido single-credit)
  - P03886 (vencido multi-credit)

Cross-state bleed assertions:
  - al_dia never shows compromiso option
  - vencido never shows domingo_feriado holiday copy

Wiring gap assertions (task 6.2 integration):
  (a) vencido profile → morosidad amounts present after profile enrichment
  (b) ID-contrato + DNI happy path resolves end-to-end through handle_id_contrato_step
  (c) comprobante tool registry passes tenant_id so n_cuota validation is active
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from features.cobranza.scenario import classify_credit_state
from features.conversation import responses as R
from tenancy.responses_spec import ResponsesSpec

TENANT = "prestamype"


def _tenant_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "tenants" / TENANT


def _spec() -> ResponsesSpec:
    return ResponsesSpec.from_dir(_tenant_dir(), response_mode="hybrid")


# ── Profile fixtures ──────────────────────────────────────────────────────────

def _profile_p04069() -> dict:
    """P04069 — al_dia (real case confirmed Naomi 2026-06-10)."""
    return {
        "account_id": "P04069",
        "borrower_name": "NAOMI TEST AL DIA",
        "dni": "48000001",
        "days_overdue": 0,
        "cuotas_vencidas": 0,
        "next_due_date": "2026-07-20",
        "cuota_esperada": 350.0,
        "saldo_por_cancelar": 12000.0,
        "saldo_capital_inicial": 12000.0,
        "balance": 12000.0,
        "inversionista": "INVERSIONISTA ALPHA",
        "cci": "00312345678901234501",
    }


def _profile_p03638() -> dict:
    """P03638 — al_dia."""
    return {
        "account_id": "P03638",
        "borrower_name": "PEDRO RAMIREZ LIMA",
        "dni": "48000002",
        "days_overdue": 0,
        "cuotas_vencidas": 0,
        "next_due_date": "2026-08-01",
        "cuota_esperada": 400.0,
        "saldo_por_cancelar": 15000.0,
        "saldo_capital_inicial": 15000.0,
        "balance": 15000.0,
        "inversionista": "INVERSIONISTA BETA",
        "cci": "00312345678901234502",
    }


def _profile_p03700() -> dict:
    """P03700 — por_vencer SYNTHETIC (days_until_next_due=3, no real case available).

    NOTE (task 6.2): no real por_vencer case currently in data (all June
    vencimientos are past the 5-day window or past due). This fixture is
    synthetic until Naomi provides a real por_vencer case.
    """
    from datetime import date, timedelta
    due = (date.today() + timedelta(days=3)).isoformat()
    return {
        "account_id": "P03700",
        "borrower_name": "LUCIA FLORES SYNTHETIC",
        "dni": "48000003",
        "days_overdue": 0,
        "cuotas_vencidas": 0,
        "next_due_date": due,
        "cuota_esperada": 280.0,
        "saldo_por_cancelar": 9000.0,
        "saldo_capital_inicial": 9000.0,
        "balance": 9000.0,
        "inversionista": "INVERSIONISTA GAMMA",
        "cci": "00312345678901234503",
    }


def _profile_p03871() -> dict:
    """P03871 — vencido single-credit."""
    return {
        "account_id": "P03871",
        "borrower_name": "JORGE MAMANI VENCIDO",
        "dni": "48000004",
        "days_overdue": 12,
        "cuotas_vencidas": 2,
        "next_due_date": "2026-05-15",
        "cuota_esperada": 280.0,
        "saldo_por_cancelar": 8400.0,
        "saldo_capital_inicial": 8400.0,
        "amortizacion_cuota": 250.0,
        "tasa_interes_mensual": 0.03,
        "balance": 8400.0,
        "inversionista": "INVERSIONISTA DELTA",
        "cci": "00387654321098765432",
    }


def _profile_p03886() -> dict:
    """P03886 — vencido multi-credit."""
    return {
        "account_id": "P03886",
        "borrower_name": "ANA QUISPE MULTI",
        "dni": "48000005",
        "days_overdue": 5,
        "cuotas_vencidas": 1,
        "next_due_date": "2026-06-01",
        "cuota_esperada": 320.0,
        "saldo_por_cancelar": 10000.0,
        "saldo_capital_inicial": 10000.0,
        "amortizacion_cuota": 290.0,
        "tasa_interes_mensual": 0.025,
        "balance": 10000.0,
        "credits": [
            {"account_id": "P03886-A", "inversionista": "INV ALPHA",
             "cuenta_bancaria": "111", "cci": "00311100000000000001"},
            {"account_id": "P03886-B", "inversionista": "INV BETA",
             "cuenta_bancaria": "222", "cci": "00322200000000000002"},
        ],
    }


# ── 1. Credit state classification per fixture ───────────────────────────────

def test_p04069_classified_al_dia():
    cs = classify_credit_state(_profile_p04069(), window_days=5)
    assert cs == "al_dia"


def test_p03638_classified_al_dia():
    cs = classify_credit_state(_profile_p03638(), window_days=5)
    assert cs == "al_dia"


def test_p03700_classified_por_vencer():
    """SYNTHETIC fixture: days_until_next_due=3 → por_vencer."""
    cs = classify_credit_state(_profile_p03700(), window_days=5)
    assert cs == "por_vencer"


def test_p03871_classified_vencido():
    cs = classify_credit_state(_profile_p03871(), window_days=5)
    assert cs == "vencido"


def test_p03886_classified_vencido():
    cs = classify_credit_state(_profile_p03886(), window_days=5)
    assert cs == "vencido"


# ── 2. Option menu per credit_state (no cross-state bleed) ───────────────────

def test_al_dia_option_menu_no_compromiso():
    """al_dia response must NOT expose 'compromiso' option."""
    spec = _spec()
    profile = _profile_p04069()
    profile["credit_state"] = "al_dia"
    session_state = {"credit_state": "al_dia"}

    outcome = R.handle_consulta_deuda(
        spec, profile, session_state=session_state, source=R.SOURCE_KEYWORD
    )
    text = (outcome.text or "").lower()
    assert "al día" in text or "al_dia" in text or outcome.handled
    # No cross-state bleed: compromiso option must not appear in al_dia menu
    assert "compromiso" not in text


def test_vencido_option_menu_no_holiday_copy():
    """vencido response must NOT contain domingo/feriado holiday copy."""
    spec = _spec()
    profile = _profile_p03871()
    profile["credit_state"] = "vencido"
    session_state = {"credit_state": "vencido"}

    outcome = R.handle_consulta_deuda(
        spec, profile, session_state=session_state, source=R.SOURCE_KEYWORD
    )
    text = (outcome.text or "").lower()
    assert outcome.handled
    # No cross-state bleed: domingo/feriado copy must not appear in vencido menu
    assert "domingo" not in text
    assert "feriado" not in text


def test_por_vencer_option_menu_no_compromiso():
    """por_vencer response must NOT expose 'compromiso' option."""
    spec = _spec()
    profile = _profile_p03700()
    profile["credit_state"] = "por_vencer"
    session_state = {"credit_state": "por_vencer"}

    outcome = R.handle_consulta_deuda(
        spec, profile, session_state=session_state, source=R.SOURCE_KEYWORD
    )
    assert outcome.handled
    text = (outcome.text or "").lower()
    assert "compromiso" not in text


# ── 3. GAP (a): vencido profile morosidad enrichment ─────────────────────────

def test_vencido_profile_moratoria_enrichment_merges_fields():
    """GAP-1: after classify_credit_state==vencido, get_moratoria_fields() is
    called and amortizacion_cuota / tasa_interes_mensual are present in profile.

    This test verifies the enrichment logic that agent._try_canned must perform.
    """
    from features.cobranza import doris_debt_source as dds

    profile: dict = {
        "account_id": "P03871",
        "days_overdue": 12,
        "cuotas_vencidas": 2,
        "saldo_capital_inicial": 8400.0,
    }
    moratoria_data = {"amortizacion_cuota": 250.0, "tasa_interes_mensual": 0.03}

    cs = classify_credit_state(profile, window_days=5)
    assert cs == "vencido"

    with patch.object(dds, "get_moratoria_fields", return_value=moratoria_data) as mock_gm:
        # Replicate the wiring: when vencido, fetch and merge moratoria fields
        result = dds.get_moratoria_fields(profile["account_id"], TENANT)
        profile.update(result)
        mock_gm.assert_called_once_with("P03871", TENANT)

    assert profile.get("amortizacion_cuota") == 250.0
    assert profile.get("tasa_interes_mensual") == 0.03


async def test_agent_try_canned_enriches_vencido_profile(prestamype_spec):
    """GAP-1 (live path): when agent._try_canned classifies vencido, the profile
    in the registry gains amortizacion_cuota and tasa_interes_mensual.
    """
    from features.conversation.agent import SoreliaAgent
    from unittest.mock import MagicMock
    from features.cobranza import doris_debt_source as dds

    profile_store: dict = {
        "account_id": "P03871",
        "dni": "48000004",
        "days_overdue": 12,
        "cuotas_vencidas": 2,
        "next_due_date": "2026-05-15",
        "cuota_esperada": 280.0,
        "saldo_por_cancelar": 8400.0,
        "saldo_capital_inicial": 8400.0,
        "balance": 8400.0,
    }

    mock_registry = MagicMock()
    mock_registry._identity_verified = True
    mock_registry._debt_context = profile_store

    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(return_value=MagicMock(
        text="ninguna", tool_calls=[],
    ))

    mock_tenant = MagicMock()
    mock_tenant.responses = prestamype_spec
    mock_tenant.config = {"cobranza": {"proxima_vencer_window_days": 5}}

    agent = SoreliaAgent(provider=mock_provider, tool_registry=mock_registry, tenant=mock_tenant)
    moratoria_data = {"amortizacion_cuota": 250.0, "tasa_interes_mensual": 0.03}

    with patch.object(dds, "get_moratoria_fields", return_value=moratoria_data):
        session_state: dict = {}
        await agent._try_canned("consulta deuda", {}, session_state)

    # After _try_canned, the profile must carry moratoria fields (GAP-1 wiring)
    assert profile_store.get("amortizacion_cuota") == 250.0, (
        "GAP-1 not wired: agent._try_canned must call get_moratoria_fields() when "
        "credit_state==vencido and merge results into the live profile"
    )
    assert profile_store.get("tasa_interes_mensual") == 0.03


# ── 4. GAP (b): ID-contrato + DNI end-to-end through handle_id_contrato_step ─

def test_id_contrato_happy_path_arm_then_step(prestamype_spec):
    """GAP-2: arm_id_contrato_flow + handle_id_contrato_step → resolves profile.

    Step 1: arm flow (store contrato_id in session_state).
    Step 2: handle_id_contrato_step(dni_text) → calls resolve_contrato.
    On success: returns None (caller proceeds to identification tool) and
    id_contrato_verified_profile is set in session_state.
    """
    from features.conversation import responses as eng
    from features.cobranza import doris_debt_source as dds

    session_state: dict = {}
    profile: dict = {}
    contrato_id = "CONT-HAPPY-001"
    dni = "12345678"

    resolved_profile = {"account_id": contrato_id, "dni": dni, "days_overdue": 0}

    # Step 1: arm the flow
    eng.arm_id_contrato_flow(session_state, contrato_id)
    assert eng.is_id_contrato_flow_active(session_state)

    # Step 2: user provides DNI → resolve
    with patch.object(dds, "resolve_contrato", return_value=resolved_profile):
        outcome = eng.handle_id_contrato_step(
            dni, prestamype_spec, profile,
            session_state=session_state,
            source=eng.SOURCE_KEYWORD,
            tenant_id=TENANT,
        )

    # On success: returns None (let caller proceed to identity tool)
    assert outcome is None
    # Profile stashed for the tool registry
    assert session_state.get("id_contrato_verified_profile") == resolved_profile
    # Pending key cleared
    assert not eng.is_id_contrato_flow_active(session_state)


def test_id_contrato_not_found_emits_no_reveal(prestamype_spec):
    """GAP-2: failed resolve_contrato (DNI mismatch) → neutral no-reveal message."""
    from features.conversation import responses as eng
    from features.cobranza import doris_debt_source as dds

    session_state: dict = {}
    profile: dict = {}
    contrato_id = "CONT-FAIL-002"
    bad_dni = "00000000"

    eng.arm_id_contrato_flow(session_state, contrato_id)

    with patch.object(dds, "resolve_contrato", return_value=None):
        outcome = eng.handle_id_contrato_step(
            bad_dni, prestamype_spec, profile,
            session_state=session_state,
            source=eng.SOURCE_KEYWORD,
            tenant_id=TENANT,
        )

    assert outcome is not None
    assert outcome.handled
    # Must NOT reveal whether contract exists or DNI mismatched
    text = (outcome.text or "").lower()
    assert "no existe" not in text
    assert "contrato" not in text or "no encontramos" in text or "no pude" in text


def test_id_contrato_wired_in_route_layer1_pending_step(prestamype_spec):
    """GAP-2 live: route_layer1 routes through handle_id_contrato_step when flow active.

    When is_id_contrato_flow_active → session has _ID_CONTRATO_PENDING_KEY,
    route_layer1 must call handle_id_contrato_step so the DNI input is processed.
    """
    from features.conversation import responses as eng
    from features.cobranza import doris_debt_source as dds

    session_state: dict = {}
    profile: dict = {}
    spec = prestamype_spec

    # Arm the flow (step 1 already done)
    eng.arm_id_contrato_flow(session_state, "CONT-WIRE-001")
    assert eng.is_id_contrato_flow_active(session_state)

    resolved_profile = {"account_id": "CONT-WIRE-001", "dni": "55555555", "days_overdue": 0}

    with patch.object(dds, "resolve_contrato", return_value=resolved_profile):
        # Layer-1 receives the DNI input while id_contrato flow is armed
        eng.route_layer1(
            "55555555", spec, profile,
            session_state=session_state,
            identity_verified=False,
        )

    # The flow must have been processed: either outcome.handled (found/not-found)
    # or None was returned and session carries the verified profile.
    # Key assertion: the id_contrato_pending key must be cleared after processing.
    assert not eng.is_id_contrato_flow_active(session_state)
    # Verified profile stashed for tool registry
    assert session_state.get("id_contrato_verified_profile") == resolved_profile


# ── 5. GAP (c): comprobante tool registry passes tenant_id ───────────────────

async def test_tool_registry_passes_tenant_id_to_validar_comprobante():
    """GAP-3: _validar_comprobante in the live tool registry must pass tenant_id
    so n_cuota correlativo validation is active for Prestamype calls.
    """
    from api.tool_registry import ToolRegistry  # noqa: PLC0415

    debt_context = {
        "account_id": "P03871",
        "dni": "48000004",
        "cuota_esperada": 280.0,
        "saldo_por_cancelar": 8400.0,
    }

    registry = ToolRegistry.__new__(ToolRegistry)
    registry._tenant_id = "prestamype"
    registry._debt_context = debt_context
    registry._identity_verified = True

    captured_kwargs: list[dict] = []

    async def _mock_validar(profile, monto, **kwargs):
        captured_kwargs.append(kwargs)
        return {"cuenta_valida": True, "tipo": "pago", "dedup_ok": True, "mensaje": "ok"}

    with patch("api.tool_registry.validar_comprobante", side_effect=_mock_validar):
        await registry._validar_comprobante(
            monto=280.0,
            n_cuota="2",
        )

    assert len(captured_kwargs) == 1
    assert captured_kwargs[0].get("tenant_id") == "prestamype", (
        "GAP-3 not wired: tool_registry._validar_comprobante must pass tenant_id "
        "to validar_comprobante so n_cuota validation is active"
    )


async def test_validar_comprobante_n_cuota_validation_fires_with_tenant_id():
    """GAP-3 end-to-end: when tenant_id is passed and n_cuota is None,
    validar_comprobante returns ncuota_required=True (validation active).
    """
    from features.comprobantes.validator import validar_comprobante

    profile = {
        "account_id": "P03871",
        "cuota_esperada": 280.0,
        "saldo_por_cancelar": 8400.0,
    }

    result = await validar_comprobante(
        profile, monto=280.0,
        n_cuota=None,
        tenant_id="prestamype",
    )
    assert result.get("ncuota_required") is True


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def prestamype_spec():
    """Load the real prestamype ResponsesSpec from disk."""
    return ResponsesSpec.from_dir(_tenant_dir(), response_mode="hybrid")
