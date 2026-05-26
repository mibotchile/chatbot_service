"""Tests for the PrestaUnion cobranza demo: identity gate + 3 tools.

Covers the non-negotiable security gate and the three use cases against the
three fictitious borrower profiles.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from integrations.mock_debt_source import resolve_token
from tools import ToolRegistry

JUAN = "demo-juan"      # al día
CARLOS = "demo-carlos"  # en mora
MARIA = "demo-maria"    # sin deuda (cancelado)


# ── Token resolver ──────────────────────────────────────────────────────

def test_resolve_token_returns_correct_profiles():
    juan = resolve_token(JUAN)
    carlos = resolve_token(CARLOS)
    maria = resolve_token(MARIA)
    assert juan["account_id"] == "ACC-PYPE-2024-00123"
    assert juan["business_name"] == "Bodega Don Juan E.I.R.L."
    assert juan["status"] == "al_dia"
    assert carlos["status"] == "en_mora"
    assert carlos["days_overdue"] == 8
    assert maria["status"] == "cancelado"
    assert maria["balance"] == 0.0


def test_resolve_token_unknown_returns_none():
    assert resolve_token("demo-nope") is None
    assert resolve_token("") is None


# ── Identity gate (HARD) ────────────────────────────────────────────────

async def test_gate_blocks_debt_tools_without_identity():
    reg = ToolRegistry(identity_verified=False, debt_context={})
    for tool in ("consultar_deuda", "registrar_reclamo", "emitir_certificado_no_adeudo"):
        args = {"tipo": "reclamo", "descripcion": "x"} if tool == "registrar_reclamo" else {}
        result = await reg.execute(tool, args)
        assert result.get("blocked") == "identity_required", tool


async def test_gate_allows_generic_tools_without_identity():
    reg = ToolRegistry(identity_verified=False, debt_context={})
    result = await reg.execute("suggest_quick_replies", {"options": ["Sí", "No"]})
    assert result.get("validated") is True


async def test_gate_opens_with_verified_identity():
    reg = ToolRegistry(identity_verified=True, debt_context=resolve_token(JUAN))
    result = await reg.execute("consultar_deuda", {})
    assert "blocked" not in result
    assert result["account_id"] == "ACC-PYPE-2024-00123"


# ── consultar_deuda per profile ─────────────────────────────────────────

async def test_consultar_deuda_juan_al_dia():
    reg = ToolRegistry(identity_verified=True, debt_context=resolve_token(JUAN))
    r = await reg.execute("consultar_deuda", {})
    assert r["status"] == "al_dia"
    assert r["balance"] == 4850.00
    assert r["installments_pending"] == 3
    assert r["next_due_date"] == "2026-06-15"
    assert r["next_installment_amount"] == 1650.00
    assert r["has_debt"] is True
    assert r["late_fee"] == 0.0


async def test_consultar_deuda_carlos_en_mora():
    reg = ToolRegistry(identity_verified=True, debt_context=resolve_token(CARLOS))
    r = await reg.execute("consultar_deuda", {})
    assert r["status"] == "en_mora"
    assert r["balance"] == 2300.00
    assert r["late_fee"] == 85.00
    assert r["days_overdue"] == 8
    assert r["has_debt"] is True


async def test_consultar_deuda_maria_sin_deuda():
    reg = ToolRegistry(identity_verified=True, debt_context=resolve_token(MARIA))
    r = await reg.execute("consultar_deuda", {})
    assert r["status"] == "cancelado"
    assert r["balance"] == 0.0
    assert r["has_debt"] is False


# ── registrar_reclamo ───────────────────────────────────────────────────

async def test_registrar_reclamo_generates_folio_and_deadline():
    reg = ToolRegistry(identity_verified=True, debt_context=resolve_token(CARLOS))
    r = await reg.execute(
        "registrar_reclamo",
        {"tipo": "reclamo", "descripcion": "El recargo por mora no coincide con lo informado."},
    )
    assert r["registered"] is True
    assert re.fullmatch(r"LR-\d{4}-\d{5}", r["folio"]), r["folio"]
    assert r["response_business_days"] == 15
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["response_deadline"])


async def test_registrar_reclamo_requires_description():
    reg = ToolRegistry(identity_verified=True, debt_context=resolve_token(JUAN))
    r = await reg.execute("registrar_reclamo", {"tipo": "queja", "descripcion": "   "})
    assert r.get("error") == "descripcion_required"


# ── emitir_certificado_no_adeudo ────────────────────────────────────────

async def test_certificado_only_for_maria_sin_deuda():
    reg = ToolRegistry(identity_verified=True, debt_context=resolve_token(MARIA))
    r = await reg.execute("emitir_certificado_no_adeudo", {})
    assert r["issued"] is True
    assert r["folio"].startswith("CNA-")
    assert r["download_url"].endswith(".pdf")
    pdf = Path("/tmp/prestaunion_certificates") / r["filename"]
    assert pdf.exists() and pdf.stat().st_size > 0
    # confirm it's a real PDF
    assert pdf.read_bytes()[:5] == b"%PDF-"


async def test_certificado_blocked_when_debt_outstanding():
    for token in (JUAN, CARLOS):
        reg = ToolRegistry(identity_verified=True, debt_context=resolve_token(token))
        r = await reg.execute("emitir_certificado_no_adeudo", {})
        assert r["issued"] is False
        assert r["reason"] == "outstanding_balance"


# ── No account_id accepted from the LLM (server-side identity) ──────────

async def test_consultar_deuda_ignores_llm_supplied_account():
    """Even if the LLM tries to pass account_id, the tool reads from debt_context."""
    reg = ToolRegistry(identity_verified=True, debt_context=resolve_token(JUAN))
    # The schema has no account_id param; execute() would raise TypeError if passed.
    with pytest.raises(TypeError):
        await reg.execute("consultar_deuda", {"account_id": "ACC-PYPE-2024-00210"})
