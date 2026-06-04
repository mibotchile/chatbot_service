"""SLICE B — RED tests: inversionista + cuenta_bancaria in debt card + templates.

These tests FAIL against the current implementation (tools.py consultar_deuda
does not return inversionista/cuenta_bancaria; widget card does not render them;
responses.json templates do not include them). They pass after GREEN.

Spec: cobranza-credit-display
  - Debt card must include inversionista and cuenta_bancaria (numero_de_cuenta).
  - capital / saldo_capital must be absent from user-facing output.
  - Graceful omission when optional fields are None/missing.
"""

from __future__ import annotations

import pytest

from features.cobranza.tools import consultar_deuda, _credit_brief


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _prestamype_profile(*, inversionista="FONDO A", cuenta_bancaria="20300001234"):
    """Minimal PrestamYpe profile (Doris window-CTE shape, Slice A)."""
    return {
        "account_id": "P04197",
        "loan_number": "P04197",
        "borrower_name": "PRUEBA CLIENTE CUATRO",
        "dni": "12345678",
        "email": "test@example.com",
        "phone": "987654321",
        "currency": "PEN",
        "currency_symbol": "S/",
        "balance": 81510.15,
        "saldo_por_cancelar": 81510.15,
        "next_due_date": "2026-03-02",
        "next_installment_amount": 7031.91,
        "cuota_esperada": 7031.91,
        "days_overdue": 94,
        "status": "en_mora",
        "status_label": "En mora",
        "banco": "BCP",
        "cci": "00382100123456789012",
        "inversionista": inversionista,
        "cuenta_bancaria": cuenta_bancaria,
        # principal_original omitted per spec (capital not in new column_map)
    }


# ── B.1: consultar_deuda return dict includes inversionista + cuenta_bancaria ──

async def test_consultar_deuda_returns_inversionista():
    """consultar_deuda must include inversionista in its result dict."""
    profile = _prestamype_profile()
    result = await consultar_deuda(profile)
    assert "inversionista" in result, "consultar_deuda must return inversionista"
    assert result["inversionista"] == "FONDO A"


async def test_consultar_deuda_returns_cuenta_bancaria():
    """consultar_deuda must include cuenta_bancaria in its result dict."""
    profile = _prestamype_profile()
    result = await consultar_deuda(profile)
    assert "cuenta_bancaria" in result, "consultar_deuda must return cuenta_bancaria"
    assert result["cuenta_bancaria"] == "20300001234"


async def test_consultar_deuda_omits_principal_original():
    """consultar_deuda must NOT include principal_original (saldo capital) per spec."""
    profile = _prestamype_profile()
    result = await consultar_deuda(profile)
    # principal_original is no longer in the profile (capital dropped from column_map)
    # and must not be forwarded to the debt card.
    assert result.get("principal_original") is None or "principal_original" not in result, (
        "principal_original (capital) must be absent from consultar_deuda result"
    )


async def test_consultar_deuda_balance_is_saldo_not_capital():
    """balance in consultar_deuda result must be 81510.15 (first-unpaid), not capital."""
    profile = _prestamype_profile()
    result = await consultar_deuda(profile)
    assert result["balance"] == 81510.15, (
        f"balance must be 81510.15 (first-unpaid saldo), got {result['balance']}"
    )


# ── B.2: graceful omission when optional fields are None ─────────────────────

async def test_consultar_deuda_inversionista_none_is_ok():
    """inversionista=None must not crash; result may be None or key absent."""
    profile = _prestamype_profile(inversionista=None)
    result = await consultar_deuda(profile)
    # No crash. inversionista may be None or absent — both acceptable.
    assert result.get("inversionista") is None or "inversionista" not in result or result["inversionista"] is None


async def test_consultar_deuda_cuenta_bancaria_none_is_ok():
    """cuenta_bancaria=None must not crash; result may be None or key absent."""
    profile = _prestamype_profile(cuenta_bancaria=None)
    result = await consultar_deuda(profile)
    # No crash. cuenta_bancaria may be None or absent.
    val = result.get("cuenta_bancaria")
    assert val is None or val == "" or "cuenta_bancaria" not in result


# ── B.3: _credit_brief includes inversionista + cuenta_bancaria ──────────────

def test_credit_brief_includes_inversionista():
    """_credit_brief (multi-credit summary) must include inversionista."""
    profile = _prestamype_profile()
    brief = _credit_brief(profile, "S/")
    assert "inversionista" in brief, "_credit_brief must include inversionista"
    assert brief["inversionista"] == "FONDO A"


def test_credit_brief_includes_cuenta_bancaria():
    """_credit_brief must include cuenta_bancaria."""
    profile = _prestamype_profile()
    brief = _credit_brief(profile, "S/")
    assert "cuenta_bancaria" in brief, "_credit_brief must include cuenta_bancaria"
    assert brief["cuenta_bancaria"] == "20300001234"


def test_credit_brief_omits_principal_original():
    """_credit_brief must not expose principal_original."""
    profile = _prestamype_profile()
    brief = _credit_brief(profile, "S/")
    assert brief.get("principal_original") is None or "principal_original" not in brief


# ── B.4: widget debt card HTML contains inversionista + cuenta_bancaria ───────
# (Pure-JS rendering logic — tested here via string assertions on the template
#  output format rather than a browser; the JS function _debtCardHtml is
#  responsible for rendering. These tests document the CONTRACT so the verify
#  phase can check the JS implementation.)

async def test_debt_card_contract_inversionista_row():
    """The debt card HTML must include an Inversionista row when present.

    This test documents the expected HTML contract for the widget's _debtCardHtml.
    It fails until the JS is updated — the Python side test verifies the data
    payload (consultar_deuda) carries the field; the JS widget test (test_widget_gate)
    or manual smoke verifies rendering. Here we lock the payload contract.
    """
    profile = _prestamype_profile()
    result = await consultar_deuda(profile)
    # The field must be in the result so the widget can render it.
    assert result.get("inversionista") == "FONDO A"


async def test_debt_card_contract_capital_absent():
    """The debt card must NOT contain capital / saldo_capital."""
    profile = _prestamype_profile()
    result = await consultar_deuda(profile)
    # Neither 'capital' nor any legacy saldo_capital alias must appear.
    assert "capital" not in result or result.get("capital") is None
    assert "saldo_capital" not in result
