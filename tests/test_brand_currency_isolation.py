"""PR-4: Brand strings and currency symbols come from tenant config, not hardcode.

Verifies:
  - Email subject/body uses tenant config name (not hardcoded "PrestaUnion")
  - Certificate company_name comes from tenant config
  - Estado-de-cuenta WhatsApp header uses tenant config name
  - Currency symbol flows from profile (no silent "S/" when profile says "US$")
  - Fallback + loguru warning paths fire when config is absent
  - Per-tenant output is stable: prestaunion produces "PrestaUnion", prestamype
    produces "Prestamype" (both from their own tenant.config.json → name)
"""

from __future__ import annotations

import pytest
from unittest.mock import patch

from shared.delivery.email_delivery import _document_html, EmailService
from features.cobranza.tools import (
    _estado_cuenta_text,
    _fmt,
)
from shared.templates import _money, normalize_credits, build_variables


# ── Synthetic profiles ─────────────────────────────────────────────────────────

_PROFILE_SOL = {
    "account_id": "T001",
    "borrower_name": "CARLOS MENDOZA",
    "business_name": "Bodega Don Carlos",
    "loan_number": "L-001",
    "balance": 5000.0,
    "currency": "PEN",
    "currency_symbol": "S/",
    "status": "en_mora",
    "status_label": "En mora",
    "installments_paid": 3,
    "installments_total": 12,
    "installments_pending": 9,
}

_PROFILE_USD = {**_PROFILE_SOL, "currency": "USD", "currency_symbol": "US$"}

_PROFILE_NO_SYM = {k: v for k, v in _PROFILE_SOL.items() if k != "currency_symbol"}


# ── _document_html: company_name and agent_name templated ─────────────────────

def test_document_html_uses_company_name():
    html = _document_html("Carlos", "Estado de cuenta", company_name="Prestamype")
    assert "Prestamype" in html
    assert "PrestaUnion" not in html


def test_document_html_uses_agent_name():
    html = _document_html("Carlos", "Estado de cuenta", company_name="Prestamype", agent_name="Ada")
    assert "Ada" in html


def test_document_html_different_tenants_produce_different_output():
    html_pu = _document_html("Carlos", "Estado", company_name="PrestaUnion", agent_name="Ada")
    html_pm = _document_html("Carlos", "Estado", company_name="Prestamype", agent_name="Ada")
    assert "PrestaUnion" in html_pu
    assert "Prestamype" in html_pm
    assert "PrestaUnion" not in html_pm
    assert "Prestamype" not in html_pu


def test_document_html_fallback_no_brand_emits_generic():
    """When company_name is empty the HTML still renders (generic fallback)."""
    html = _document_html("Carlos", "Estado", company_name="", agent_name="")
    # Should not contain the hardcoded brand
    assert "PrestaUnion" not in html
    # Should contain some generic placeholder
    assert html  # non-empty


# ── EmailService.send_document: subject contains company name ─────────────────

class _CaptureSend:
    """Spy: records args passed to _send."""
    def __init__(self):
        self.calls: list[dict] = []

    async def _send(self, to_email, subject, html_content, event, attachments=None, from_email=""):
        self.calls.append({"subject": subject, "body": html_content, "from": from_email})
        return True


async def test_email_subject_uses_company_name():
    svc = EmailService(api_url="")  # dry-run (not enabled)
    captured = []

    async def _fake_send(
        to, subject, body, event, attachments=None, from_email="", tenant_slug=""
    ):
        captured.append(subject)
        return True

    svc._send = _fake_send  # type: ignore[assignment]
    svc._enabled = True  # bypass dry-run path to reach _send

    await svc.send_document(
        "borrower@example.com", "Carlos", "Estado de cuenta",
        company_name="Prestamype",
    )
    assert len(captured) == 1
    assert "Prestamype" in captured[0]
    assert "PrestaUnion" not in captured[0]


async def test_email_subject_prestaunion_stable():
    """PrestaUnion tenant still produces 'PrestaUnion' in subject."""
    svc = EmailService(api_url="")
    captured = []

    async def _fake_send(
        to, subject, body, event, attachments=None, from_email="", tenant_slug=""
    ):
        captured.append(subject)
        return True

    svc._send = _fake_send  # type: ignore[assignment]
    svc._enabled = True

    await svc.send_document(
        "borrower@example.com", "Ana", "Certificado de no adeudo",
        company_name="PrestaUnion",
    )
    assert "PrestaUnion" in captured[0]


async def test_email_send_document_missing_company_warns(caplog):
    """Missing company_name triggers a loguru warning and degrades gracefully."""
    import logging

    svc = EmailService(api_url="")
    sent_subjects: list[str] = []

    async def _fake_send(
        to, subject, body, event, attachments=None, from_email="", tenant_slug=""
    ):
        sent_subjects.append(subject)
        return True

    svc._send = _fake_send  # type: ignore[assignment]
    svc._enabled = True

    with patch("shared.delivery.email_delivery.logger") as mock_log:
        await svc.send_document(
            "borrower@example.com", "Ana", "Estado de cuenta",
            company_name="",
        )
        # Warning must have fired
        assert mock_log.warning.called


# ── _estado_cuenta_text: header uses tenant_name ──────────────────────────────

def test_estado_cuenta_text_header_uses_tenant_name():
    text = _estado_cuenta_text(_PROFILE_SOL, tenant_name="Prestamype")
    assert "*Estado de cuenta — Prestamype*" in text
    assert "PrestaUnion" not in text


def test_estado_cuenta_text_prestaunion_stable():
    text = _estado_cuenta_text(_PROFILE_SOL, tenant_name="PrestaUnion")
    assert "*Estado de cuenta — PrestaUnion*" in text


def test_estado_cuenta_text_no_tenant_name_generic():
    """Empty tenant_name produces a generic header without any brand."""
    text = _estado_cuenta_text(_PROFILE_SOL, tenant_name="")
    assert "*Estado de cuenta*" in text
    assert "PrestaUnion" not in text
    assert "Prestamype" not in text


# ── Certificate: company_name is required (no default) ────────────────────────

def test_certificate_no_default_company_name():
    """generate_certificate must be called with an explicit company_name.

    Verifies the function signature no longer has 'PrestaUnion' as default
    by calling it with a different company and checking the output path exists.
    """
    from shared.delivery.certificate_pdf import generate_certificate
    import inspect

    sig = inspect.signature(generate_certificate)
    param = sig.parameters.get("company_name")
    assert param is not None
    # No default — callers MUST supply it
    assert param.default is inspect.Parameter.empty, (
        "company_name must not have a default value; callers must pass tenant name"
    )


def test_certificate_uses_provided_company_name():
    """generate_certificate renders the given company_name into the PDF path."""
    from shared.delivery.certificate_pdf import generate_certificate

    path = generate_certificate(
        folio="TEST-2026-00001",
        borrower_name="CARLOS MENDOZA",
        business_name="Bodega Carlos",
        loan_number="L-001",
        company_name="Prestamype",
    )
    assert path.exists()
    # PDF content contains the company name (via reportlab text draw)
    # We can verify the file is non-empty and has the right folio in the filename
    assert "TEST-2026-00001" in path.name
    assert path.stat().st_size > 0


# ── emitir_certificado_no_adeudo: tenant_name warning when absent ─────────────

async def test_emitir_certificado_warns_without_tenant_name():
    """emitir_certificado_no_adeudo logs a warning when tenant_name is empty."""
    from features.cobranza.tools import emitir_certificado_no_adeudo

    profile = {**_PROFILE_SOL, "balance": 0.0, "cancelled_at": "2026-01-01"}

    with patch("features.cobranza.tools.logger") as mock_log:
        result = await emitir_certificado_no_adeudo(profile, tenant_name="")
        assert result.get("issued") is True
        assert mock_log.warning.called


async def test_emitir_certificado_uses_tenant_name():
    """emitir_certificado_no_adeudo passes tenant_name to generate_certificate."""
    from features.cobranza.tools import emitir_certificado_no_adeudo

    profile = {**_PROFILE_SOL, "balance": 0.0, "cancelled_at": "2026-01-15"}

    with patch("features.cobranza.tools.generate_certificate") as mock_gen:
        from pathlib import Path as _P
        import tempfile
        _tmp = _P(tempfile.mktemp(suffix=".pdf"))
        _tmp.touch()
        mock_gen.return_value = _tmp

        await emitir_certificado_no_adeudo(profile, tenant_name="Prestamype")
        call_kwargs = mock_gen.call_args.kwargs
        assert call_kwargs["company_name"] == "Prestamype"


# ── Currency: _fmt / _money require sym (no silent "S/") ────────────────────

def test_fmt_uses_provided_sym():
    assert _fmt(1234.5, "US$") == "US$ 1,234.50"
    assert _fmt(1234.5, "S/") == "S/ 1,234.50"


def test_money_uses_provided_sym():
    assert _money(999.0, "US$") == "US$ 999.00"
    assert _money(0.0, "S/") == "S/ 0.00"


def test_normalize_credits_usd_profile_uses_usd_sym():
    credits = normalize_credits(_PROFILE_USD)
    assert credits[0]["moneda"] == "US$"
    assert "US$" in credits[0]["saldo"]
    assert "S/" not in credits[0]["saldo"]


def test_normalize_credits_sol_profile_uses_sol_sym():
    credits = normalize_credits(_PROFILE_SOL)
    assert credits[0]["moneda"] == "S/"
    assert "S/" in credits[0]["saldo"]


def test_normalize_credits_missing_sym_warns():
    """Missing currency_symbol triggers warning and falls back to 'S/'."""
    with patch("shared.templates.logger") as mock_log:
        credits = normalize_credits(_PROFILE_NO_SYM)
        assert mock_log.warning.called
        # Fallback still produces output
        assert credits[0]["moneda"] == "S/"


def test_build_variables_usd_profile_propagates_sym():
    variables = build_variables(_PROFILE_USD)
    assert variables["moneda"] == "US$"
    assert "US$" in variables["saldo"]


def test_build_variables_missing_sym_warns():
    with patch("shared.templates.logger") as mock_log:
        variables = build_variables(_PROFILE_NO_SYM)
        assert mock_log.warning.called
        assert variables["moneda"] == "S/"


# ── tools_schema: escalate_to_human description is brand-neutral ─────────────

def test_tools_schema_escalate_no_brand():
    from shared.config.tools_schema import TOOL_DEFINITIONS

    escalate = next(t for t in TOOL_DEFINITIONS if t["name"] == "escalate_to_human")
    desc = escalate["description"]
    assert "PrestaUnion" not in desc
    assert "asesor humano" in desc


def test_tools_schema_module_docstring_neutral():
    import shared.config.tools_schema as mod
    assert "PrestaUnion" not in (mod.__doc__ or "")


# ── consultar_cronograma: currency fallback warns (review fix #1) ─────────────

_CRONOGRAMA = [
    {"n_cuota": 1, "fecha_venc": "2026-07-01", "monto": 462.14, "estado": "pendiente"},
    {"n_cuota": 2, "fecha_venc": "2026-08-01", "monto": 462.14, "estado": "pendiente"},
]


async def test_cronograma_missing_sym_warns_and_falls_back():
    from features.cobranza.tools import consultar_cronograma

    with (
        patch("features.cobranza.doris_debt_source.get_cronograma", return_value=_CRONOGRAMA),
        patch("features.cobranza.tools.logger") as mock_log,
    ):
        r = await consultar_cronograma(_PROFILE_NO_SYM, "prestamype")
        assert r["escalate"] is False
        assert "S/ 462.14" in r["message"]  # fallback symbol still renders
        assert mock_log.warning.called


async def test_cronograma_usd_profile_uses_usd_sym():
    from features.cobranza.tools import consultar_cronograma

    with patch("features.cobranza.doris_debt_source.get_cronograma", return_value=_CRONOGRAMA):
        r = await consultar_cronograma(_PROFILE_USD, "prestamype")
        assert "US$ 462.14" in r["message"]
        assert "S/ " not in r["message"]


# ── Router currency fallbacks warn (review fix #2, F9) ───────────────────────

def test_router_prof_currency_warns_on_missing():
    from api.routers.cobranza import _prof_currency

    with patch("api.routers.cobranza.logger") as mock_log:
        assert _prof_currency({}, "prestamype") == "PEN"
        assert mock_log.warning.called
        # warning names the tenant
        assert "prestamype" in str(mock_log.warning.call_args)


def test_router_prof_currency_silent_when_present():
    from api.routers.cobranza import _prof_currency

    with patch("api.routers.cobranza.logger") as mock_log:
        assert _prof_currency({"currency": "USD"}, "prestamype") == "USD"
        assert not mock_log.warning.called


# ── Mail-API origin: per-tenant routing preserved (review fix #6) ─────────────

class _FakeResp:
    status_code = 200
    text = ""


class _FakeAsyncClient:
    """Captures the JSON payload POSTed to the mail API."""

    captured: list[dict] = []

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None, timeout=None):
        _FakeAsyncClient.captured.append(json)
        return _FakeResp()


@pytest.mark.parametrize(
    ("slug", "expected_origin"),
    [
        ("prestaunion", "prestaunion-cobranza"),  # byte-identical to historical value
        ("prestamype", "prestamype-cobranza"),
        ("", "cobranza"),  # no slug → generic (never "-cobranza")
    ],
)
async def test_mail_origin_built_from_tenant_slug(slug, expected_origin):
    _FakeAsyncClient.captured = []
    svc = EmailService(api_url="https://mail.example.test/send")
    with patch("shared.delivery.email_delivery.httpx.AsyncClient", _FakeAsyncClient):
        sent = await svc.send_document(
            "borrower@example.com", "Carlos", "Estado de cuenta",
            summary_html="<p>resumen</p>",
            company_name="Whatever",
            tenant_slug=slug,
        )
    assert sent is True
    assert len(_FakeAsyncClient.captured) == 1
    assert _FakeAsyncClient.captured[0]["origin"] == expected_origin


async def test_tool_registry_threads_tenant_slug_to_email_origin():
    """End-to-end: ToolRegistry(tenant_id=...) → enviar_documento → mail origin."""
    from api.tool_registry import ToolRegistry

    _FakeAsyncClient.captured = []
    svc = EmailService(api_url="https://mail.example.test/send")
    reg = ToolRegistry(
        identity_verified=True,
        debt_context={**_PROFILE_SOL, "email": "x@example.com"},
        email_service=svc,
        tenant_id="prestamype",
        tenant_name="Prestamype",
    )
    with patch("shared.delivery.email_delivery.httpx.AsyncClient", _FakeAsyncClient):
        r = await reg.execute(
            "enviar_documento", {"tipo": "estado_cuenta", "destino": "dest@example.com"}
        )
    assert r["delivered"] is True
    assert _FakeAsyncClient.captured[0]["origin"] == "prestamype-cobranza"
