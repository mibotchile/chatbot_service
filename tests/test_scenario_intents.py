"""Tests for Phase 3: informational intents, scenario-branched consulta_deuda,
2-strike fallback, domingo/feriado redirect, and multi-credit cuentas bancarias.

Covers task 3.5 scenarios:
  (a) al_dia profile → consulta_deuda branches internally to al_dia response
      (NOT a separate consulta_deuda_al_dia binding)
  (b) vencido profile (2 overdue) → consulta_deuda branches to overdue copy
  (c) 2 consecutive unrecognized inputs → asesor escalation
  (d) vencido profile + domingo_feriado intent → vencido menu redirect, not
      holiday copy
  (e) multi-credit profile + cuentas_bancarias → each credit has a labeled row
"""

from __future__ import annotations

import json
from pathlib import Path

from features.conversation import responses as R
from features.cobranza.tools import render_cuentas_bancarias
from tenancy.responses_spec import ResponsesSpec

TENANT = "prestamype"


def _tenant_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "tenants" / TENANT


def _spec() -> ResponsesSpec:
    return ResponsesSpec.from_dir(_tenant_dir(), response_mode="hybrid")


def _al_dia_profile() -> dict:
    return {
        "account_id": "P04069",
        "loan_number": "P04069",
        "borrower_name": "MARIA ELENA TORRES QUISPE",
        "dni": "47123456",
        "currency": "PEN",
        "currency_symbol": "S/",
        "balance": 12500.0,
        "next_installment_amount": 350.0,
        "days_overdue": 0,
        "cuotas_vencidas": 0,
        "next_due_date": "2026-07-15",
        "status": "al_dia",
        "status_label": "Al día",
        "cci": "00312345678901234567",
        "banco": "BCP",
        "inversionista": "INVERSIONISTA ALPHA",
        "cuota_esperada": 350.0,
        "saldo_por_cancelar": 12500.0,
    }


def _vencido_profile() -> dict:
    return {
        "account_id": "P03871",
        "loan_number": "P03871",
        "borrower_name": "JORGE LUIS MAMANI FLORES",
        "dni": "43987654",
        "currency": "PEN",
        "currency_symbol": "S/",
        "balance": 8400.0,
        "next_installment_amount": 280.0,
        "days_overdue": 12,
        "cuotas_vencidas": 2,
        "next_due_date": "2026-05-15",
        "status": "en_mora",
        "status_label": "En mora",
        "cci": "00387654321098765432",
        "banco": "BBVA",
        "inversionista": "INVERSIONISTA BETA",
        "cuota_esperada": 280.0,
        "saldo_por_cancelar": 8400.0,
    }


def _multi_credit_profile() -> dict:
    """Profile for a user with 2 active credits (MCD-01 case)."""
    c1 = {
        "account_id": "P03886",
        "loan_number": "P03886",
        "borrower_name": "LUCIA FERNANDEZ VEGA",
        "dni": "76310582",
        "currency": "PEN",
        "currency_symbol": "S/",
        "balance": 9120.50,
        "days_overdue": 0,
        "inversionista": "INVERSIONISTA GAMMA",
        "cci": "00398765432109876543",
        "numero_de_cuenta": "3987654321",
    }
    c2 = {
        "account_id": "P03887",
        "loan_number": "P03887",
        "borrower_name": "LUCIA FERNANDEZ VEGA",
        "dni": "76310582",
        "currency": "PEN",
        "currency_symbol": "S/",
        "balance": 26340.0,
        "days_overdue": 0,
        "inversionista": "INVERSIONISTA DELTA",
        "cci": "00312345678901111111",
        "numero_de_cuenta": "1234567890",
    }
    return {**c1, "additional_credits": [c2]}


# ── (a) al_dia consulta_deuda branches internally ────────────────────────────

def test_consulta_deuda_al_dia_uses_credit_state_branches():
    """consulta_deuda with al_dia credit_state must use the al_dia branch copy.

    The binding is ONE intent key 'consulta_deuda' — NOT a separate top-level
    binding called 'consulta_deuda_al_dia'. The branch is resolved internally.
    """
    spec = _spec()
    profile = _al_dia_profile()
    session_state = {"credit_state": "al_dia"}

    # The spec must have exactly one 'consulta_deuda' binding.
    assert "consulta_deuda" in spec.intents
    assert "consulta_deuda_al_dia" not in spec.intents

    outcome = R.handle_consulta_deuda(
        spec, profile, session_state=session_state, source=R.SOURCE_KEYWORD
    )
    assert outcome is not None
    assert outcome.handled is True
    assert outcome.intent == "consulta_deuda"
    # al_dia branch text
    assert "al día" in outcome.text.lower()


def test_consulta_deuda_por_vencer_branch():
    """por_vencer credit_state uses the por_vencer branch."""
    spec = _spec()
    profile = _al_dia_profile()
    session_state = {"credit_state": "por_vencer"}

    outcome = R.handle_consulta_deuda(
        spec, profile, session_state=session_state, source=R.SOURCE_KEYWORD
    )
    assert outcome is not None
    assert outcome.handled is True
    # por_vencer branch contains 'próxima cuota vence'
    assert "vence" in outcome.text.lower()


# ── (b) vencido consulta_deuda shows overdue copy ────────────────────────────

def test_consulta_deuda_vencido_shows_overdue_branch():
    """vencido credit_state must show the overdue branch copy and vencido menu."""
    spec = _spec()
    profile = _vencido_profile()
    session_state = {"credit_state": "vencido"}

    outcome = R.handle_consulta_deuda(
        spec, profile, session_state=session_state, source=R.SOURCE_KEYWORD
    )
    assert outcome is not None
    assert outcome.handled is True
    assert outcome.intent == "consulta_deuda"
    # vencido branch must mention overdue context (días de atraso or saldo)
    text_lower = outcome.text.lower()
    assert "venci" in text_lower or "atraso" in text_lower or "saldo" in text_lower
    # The vencido option menu must be present (e.g. 'Realizar pago' or 'Compromiso')
    assert "pago" in outcome.text.lower()


# ── (c) 2 consecutive unrecognized inputs → asesor ───────────────────────────

def test_two_consecutive_misunderstood_escalates_to_asesor():
    """Strike 1 → no_comprendida_1; strike 2 → asesor escalation."""
    spec = _spec()
    profile = _al_dia_profile()
    session_state: dict = {}

    # Strike 1
    outcome1 = R.record_misunderstood(
        spec, profile, session_state=session_state, source=R.SOURCE_KEYWORD
    )
    assert outcome1.handled is True
    assert outcome1.intent == "no_comprendida_1"
    assert session_state.get("misunderstood_count") == 1

    # Strike 2
    outcome2 = R.record_misunderstood(
        spec, profile, session_state=session_state, source=R.SOURCE_KEYWORD
    )
    assert outcome2.handled is True
    assert outcome2.intent == "no_comprendida_2_asesor"
    assert session_state.get("misunderstood_count") == 2


def test_misunderstood_count_resets_on_handled_intent():
    """Any handled intent resets the strike counter."""
    spec = _spec()
    profile = _al_dia_profile()
    session_state = {"credit_state": "al_dia", "misunderstood_count": 1}

    outcome = R.handle_consulta_deuda(
        spec, profile, session_state=session_state, source=R.SOURCE_KEYWORD
    )
    assert outcome is not None
    assert outcome.handled is True
    assert "misunderstood_count" not in session_state


# ── (d) vencido + domingo_feriado intent → vencido menu redirect ─────────────

def test_domingo_feriado_intent_redirects_vencido_to_vencido_menu():
    """When credit_state=vencido, domingo/feriado intent must NOT show holiday copy.
    Instead it returns the vencido menu redirect (domingo_feriado_vencido_redirect).
    """
    spec = _spec()
    profile = _vencido_profile()

    # The spec must have the vencido redirect intent
    assert "domingo_feriado_vencido_redirect" in spec.intents
    # And the al_dia/por_vencer holiday intent
    assert "domingo_feriado_al_dia_por_vencer" in spec.intents

    # When credit_state=vencido, the redirect template is used (not holiday copy)
    result = R.render_intent(
        spec, "domingo_feriado_vencido_redirect", profile, source=R.SOURCE_KEYWORD
    )
    assert result is not None
    # Must NOT contain holiday-specific text like "traslada al siguiente día hábil"
    assert "traslada" not in result.text.lower()
    # Must redirect to vencido context
    text_lower = result.text.lower()
    assert "pago" in text_lower or "regularizar" in text_lower


def test_domingo_feriado_al_dia_shows_holiday_copy():
    """al_dia/por_vencer credit_state gets the informational holiday copy."""
    spec = _spec()
    profile = _al_dia_profile()

    result = R.render_intent(
        spec, "domingo_feriado_al_dia_por_vencer", profile, source=R.SOURCE_KEYWORD
    )
    assert result is not None
    # Must contain business rule about holiday shift
    assert "traslada" in result.text.lower() or "siguiente día hábil" in result.text.lower()


# ── (e) multi-credit cuentas bancarias has labeled rows ──────────────────────

def test_render_cuentas_bancarias_multi_credit_has_labeled_rows():
    """render_cuentas_bancarias with 2 credits must produce one labeled row per credit."""
    credits = [
        {
            "account_id": "P03886",
            "loan_number": "P03886",
            "inversionista": "INVERSIONISTA GAMMA",
            "cci": "00398765432109876543",
            "numero_de_cuenta": "3987654321",
        },
        {
            "account_id": "P03887",
            "loan_number": "P03887",
            "inversionista": "INVERSIONISTA DELTA",
            "cci": "00312345678901111111",
            "numero_de_cuenta": "1234567890",
        },
    ]
    result = render_cuentas_bancarias(credits)

    # Each credit gets a labeled row
    assert "[P03886]" in result
    assert "[P03887]" in result
    assert "INVERSIONISTA GAMMA" in result
    assert "INVERSIONISTA DELTA" in result
    assert "00398765432109876543" in result
    assert "00312345678901111111" in result
    # Two separate lines (one per credit)
    lines = [line for line in result.strip().splitlines() if line.strip()]
    assert len(lines) == 2


def test_render_cuentas_bancarias_single_credit_no_label():
    """Single credit returns plain string without bracket label."""
    credits = [
        {
            "account_id": "P04069",
            "loan_number": "P04069",
            "inversionista": "INVERSIONISTA ALPHA",
            "cci": "00312345678901234567",
            "numero_de_cuenta": "1234567890",
        }
    ]
    result = render_cuentas_bancarias(credits)
    # Single credit: no bracket label
    assert "[P04069]" not in result
    assert "INVERSIONISTA ALPHA" in result
    assert "00312345678901234567" in result


# ── vencido-only guard ────────────────────────────────────────────────────────

def test_vencido_only_guard_blocks_compromiso_for_al_dia():
    """compromiso_pago intent must be blocked (redirected) for al_dia users."""
    spec = _spec()
    profile = _al_dia_profile()
    session_state = {"credit_state": "al_dia"}

    outcome = R.handle_vencido_only_intent(
        "compromiso_pago", spec, profile,
        session_state=session_state, source=R.SOURCE_KEYWORD
    )
    # Must redirect (not None) — returns the credit-state menu
    assert outcome is not None
    assert outcome.handled is True
    # The redirect shows al_dia copy, not compromiso copy
    assert "compromiso" not in outcome.text.lower() or "al día" in outcome.text.lower()


def test_vencido_only_guard_allows_compromiso_for_vencido():
    """compromiso_pago is allowed for vencido — guard returns None."""
    spec = _spec()
    profile = _vencido_profile()
    session_state = {"credit_state": "vencido"}

    outcome = R.handle_vencido_only_intent(
        "compromiso_pago", spec, profile,
        session_state=session_state, source=R.SOURCE_KEYWORD
    )
    # None means "allowed — caller proceeds"
    assert outcome is None


def test_vencido_only_guard_passes_through_non_vencido_intents():
    """Intents NOT in the vencido-only set return None regardless of credit_state."""
    spec = _spec()
    profile = _al_dia_profile()
    session_state = {"credit_state": "al_dia"}

    outcome = R.handle_vencido_only_intent(
        "cronograma", spec, profile,
        session_state=session_state, source=R.SOURCE_KEYWORD
    )
    assert outcome is None


# ── JSON structure validation for new intents ─────────────────────────────────

def test_new_intents_have_required_fields():
    """Every new Phase 3 intent in responses.json has mode + template or branches."""
    data = json.loads((_tenant_dir() / "responses.json").read_text(encoding="utf-8"))

    new_intents = [
        "consulta_deuda_total",
        "cronograma",
        "fecha_venc_contrato",
        "cuotas_pagadas",
        "cuotas_pendientes",
        "cuentas_bancarias",
        "ya_pague",
        "no_puede_pagar",
        "alternativas",
        "domingo_feriado_al_dia_por_vencer",
        "domingo_feriado_vencido_redirect",
        "fuera_de_horario",
        "no_comprendida_1",
        "no_comprendida_2_asesor",
        "realizar_pago_vencido",
        "compromiso_pago",
    ]
    for intent in new_intents:
        assert intent in data, f"Missing intent: {intent}"
        cfg = data[intent]
        assert cfg.get("mode") in ("verbatim", "variant"), f"{intent}: invalid mode"
        has_content = cfg.get("template") or cfg.get("variants") or cfg.get("list")
        assert has_content, f"{intent}: no template or variants"

    # consulta_deuda has credit_state_branches
    assert "credit_state_branches" in data["consulta_deuda"]
    branches = data["consulta_deuda"]["credit_state_branches"]
    assert "al_dia" in branches
    assert "por_vencer" in branches
    assert "vencido" in branches

    # No separate top-level consulta_deuda_al_dia/_por_vencer/_vencido
    assert "consulta_deuda_al_dia" not in data
    assert "consulta_deuda_por_vencer" not in data
    assert "consulta_deuda_vencido" not in data


# ── Layer 1 routing: cuotas_pendientes vs consulta_deuda ──────────────────────

def test_cuantas_me_faltan_routes_to_cuotas_pendientes():
    """Accented "cuántas me faltan" (without the word "cuotas") must match
    cuotas_pendientes at Layer 1, not fall through to consulta_deuda."""
    spec = _spec()
    for text in (
        "cuántas me faltan",
        "cuantas me faltan",
        "¿cuántas cuotas me faltan?",
        "cuántas cuotas faltan",
        "cuánto me falta pagar",
    ):
        match = R.match_keyword_intent(text, spec)
        assert match is not None, f"no Layer-1 match for: {text}"
        assert match[0] == "cuotas_pendientes", f"{text!r} routed to {match[0]}"


def test_money_questions_still_route_to_consulta_deuda():
    """The new faltan pattern must not steal money/saldo questions."""
    spec = _spec()
    for text in ("cuánto debo", "cuánto saldo me falta", "mi deuda pendiente"):
        match = R.match_keyword_intent(text, spec)
        assert match is not None, f"no Layer-1 match for: {text}"
        assert match[0] == "consulta_deuda", f"{text!r} routed to {match[0]}"


def test_no_nivel_n1_n2_n3_in_responses_json():
    """Terminology guard: no nivel/n1/n2/n3 in responses.json values."""
    raw = (_tenant_dir() / "responses.json").read_text(encoding="utf-8")
    raw_lower = raw.lower()
    # Check none of the forbidden terms appear in string values
    assert '"nivel"' not in raw_lower
    assert '"n1"' not in raw_lower
    assert '"n2"' not in raw_lower
    assert '"n3"' not in raw_lower
