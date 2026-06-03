"""WhatsApp "subir pago" flow — both paths (tenant prestamype, chathub channel).

Camino A (typed): the LLM agent gathers the voucher fields and calls
``validar_comprobante`` — exercised here through the REAL chathub engine runner
with a faked LLM provider (no network / no Anthropic key).

Camino B (photo): the debtor sends the voucher IMAGE (``body.url``). With no
OCR we don't validate — when the debtor is identified we acknowledge honestly
(EN REVISIÓN) and register the image for manual reconciliation; without identity
we ask for the DNI and do NOT acknowledge a payment.

These tests mock the engine where the LLM/Doris would be hit:
  · the adapter propagation test mocks the engine runner entirely;
  · the runner tests fake ``build_llm_provider`` and isolate the comprobantes
    dedup store at a temp path, and resolve identity via the seeded fixture token
    (``CT-demo-1`` → P02137, Luis).
"""

from __future__ import annotations

import json

import pytest

from shared.llm import LLMProvider, LLMResponse, ToolCall
from integrations.chathub_adapter import ChathubChatAdapter, ChathubChatRequest


# ── (a) body.url is propagated to the engine runner ──────────────────────────


@pytest.mark.asyncio
async def test_adapter_propagates_media_url_to_engine():
    captured = {}

    async def fake_engine(**kwargs):
        captured.update(kwargs)
        return {"content": "ok", "tool_pairs": [], "ui_actions": {}}

    adapter = ChathubChatAdapter(engine_runner=fake_engine)
    body = ChathubChatRequest(
        channel_id="ch1",
        message="",
        unique_id="uid-img",
        chathub_conversation_id="conv-img",
        chathub_project_id="proj-x",
        url="https://chathub.example/media/voucher-123.jpg",
    )
    await adapter.handle(body=body, tenant_id="prestamype", tenant_cfg={})

    assert captured["media_url"] == "https://chathub.example/media/voucher-123.jpg"


@pytest.mark.asyncio
async def test_adapter_media_url_none_when_absent():
    captured = {}

    async def fake_engine(**kwargs):
        captured.update(kwargs)
        return {"content": "ok", "tool_pairs": [], "ui_actions": {}}

    adapter = ChathubChatAdapter(engine_runner=fake_engine)
    body = ChathubChatRequest(message="hola", unique_id="u", chathub_conversation_id="c")
    await adapter.handle(body=body, tenant_id="prestamype", tenant_cfg={})

    assert captured["media_url"] is None


# ── Runner harness ───────────────────────────────────────────────────────────


class _NoToolProvider(LLMProvider):
    """Never asked to run a tool — proves the photo path bypasses the LLM."""

    def __init__(self):
        self.calls = 0

    @property
    def model(self):  # pragma: no cover - trivial
        return "fake"

    @property
    def name(self):  # pragma: no cover - trivial
        return "fake"

    async def complete(self, *, system, messages, tools, model=None, max_tokens=1024, force_tool=None):
        self.calls += 1
        if not tools:
            return LLMResponse(text="ninguna", tool_calls=[])
        return LLMResponse(text="respuesta libre", tool_calls=[])


class _ComprobanteProvider(LLMProvider):
    """Fake LLM for camino A. The cheap intent-classifier call (tools=[]) is
    answered with 'ninguna' so the turn falls through to the agent loop; the
    first agent call (tools present) requests ``validar_comprobante``, and the
    follow-up answers with the confirmation text."""

    def __init__(self):
        self.calls = 0  # agent (tool-capable) calls only

    @property
    def model(self):  # pragma: no cover - trivial
        return "fake"

    @property
    def name(self):  # pragma: no cover - trivial
        return "fake"

    async def complete(self, *, system, messages, tools, model=None, max_tokens=1024, force_tool=None):
        if not tools:
            # Intent classifier (capa 2) — no canned intent matches → free agent.
            return LLMResponse(text="ninguna", tool_calls=[])
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                text="",
                tool_calls=[ToolCall(
                    id="vc1",
                    name="validar_comprobante",
                    input={
                        "monto": 462.14,
                        "nro_operacion": "OP-WA-001",
                        "cuenta_destino": "00389801338381007048",
                        "account_type": "cci",
                    },
                )],
            )
        return LLMResponse(text="Recibí tu comprobante, quedó en revisión.", tool_calls=[])


@pytest.fixture
def runner_env(monkeypatch, tmp_path):
    """Wire the chathub runner for an isolated, network-free turn.

    - fresh in-memory engine store
    - comprobantes dedup store at a temp path
    - returns a setter for the faked LLM provider
    """
    import api.main as m
    import shared.llm as llm
    import tools.cobranza as cobranza

    m.store = m.get_store()
    monkeypatch.setattr(cobranza, "_COMPROBANTES_PATH", tmp_path / "comprobantes.json")

    def set_provider(provider):
        monkeypatch.setattr(llm, "build_llm_provider", lambda *a, **k: provider)

    return set_provider, cobranza


# ── (b) photo from an IDENTIFIED debtor → acuse + registro (no validation) ────


@pytest.mark.asyncio
async def test_photo_identified_acks_and_registers(runner_env):
    set_provider, cobranza = runner_env
    provider = _NoToolProvider()
    set_provider(provider)

    from api.chathub import _run_chathub_engine_turn

    result = await _run_chathub_engine_turn(
        text="",
        tenant_id="prestamype",
        conversation_id="chathub-prestamype-photo-1",
        campaign_token="CT-demo-1",  # → P02137 (Luis) via fixture fallback
        channel="whatsapp",
        chathub_conversation_id="photo-1",
        chathub_project_id="p",
        channel_id="c",
        media_url="https://chathub.example/voucher-abc.jpg",
    )

    # Honest acuse: RECIBIDO + EN REVISIÓN, never "validado".
    content = result["content"].lower()
    assert "revisión" in content or "revision" in content
    assert "validado" not in content
    # No LLM was consulted for the photo path.
    assert provider.calls == 0
    # The image was registered for manual reconciliation.
    items = json.loads(cobranza._COMPROBANTES_PATH.read_text(encoding="utf-8"))
    assert len(items) == 1
    rec = items[0]
    assert rec["credito"] == "P02137"
    assert rec["media_url"] == "https://chathub.example/voucher-abc.jpg"
    assert rec["source"] == "foto"
    assert rec["estado"] == "en_revision"
    assert rec["monto"] is None  # no OCR → no amount


# ── (c) photo WITHOUT identity → ask DNI, do NOT ack/register ─────────────────


@pytest.mark.asyncio
async def test_photo_without_identity_asks_dni(runner_env):
    set_provider, cobranza = runner_env
    provider = _NoToolProvider()
    set_provider(provider)

    from api.chathub import _run_chathub_engine_turn

    result = await _run_chathub_engine_turn(
        text="",
        tenant_id="prestamype",
        conversation_id="chathub-prestamype-photo-anon",
        campaign_token=None,  # no token → not identified
        channel="whatsapp",
        chathub_conversation_id="photo-anon",
        chathub_project_id="p",
        channel_id="c",
        media_url="https://chathub.example/voucher-anon.jpg",
    )

    content = result["content"].lower()
    assert "dni" in content
    assert "revisión" not in content and "revision" not in content
    # Nothing registered without identity.
    assert not cobranza._COMPROBANTES_PATH.exists()
    assert provider.calls == 0


# ── (d) typed path can invoke validar_comprobante (camino A) ──────────────────


@pytest.mark.asyncio
async def test_typed_path_invokes_validar_comprobante(runner_env):
    set_provider, cobranza = runner_env
    provider = _ComprobanteProvider()
    set_provider(provider)

    from api.chathub import _run_chathub_engine_turn

    result = await _run_chathub_engine_turn(
        text="Ya hice mi transferencia, te paso los datos del voucher",
        tenant_id="prestamype",
        conversation_id="chathub-prestamype-typed-1",
        campaign_token="CT-demo-1",  # identified
        channel="whatsapp",
        chathub_conversation_id="typed-1",
        chathub_project_id="p",
        channel_id="c",
        media_url=None,
    )

    tools_called = [name for name, _ in result.get("tool_pairs", [])]
    assert "validar_comprobante" in tools_called
    # The tool actually registered the voucher (typed path, with monto).
    items = json.loads(cobranza._COMPROBANTES_PATH.read_text(encoding="utf-8"))
    assert len(items) == 1
    assert items[0]["credito"] == "P02137"
    assert items[0]["nro_operacion"] == "OP-WA-001"
    assert items[0]["monto"] == 462.14


# ── (e) photo + text → text wins (camino A), image attached to same report ────


@pytest.mark.asyncio
async def test_photo_plus_text_prioritizes_text_and_attaches_image(runner_env):
    set_provider, cobranza = runner_env
    provider = _ComprobanteProvider()
    set_provider(provider)

    from api.chathub import _run_chathub_engine_turn

    result = await _run_chathub_engine_turn(
        text="Ya hice mi transferencia, te paso los datos del voucher",
        tenant_id="prestamype",
        conversation_id="chathub-prestamype-mixed-1",
        campaign_token="CT-demo-1",
        channel="whatsapp",
        chathub_conversation_id="mixed-1",
        chathub_project_id="p",
        channel_id="c",
        media_url="https://chathub.example/voucher-mixed.jpg",
    )

    # Text path ran (LLM was consulted, tool invoked).
    tools_called = [name for name, _ in result.get("tool_pairs", [])]
    assert "validar_comprobante" in tools_called
    # Both the photo record AND the typed voucher are present.
    items = json.loads(cobranza._COMPROBANTES_PATH.read_text(encoding="utf-8"))
    sources = sorted(str(i.get("source") or "typed") for i in items)
    assert "foto" in sources
    # The typed voucher (from the forced tool-call) is also present.
    assert any(i.get("nro_operacion") == "OP-WA-001" for i in items)


# ── (f) tool: registrar_comprobante_foto dedups by media_url ──────────────────


def test_registrar_comprobante_foto_dedups(runner_env):
    _set, cobranza = runner_env
    from tools.cobranza import registrar_comprobante_foto

    profile = {"account_id": "P02137", "dni": "44218903"}
    first = registrar_comprobante_foto(profile, "https://x/v.jpg")
    second = registrar_comprobante_foto(profile, "https://x/v.jpg")

    assert first["registered"] is True
    assert first["duplicate"] is False
    assert second["registered"] is False
    assert second["duplicate"] is True
    items = json.loads(cobranza._COMPROBANTES_PATH.read_text(encoding="utf-8"))
    assert len(items) == 1
