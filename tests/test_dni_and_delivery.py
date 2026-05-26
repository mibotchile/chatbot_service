"""Tests for DNI-first identity + multichannel document delivery (correo/WhatsApp).

No network: EmailService/WhatsAppService run in dry-run (not configured) and we
assert their return + that destination comes from the verified profile, never
from the LLM.
"""

from __future__ import annotations

import pytest

from core.email_service import EmailService
from core.whatsapp_service import WhatsAppService
from integrations.mock_debt_source import resolve_dni, resolve_token
from tools import ToolRegistry

JUAN_DNI = "41785236"     # al día (con deuda)
CARLOS_DNI = "09823514"   # en mora (con deuda)
MARIA_DNI = "72514893"    # sin deuda (cancelado)


# ── DNI resolver ──────────────────────────────────────────────────────────

def test_resolve_dni_valid_profiles():
    assert resolve_dni(JUAN_DNI)["borrower_name"] == "Juan Pérez Rojas"
    assert resolve_dni(CARLOS_DNI)["status"] == "en_mora"
    assert resolve_dni(MARIA_DNI)["balance"] == 0.0


def test_resolve_dni_tolerates_spaces_and_dots():
    assert resolve_dni("417 852 36") is not None
    assert resolve_dni("41.785.236") is not None


def test_resolve_dni_invalid_returns_none():
    assert resolve_dni("00000000") is None     # not found
    assert resolve_dni("123") is None           # wrong length
    assert resolve_dni("") is None


# ── identificar_cliente opens the gate; bad DNI does not ───────────────────

async def test_identificar_cliente_opens_gate_and_persists():
    persisted = {}
    reg = ToolRegistry(
        identity_verified=False,
        on_identity_resolved=lambda p: persisted.update(p),
    )
    # gated tool blocked before identification
    blocked = await reg.execute("consultar_deuda", {})
    assert blocked.get("blocked") == "identity_required"

    ident = await reg.execute("identificar_cliente", {"dni": JUAN_DNI})
    assert ident["identified"] is True
    assert persisted.get("account_id") == "ACC-PYPE-2024-00123"  # persisted server-side

    # now the gate is open within the same registry
    deuda = await reg.execute("consultar_deuda", {})
    assert "blocked" not in deuda
    assert deuda["account_id"] == "ACC-PYPE-2024-00123"


async def test_identificar_cliente_invalid_does_not_open_gate():
    reg = ToolRegistry(identity_verified=False)
    ident = await reg.execute("identificar_cliente", {"dni": "00000000"})
    assert ident["identified"] is False
    # gate stays closed
    assert (await reg.execute("consultar_deuda", {})).get("blocked") == "identity_required"


# ── enviar_documento is gated ──────────────────────────────────────────────

async def test_enviar_documento_blocked_without_identity():
    reg = ToolRegistry(identity_verified=False)
    r = await reg.execute("enviar_documento", {"tipo": "estado_cuenta", "canal": "correo"})
    assert r.get("blocked") == "identity_required"


# ── Email delivery (dry-run) — destino from profile, not LLM ───────────────

async def test_send_document_email_dryrun_estado_cuenta():
    reg = ToolRegistry(
        identity_verified=True,
        debt_context=resolve_dni(CARLOS_DNI),
        email_service=EmailService(api_url=""),  # not configured → dry-run
    )
    r = await reg.execute("enviar_documento", {"tipo": "estado_cuenta", "canal": "correo"})
    assert r["delivered"] is True          # dry-run reports logged-true
    assert r["canal"] == "correo"
    assert r["destino"].endswith("@correo.pe")   # masked, from profile
    assert r["destino"].startswith("car") and "***" in r["destino"]  # masked, not full


async def test_send_document_email_certificate_only_when_no_debt():
    # María (no debt) → certificate delivered
    reg_ok = ToolRegistry(
        identity_verified=True, debt_context=resolve_dni(MARIA_DNI),
        email_service=EmailService(api_url=""), download_base_url="http://t",
    )
    ok = await reg_ok.execute("enviar_documento", {"tipo": "certificado_no_adeudo", "canal": "correo"})
    assert ok["delivered"] is True
    assert ok["doc_ref"].startswith("CNA-")

    # Carlos (debt) → certificate does NOT proceed
    reg_no = ToolRegistry(
        identity_verified=True, debt_context=resolve_dni(CARLOS_DNI),
        email_service=EmailService(api_url=""), download_base_url="http://t",
    )
    no = await reg_no.execute("enviar_documento", {"tipo": "certificado_no_adeudo", "canal": "correo"})
    assert no.get("issued") is False
    assert no["reason"] == "outstanding_balance"


# ── WhatsApp delivery (backlog: honest dry-run) ────────────────────────────

async def test_send_document_whatsapp_dryrun():
    reg = ToolRegistry(
        identity_verified=True, debt_context=resolve_dni(MARIA_DNI),
        whatsapp_service=WhatsAppService(api_url="", api_key="", instance_name=""),
        download_base_url="http://t",
    )
    r = await reg.execute("enviar_documento", {"tipo": "certificado_no_adeudo", "canal": "whatsapp"})
    assert r["canal"] == "whatsapp"
    assert r["destino"].startswith("+51 ***")     # masked phone from profile
    # not configured → honest backlog/dry-run, never a fake success
    assert r["channel_status"] == "backlog_or_dry_run"


async def test_enviar_documento_rejects_unknown_type_and_channel():
    reg = ToolRegistry(identity_verified=True, debt_context=resolve_dni(JUAN_DNI))
    assert (await reg.execute("enviar_documento", {"tipo": "x", "canal": "correo"})).get("error") == "tipo_invalido"
    assert (await reg.execute("enviar_documento", {"tipo": "estado_cuenta", "canal": "x"})).get("error") == "canal_invalido"


# ── Chip/offer gating by balance: certificate tool blocks for debtors ──────

async def test_certificate_tool_gates_by_balance():
    for dni in (JUAN_DNI, CARLOS_DNI):
        reg = ToolRegistry(identity_verified=True, debt_context=resolve_dni(dni), download_base_url="http://t")
        r = await reg.execute("emitir_certificado_no_adeudo", {})
        assert r["issued"] is False and r["reason"] == "outstanding_balance"
    reg = ToolRegistry(identity_verified=True, debt_context=resolve_dni(MARIA_DNI), download_base_url="http://t")
    assert (await reg.execute("emitir_certificado_no_adeudo", {}))["issued"] is True


# ── Token still works (pre-identified) alongside DNI ───────────────────────

def test_token_still_resolves():
    assert resolve_token("demo-juan")["account_id"] == "ACC-PYPE-2024-00123"
