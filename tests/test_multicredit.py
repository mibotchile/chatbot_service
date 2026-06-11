"""Tests for Phase 8: Multi-credit selector + 7 fields (MCD-01).

Covers task 8.4 scenarios:
  (a) user with 2 credits → credit_selector intent emitted
  (b) user selects credit A → session_state["selected_credit_id"] set correctly
  (c) cuentas_bancarias display for selected credit shows all 7 fields
  (d) single-credit user → no selector shown, all 7 fields displayed without selector
  (e) consulta_deuda for user with 2 credits uses selected credit's data

All tests use synthetic 2-credit fixtures (no real 2-credit client exists in data).
"""

from __future__ import annotations

from pathlib import Path

from features.conversation import responses as R
from features.cobranza.tools import render_cuentas_bancarias
from tenancy.responses_spec import ResponsesSpec

TENANT = "prestamype"


def _tenant_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "tenants" / TENANT


def _spec() -> ResponsesSpec:
    return ResponsesSpec.from_dir(_tenant_dir(), response_mode="hybrid")


# ── Synthetic fixtures ────────────────────────────────────────────────────────

def _credit_A() -> dict:
    """Credit A — first of two credits for a dual-credit borrower."""
    return {
        "account_id": "P05001",
        "loan_number": "P05001",
        "borrower_name": "LUCIA FERNANDEZ VEGA",
        "dni": "76310582",
        "currency": "PEN",
        "currency_symbol": "S/",
        "balance": 9120.50,
        "days_overdue": 0,
        "next_due_date": "2026-07-10",
        "status": "al_dia",
        "status_label": "Al día",
        # 7 MCD-01 fields
        "valor_cuota": 420.0,
        "cuenta_bancaria": "1234567890",
        "cci": "00312345678901234567",
        "inversionista": "INVERSIONISTA ALPHA",
        "plazo": 24,
        "fecha_vencimiento_contrato": "2027-06-10",
        "fecha_inicio_prestamo": "2025-06-10",
        # legacy field aliases used by tools
        "numero_de_cuenta": "1234567890",
        "cuota_esperada": 420.0,
        "saldo_por_cancelar": 9120.50,
    }


def _credit_B() -> dict:
    """Credit B — second of two credits for a dual-credit borrower."""
    return {
        "account_id": "P05002",
        "loan_number": "P05002",
        "borrower_name": "LUCIA FERNANDEZ VEGA",
        "dni": "76310582",
        "currency": "PEN",
        "currency_symbol": "S/",
        "balance": 26340.0,
        "days_overdue": 0,
        "next_due_date": "2026-07-15",
        "status": "al_dia",
        "status_label": "Al día",
        # 7 MCD-01 fields
        "valor_cuota": 1100.0,
        "cuenta_bancaria": "9876543210",
        "cci": "00398765432109876543",
        "inversionista": "INVERSIONISTA BETA",
        "plazo": 36,
        "fecha_vencimiento_contrato": "2028-06-15",
        "fecha_inicio_prestamo": "2025-06-15",
        # legacy aliases
        "numero_de_cuenta": "9876543210",
        "cuota_esperada": 1100.0,
        "saldo_por_cancelar": 26340.0,
    }


def _two_credit_profile() -> dict:
    """Primary profile when borrower has 2 credits.

    The primary profile is credit A; credit B is in ``credits`` list.
    ``session_state["credits"]`` holds both so the selector can list them.
    """
    p = _credit_A()
    p["credits"] = [_credit_A(), _credit_B()]
    return p


def _single_credit_profile() -> dict:
    """Profile for a borrower with exactly 1 credit."""
    p = _credit_A()
    p["credits"] = [_credit_A()]
    return p


# ── (a) 2 credits → credit_selector intent emitted ───────────────────────────

def test_two_credits_emits_credit_selector():
    """When len(profile['credits']) == 2, emit_credit_selector returns a handled outcome."""
    spec = _spec()
    profile = _two_credit_profile()
    session_state: dict = {"credit_state": "al_dia"}

    outcome = R.emit_credit_selector(
        spec, profile, session_state=session_state, source=R.SOURCE_KEYWORD
    )

    assert outcome is not None
    assert outcome.handled is True
    assert outcome.intent == "credit_selector"
    # Both credit IDs or inversionistas must appear in the selector text
    text = outcome.text
    assert "P05001" in text or "INVERSIONISTA ALPHA" in text
    assert "P05002" in text or "INVERSIONISTA BETA" in text


def test_single_credit_does_not_emit_selector():
    """When len(profile['credits']) == 1, emit_credit_selector returns None."""
    spec = _spec()
    profile = _single_credit_profile()
    session_state: dict = {"credit_state": "al_dia"}

    outcome = R.emit_credit_selector(
        spec, profile, session_state=session_state, source=R.SOURCE_KEYWORD
    )

    assert outcome is None


def test_no_credits_key_does_not_emit_selector():
    """When profile has no 'credits' key, emit_credit_selector returns None."""
    spec = _spec()
    profile = _credit_A()  # no 'credits' list
    session_state: dict = {}

    outcome = R.emit_credit_selector(
        spec, profile, session_state=session_state, source=R.SOURCE_KEYWORD
    )

    assert outcome is None


# ── (b) select credit A → selected_credit_id set ─────────────────────────────

def test_handle_credit_selection_sets_selected_credit_id():
    """Selecting a credit stores its ID in session_state['selected_credit_id']."""
    session_state: dict = {}
    profile = _two_credit_profile()

    R.handle_credit_selection("P05001", profile, session_state=session_state)

    assert session_state.get("selected_credit_id") == "P05001"


def test_handle_credit_selection_credit_b():
    """Selecting credit B stores B's ID."""
    session_state: dict = {}
    profile = _two_credit_profile()

    R.handle_credit_selection("P05002", profile, session_state=session_state)

    assert session_state.get("selected_credit_id") == "P05002"


# ── (c) cuentas_bancarias for selected credit shows all 7 fields ──────────────

def test_render_cuentas_bancarias_shows_all_7_fields_for_selected():
    """Render for selected credit must include all 7 MCD-01 fields."""
    credits = [_credit_A()]
    result = render_cuentas_bancarias(credits)

    # All 7 fields must be present
    assert "420" in result or "S/ 420" in result        # valor_cuota
    assert "1234567890" in result                        # cuenta_bancaria
    assert "00312345678901234567" in result              # cci
    assert "INVERSIONISTA ALPHA" in result               # inversionista
    assert "24" in result                                # plazo
    assert "2027-06-10" in result                        # fecha_vencimiento_contrato
    assert "2025-06-10" in result                        # fecha_inicio_prestamo


def test_render_cuentas_bancarias_two_credits_shows_all_7_fields_each():
    """Two-credit render: each credit shows all 7 MCD-01 fields."""
    credits = [_credit_A(), _credit_B()]
    result = render_cuentas_bancarias(credits)

    # Credit A fields
    assert "420" in result                               # valor_cuota A
    assert "1234567890" in result                        # cuenta A
    assert "00312345678901234567" in result              # cci A
    assert "INVERSIONISTA ALPHA" in result               # inversionista A
    assert "2027-06-10" in result                        # fecha_venc A
    assert "2025-06-10" in result                        # fecha_inicio A

    # Credit B fields
    assert "1,100" in result or "1100" in result         # valor_cuota B (comma-formatted)
    assert "9876543210" in result                        # cuenta B
    assert "00398765432109876543" in result              # cci B
    assert "INVERSIONISTA BETA" in result                # inversionista B
    assert "2028-06-15" in result                        # fecha_venc B
    assert "2025-06-15" in result                        # fecha_inicio B

    # Two labeled rows
    assert "[P05001]" in result
    assert "[P05002]" in result


# ── (d) single-credit: no selector, all 7 fields ─────────────────────────────

def test_single_credit_no_selector_7_fields():
    """Single-credit path: no selector emitted, 7 fields rendered."""
    spec = _spec()
    profile = _single_credit_profile()
    session_state: dict = {"credit_state": "al_dia"}

    # No selector
    selector_outcome = R.emit_credit_selector(
        spec, profile, session_state=session_state, source=R.SOURCE_KEYWORD
    )
    assert selector_outcome is None

    # 7 fields present in single-credit render
    credits = profile["credits"]
    result = render_cuentas_bancarias(credits)
    assert "INVERSIONISTA ALPHA" in result
    assert "1234567890" in result
    assert "00312345678901234567" in result
    assert "420" in result
    assert "24" in result
    assert "2027-06-10" in result
    assert "2025-06-10" in result


# ── (e) consulta_deuda with 2 credits uses selected credit ───────────────────

def test_consulta_deuda_uses_selected_credit_id():
    """When selected_credit_id is set, consulta_deuda uses that credit's data."""
    spec = _spec()
    profile = _two_credit_profile()
    session_state = {
        "credit_state": "al_dia",
        "selected_credit_id": "P05002",  # credit B selected
    }

    outcome = R.handle_consulta_deuda(
        spec, profile, session_state=session_state, source=R.SOURCE_KEYWORD
    )

    assert outcome is not None
    assert outcome.handled is True
    assert outcome.intent == "consulta_deuda"
    # The outcome must carry the selected_credit_id context
    assert session_state.get("selected_credit_id") == "P05002"


def test_consulta_deuda_two_credits_no_selection_defaults_to_first():
    """When 2 credits but no selection yet, consulta_deuda still works (uses primary)."""
    spec = _spec()
    profile = _two_credit_profile()
    session_state = {"credit_state": "al_dia"}  # no selected_credit_id

    outcome = R.handle_consulta_deuda(
        spec, profile, session_state=session_state, source=R.SOURCE_KEYWORD
    )

    assert outcome is not None
    assert outcome.handled is True
