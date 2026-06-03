"""Tests for DNI-first identity + multichannel document delivery (correo/WhatsApp).

No network: EmailService/WhatsAppService run in dry-run (not configured) and we
assert their return + that destination comes from the verified profile, never
from the LLM.
"""

from __future__ import annotations

import pytest

from shared.delivery.email_delivery import EmailService
from features.messaging.whatsapp_service import WhatsAppService
from features.cobranza.mock_debt_source import resolve_dni, resolve_token
from shared.tool_registry import ToolRegistry

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


# ── Delivery uses the USER-PROVIDED destination, not the profile's ─────────

async def test_send_document_uses_user_destino_not_profile():
    """Demo tangible: the document goes to the address the USER typed."""
    carlos = resolve_dni(CARLOS_DNI)
    reg = ToolRegistry(
        identity_verified=True,
        debt_context=carlos,
        email_service=EmailService(api_url=""),  # not configured → dry-run
    )
    r = await reg.execute(
        "enviar_documento",
        {"tipo": "estado_cuenta", "destino": "test@ejemplo.com"},  # canal inferred
    )
    assert r["delivered"] is True
    assert r["canal"] == "correo"                 # inferred from "@"
    assert r["destino"] == "test@ejemplo.com"     # the user's address, verbatim
    assert r["destino"] != carlos["email"]        # NOT the profile's email


async def test_send_document_invalid_email_asks_again():
    reg = ToolRegistry(identity_verified=True, debt_context=resolve_dni(JUAN_DNI),
                        email_service=EmailService(api_url=""))
    r = await reg.execute("enviar_documento", {"tipo": "estado_cuenta", "destino": "no-es-un-correo", "canal": "correo"})
    assert r.get("error") == "email_invalido"
    assert "delivered" not in r  # error path → nothing was sent


async def test_send_document_missing_destino_prompts():
    reg = ToolRegistry(identity_verified=True, debt_context=resolve_dni(JUAN_DNI))
    r = await reg.execute("enviar_documento", {"tipo": "estado_cuenta"})
    assert r.get("error") == "destino_requerido"
    assert "correo" in r["message"].lower() or "whatsapp" in r["message"].lower()


async def test_send_document_email_certificate_only_when_no_debt():
    # María (no debt) → certificate delivered to the address she gives
    reg_ok = ToolRegistry(
        identity_verified=True, debt_context=resolve_dni(MARIA_DNI),
        email_service=EmailService(api_url=""), download_base_url="http://t",
    )
    ok = await reg_ok.execute(
        "enviar_documento",
        {"tipo": "certificado_no_adeudo", "destino": "yo@ejemplo.com"},
    )
    assert ok["delivered"] is True
    assert ok["destino"] == "yo@ejemplo.com"
    assert ok["doc_ref"].startswith("CNA-")

    # Carlos (debt) → certificate does NOT proceed (gated by balance)
    reg_no = ToolRegistry(
        identity_verified=True, debt_context=resolve_dni(CARLOS_DNI),
        email_service=EmailService(api_url=""), download_base_url="http://t",
    )
    no = await reg_no.execute(
        "enviar_documento",
        {"tipo": "certificado_no_adeudo", "destino": "yo@ejemplo.com"},
    )
    assert no.get("issued") is False
    assert no["reason"] == "outstanding_balance"


# ── WhatsApp delivery (backlog: honest dry-run) — user-provided number ─────

async def test_send_document_whatsapp_dryrun():
    reg = ToolRegistry(
        identity_verified=True, debt_context=resolve_dni(MARIA_DNI),
        whatsapp_service=WhatsAppService(api_url="", api_key="", instance_name=""),
        download_base_url="http://t",
    )
    r = await reg.execute(
        "enviar_documento",
        {"tipo": "certificado_no_adeudo", "destino": "999111222"},  # canal inferred → whatsapp
    )
    assert r["canal"] == "whatsapp"
    assert r["destino"] == "999111222"            # the number the user gave
    # not configured → honest backlog/dry-run, never a fake success
    assert r["channel_status"] == "backlog_or_dry_run"


async def test_send_document_invalid_phone_asks_again():
    reg = ToolRegistry(identity_verified=True, debt_context=resolve_dni(MARIA_DNI),
                        whatsapp_service=WhatsAppService(api_url="", api_key="", instance_name=""))
    r = await reg.execute("enviar_documento", {"tipo": "estado_cuenta", "destino": "123", "canal": "whatsapp"})
    assert r.get("error") == "telefono_invalido"


async def test_enviar_documento_rejects_unknown_type():
    reg = ToolRegistry(identity_verified=True, debt_context=resolve_dni(JUAN_DNI))
    r = await reg.execute("enviar_documento", {"tipo": "x", "destino": "a@b.co"})
    assert r.get("error") == "tipo_invalido"


# ── Chip/offer gating by balance: certificate tool blocks for debtors ──────

async def test_certificate_tool_gates_by_balance():
    for dni in (JUAN_DNI, CARLOS_DNI):
        reg = ToolRegistry(identity_verified=True, debt_context=resolve_dni(dni), download_base_url="http://t")
        r = await reg.execute("emitir_certificado_no_adeudo", {})
        assert r["issued"] is False and r["reason"] == "outstanding_balance"
    reg = ToolRegistry(identity_verified=True, debt_context=resolve_dni(MARIA_DNI), download_base_url="http://t")
    assert (await reg.execute("emitir_certificado_no_adeudo", {}))["issued"] is True


# ── WhatsApp delivery: estado as TEXT, certificate as PDF (media_url) ──────


class _SpyWhatsApp:
    """Records send_text/send_document calls; reports as configured."""

    def __init__(self):
        self.is_configured = True
        self.text_calls: list[tuple[str, str]] = []
        self.document_calls: list[dict] = []

    async def send_text(self, phone, message, incoming_id=None):
        self.text_calls.append((phone, message))
        return True

    async def send_document(self, phone, customer_name, doc_label, media_url="", caption=""):
        self.document_calls.append(
            {"phone": phone, "name": customer_name, "label": doc_label, "media_url": media_url}
        )
        return bool(media_url)


async def test_estado_cuenta_whatsapp_sends_text_summary():
    """estado_cuenta over WhatsApp → send_text with the plain-text summary,
    NOT an empty send_document."""
    spy = _SpyWhatsApp()
    carlos = resolve_dni(CARLOS_DNI)  # en mora → tiene recargo
    reg = ToolRegistry(identity_verified=True, debt_context=carlos, whatsapp_service=spy)
    r = await reg.execute(
        "enviar_documento", {"tipo": "estado_cuenta", "destino": "999111222"}
    )
    assert r["canal"] == "whatsapp"
    assert r["delivered"] is True
    assert len(spy.text_calls) == 1
    assert len(spy.document_calls) == 0  # never tried to send an empty PDF
    _, body = spy.text_calls[0]
    assert "Estado de cuenta" in body
    assert carlos["status_label"] in body
    assert "Saldo pendiente" in body
    assert "Recargo por mora" in body  # Carlos está en mora


async def test_certificate_whatsapp_builds_media_url_with_public_base():
    """certificado over WhatsApp → send_document with media_url built from the
    public base URL passed as download_base_url."""
    spy = _SpyWhatsApp()
    maria = resolve_dni(MARIA_DNI)  # sin deuda → certificado procede
    reg = ToolRegistry(
        identity_verified=True,
        debt_context=maria,
        whatsapp_service=spy,
        download_base_url="https://demos.mibot.cl/pubot-gj5w2a0p",
    )
    r = await reg.execute(
        "enviar_documento", {"tipo": "certificado_no_adeudo", "destino": "999111222"}
    )
    assert r["canal"] == "whatsapp"
    assert len(spy.document_calls) == 1
    assert len(spy.text_calls) == 0
    media_url = spy.document_calls[0]["media_url"]
    assert media_url.startswith("https://demos.mibot.cl/pubot-gj5w2a0p/api/v1/cobranza/certificate/")
    assert media_url.endswith(".pdf")
    assert r["delivered"] is True  # spy returns True when media_url present


# ── Token still works (pre-identified) alongside DNI ───────────────────────

def test_token_still_resolves():
    assert resolve_token("demo-juan")["account_id"] == "ACC-PYPE-2024-00123"
