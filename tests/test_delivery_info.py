"""Tests for envío de información bajo demanda (CORE, data-driven, demo simulado).

Covers the new ``enviar_info`` tool + the data-driven deliverables/flow:
  - renders the right copy (estado single/multi-deuda, datos de pago, constancia);
  - SIMULATES in demo mode (delivery_mode="simulate") — never calls SendGrid/ChatHub;
  - sends REAL in prod mode and masks the destination either way;
  - sends to the borrower's REGISTERED destination (never user-typed);
  - identity gate blocks the tool without a verified profile;
  - the channel-choice flow (set_session tipo → elegir_canal canal) round-trips.

No network: services are spies / not-configured, so nothing leaves the process.
"""

from __future__ import annotations

from pathlib import Path

from tenancy.responses_spec import ResponsesSpec
from features.conversation.responses import (
    route_layer1,
    resolve_classified_intent,
)
from api.tool_registry import ToolRegistry
from features.cobranza.tools import (
    enviar_info,
    mask_email,
    mask_phone,
    render_deliverable,
    _normalize_canal,
)

# ── Synthetic profiles (mirror the prestamype fixture shapes) ──────────────

SINGLE = {
    "account_id": "P02137",
    "loan_number": "P02137",
    "borrower_name": "CARLOS ANTONIO MENDOZA RIVERA",
    "email": "cmendoza.demo@example.com",
    "phone": "951000111",
    "currency_symbol": "S/",
    "balance": 18420.0,
    "next_installment_amount": 462.14,
    "next_due_date": "2026-06-18",
    "status_label": "Al día",
    "cci": "00389801338381007048",
    "banco": "INTERBANK",
}

MULTI = {
    "account_id": "P05012",
    "loan_number": "P05012",
    "borrower_name": "LUCIA FERNANDA TORRES VEGA",
    "email": "ltorres.demo@example.com",
    "phone": "999000222",
    "currency_symbol": "S/",
    "balance": 9120.5,
    "next_installment_amount": 612.3,
    "next_due_date": "2026-06-12",
    "status_label": "Al día",
    "cci": "00313701313333339674",
    "banco": "INTERBANK",
    "additional_credits": [
        {
            "account_id": "P05119",
            "loan_number": "P05119",
            "currency_symbol": "S/",
            "balance": 26340.0,
            "next_installment_amount": 880.45,
            "next_due_date": "2026-05-14",
            "status_label": "En mora",
        }
    ],
}


def _prestamype_spec() -> ResponsesSpec:
    tenant_dir = Path(__file__).resolve().parent.parent / "tenants" / "prestamype"
    return ResponsesSpec.from_dir(tenant_dir, response_mode="hybrid")


def _deliverables() -> dict:
    return _prestamype_spec().deliverables


# ── Masking ────────────────────────────────────────────────────────────────

def test_mask_email_hides_local_and_domain():
    m = mask_email("cmendoza.demo@example.com")
    assert m == "c···@···.com"
    assert "cmendoza" not in m
    assert "example" not in m


def test_mask_phone_keeps_last_four():
    assert mask_phone("951000111") == "···0111"
    assert mask_phone("") == "···"


def test_normalize_canal_variants():
    assert _normalize_canal("correo") == "correo"
    assert _normalize_canal("email") == "correo"
    assert _normalize_canal("WhatsApp") == "whatsapp"
    assert _normalize_canal("wsp") == "whatsapp"


# ── Deliverables spec loads from responses.json (data-driven) ──────────────

def test_deliverables_loaded_from_responses_json():
    d = _deliverables()
    assert set(d) >= {"estado_cuenta", "datos_pago", "constancia_comprobante"}
    assert d["estado_cuenta"]["correo"]["subject"]
    assert d["datos_pago"]["whatsapp"]["text"]


# ── Rendering: variables filled from the verified profile ──────────────────

def test_render_estado_single_correo_fills_balance():
    r = render_deliverable(_deliverables()["estado_cuenta"], "correo", SINGLE)
    assert "S/ 18,420.00" in r["body"]
    assert "P02137" in r["body"]
    assert r["subject"]


def test_render_estado_multi_lists_both_credits():
    r = render_deliverable(_deliverables()["estado_cuenta"], "correo", MULTI)
    assert "P05012" in r["body"]
    assert "P05119" in r["body"]          # second credit listed
    assert "S/ 35,460.50" in r["body"]    # total = 9120.50 + 26340.00


def test_render_datos_pago_whatsapp_has_cci_and_banco():
    r = render_deliverable(_deliverables()["datos_pago"], "whatsapp", SINGLE)
    assert "00389801338381007048" in r["text"]
    assert "INTERBANK" in r["text"]


# ── Demo (simulate): NO SendGrid/ChatHub call, masked destino ──────────────

class _SpyEmail:
    def __init__(self):
        self.calls = []

    async def send_document(
        self, to_email, name, label, *, pdf_path=None, summary_html="", tenant_slug=""
    ):
        self.calls.append((to_email, label, summary_html))
        return True


class _SpyChathub:
    def __init__(self, configured=False):
        self.is_configured = configured
        self.calls = []

    async def send_text(self, phone, message):
        self.calls.append((phone, message))
        return True


async def test_demo_correo_is_simulated_no_send():
    spy = _SpyEmail()
    r = await enviar_info(
        SINGLE, "estado_cuenta", "correo",
        deliverables=_deliverables(), delivery_mode="simulate", email_service=spy,
    )
    assert r["delivered"] is True
    assert r["simulated"] is True
    assert r["canal"] == "correo"
    assert r["destino_masked"] == "c···@···.com"
    assert spy.calls == []  # SendGrid NEVER called in demo


async def test_demo_whatsapp_is_simulated_no_send():
    spy = _SpyChathub(configured=True)  # even if configured, demo simulates
    r = await enviar_info(
        SINGLE, "datos_pago", "whatsapp",
        deliverables=_deliverables(), delivery_mode="simulate", chathub_outbound=spy,
    )
    assert r["delivered"] is True
    assert r["simulated"] is True
    assert r["canal"] == "whatsapp"
    assert r["destino_masked"] == "···0111"
    assert spy.calls == []  # ChatHub NEVER called in demo


# ── Prod (real): calls the services, still masks the confirmation ──────────

async def test_real_correo_calls_sendgrid():
    spy = _SpyEmail()
    r = await enviar_info(
        SINGLE, "estado_cuenta", "correo",
        deliverables=_deliverables(), delivery_mode="real", email_service=spy,
    )
    assert r["delivered"] is True
    assert r["simulated"] is False
    assert len(spy.calls) == 1
    assert spy.calls[0][0] == SINGLE["email"]  # registered destination


async def test_real_whatsapp_calls_chathub_when_configured():
    spy = _SpyChathub(configured=True)
    r = await enviar_info(
        SINGLE, "datos_pago", "whatsapp",
        deliverables=_deliverables(), delivery_mode="real", chathub_outbound=spy,
    )
    assert r["delivered"] is True
    assert r["simulated"] is False
    assert r["channel_status"] == "configured"
    assert len(spy.calls) == 1
    assert spy.calls[0][0] == SINGLE["phone"]  # registered destination


async def test_real_whatsapp_simulates_when_chathub_pending():
    """Prod tenant but ChatHub outbound NOT provisioned yet → honest simulate."""
    spy = _SpyChathub(configured=False)
    r = await enviar_info(
        SINGLE, "datos_pago", "whatsapp",
        deliverables=_deliverables(), delivery_mode="real", chathub_outbound=spy,
    )
    assert r["delivered"] is True
    assert r["simulated"] is True
    assert r["channel_status"] == "chathub_pending"
    assert spy.calls == []


# ── Validation / errors ─────────────────────────────────────────────────────

async def test_unknown_tipo_rejected():
    r = await enviar_info(SINGLE, "no_existe", "correo", deliverables=_deliverables())
    assert r.get("error") == "tipo_no_disponible"


async def test_missing_canal_asks():
    r = await enviar_info(SINGLE, "estado_cuenta", "", deliverables=_deliverables())
    assert r.get("error") == "canal_requerido"


async def test_no_email_on_profile_offers_whatsapp():
    prof = {**SINGLE, "email": ""}
    r = await enviar_info(prof, "estado_cuenta", "correo", deliverables=_deliverables())
    assert r.get("error") == "sin_correo"


# ── Identity gate: enviar_info blocked without a verified profile ──────────

async def test_enviar_info_blocked_without_identity():
    reg = ToolRegistry(identity_verified=False, deliverables=_deliverables())
    r = await reg.execute("enviar_info", {"tipo": "estado_cuenta", "canal": "correo"})
    assert r.get("blocked") == "identity_required"


async def test_enviar_info_via_registry_simulates_in_demo():
    reg = ToolRegistry(
        identity_verified=True,
        debt_context=SINGLE,
        deliverables=_deliverables(),
        delivery_mode="simulate",
        email_service=_SpyEmail(),
    )
    r = await reg.execute("enviar_info", {"tipo": "estado_cuenta", "canal": "correo"})
    assert r["delivered"] is True and r["simulated"] is True
    assert r["destino_masked"] == "c···@···.com"


# ── Conversational flow: ask channel → store tipo → elegir_canal feeds tool ─

def test_flow_pide_estado_pregunta_canal_y_guarda_tipo():
    spec = _prestamype_spec()
    session: dict = {}
    out = route_layer1(
        "envíame mi estado de cuenta", spec, SINGLE,
        session_state=session, identity_verified=True,
    )
    assert out.handled is True
    assert "correo o" in out.text.lower()           # Ada pregunta el canal
    assert out.run_tool is None                       # todavía no envía
    # el tipo pendiente quedó guardado en la sesión (data-driven set_session)
    from features.conversation.responses import _get_session
    assert _get_session(session, "tipo") == "estado_cuenta"


def test_flow_elegir_canal_corre_tool_con_tipo_de_sesion():
    spec = _prestamype_spec()
    # sesión ya tiene el tipo pendiente (turno anterior pidió el estado)
    session = {"_responses_session": {"tipo": "estado_cuenta"}}
    out = route_layer1(
        "correo", spec, SINGLE,
        session_state=session, identity_verified=True,
    )
    assert out.handled is True
    assert out.run_tool == "enviar_info"
    # el tool recibe tipo (de la sesión) + canal (capturado este turno)
    assert out.tool_args.get("tipo") == "estado_cuenta"
    assert out.tool_args.get("canal") == "correo"
    assert out.rerender_with_result is True


def test_flow_enviar_estado_requires_identity_gate():
    spec = _prestamype_spec()
    out = route_layer1(
        "envíame mi estado de cuenta", spec, {},
        session_state={}, identity_verified=False,
    )
    assert out.handled is True
    assert out.intent == "identidad_requerida"        # gated → pide DNI
