"""Tests for the curated-responses engine (tenant-agnostic, hybrid router).

Covers the CORE feature (engine logic) plus the prestamype DATA that exercises
it: responses.json format, single/list/grupal render, variant no-repeat, the
2-layer router (keyword resolves with NO LLM, hybrid classification, fallback),
multi-deuda (Lucía → 2 credits), grupal (Rosa → codeudores) and desambiguación.

The engine is generic: nothing here asserts an if-tenant branch — it asserts the
engine reads the tenant's responses.json + the verified profile.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from features.conversation import responses as R
from tenancy.responses_spec import ResponsesSpec
from tenancy.tenant_loader import TenantConfig
from features.cobranza import debt_source

TENANT = "prestamype"
LUIS = "44218903"    # P02137, al día, single credit
LUCIA = "76310582"   # P05012, multi-deuda (2 créditos)
ROSA = "40517264"    # P05480, grupal (2 codeudores)


def _tenant_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "tenants" / TENANT


def _spec() -> ResponsesSpec:
    return ResponsesSpec.from_dir(_tenant_dir(), response_mode="hybrid")


def _profile(dni: str) -> dict:
    return debt_source.resolve_dni(dni, tenant_id=TENANT)


# ── responses.json format + spec loading ─────────────────────────────────────

def test_responses_json_is_valid_and_has_required_intents():
    data = json.loads((_tenant_dir() / "responses.json").read_text(encoding="utf-8"))
    required = {
        "saludo", "no_entendido", "despedida", "consulta_deuda",
        "politica_pago", "donde_pagar", "elegir_credito",
        "comprobante_resultado", "derivar_asesor",
    }
    assert required <= set(data)
    for intent, cfg in data.items():
        if intent.startswith("_"):
            continue
        assert cfg.get("mode") in ("verbatim", "variant")


def test_spec_enabled_only_in_scripted_or_hybrid():
    assert _spec().enabled is True
    llm_spec = ResponsesSpec.from_dir(_tenant_dir(), response_mode="llm")
    assert llm_spec.enabled is False  # data present but mode=llm → off


def test_missing_responses_json_degrades_to_llm():
    # prestaunion ships no responses.json → empty spec, mode llm, nothing breaks.
    spec = ResponsesSpec.from_dir(
        Path(__file__).resolve().parent.parent / "tenants" / "prestaunion",
        response_mode="llm",
    )
    assert spec.intents == {}
    assert spec.enabled is False


# ── response_mode flag wiring (tenant_loader) ─────────────────────────────────

def test_prestamype_loads_hybrid_mode_and_spec():
    cfg = TenantConfig.from_directory(_tenant_dir())
    assert cfg.response_mode == "hybrid"
    assert cfg.responses.enabled is True


def test_prestaunion_stays_llm_intact():
    cfg = TenantConfig.from_directory(
        Path(__file__).resolve().parent.parent / "tenants" / "prestaunion"
    )
    assert cfg.response_mode == "llm"
    assert cfg.responses.enabled is False


# ── single template render (real data) ───────────────────────────────────────

def test_render_single_consulta_deuda_with_real_data():
    spec = _spec()
    prof = _profile(LUIS)
    res = R.render_intent(spec, "consulta_deuda", prof, source=R.SOURCE_KEYWORD)
    assert res is not None
    assert "P02137" in res.text
    assert "S/ 18,420.00" in res.text       # saldo real
    assert "S/ 462.14" in res.text          # cuota real
    assert "2026-06-18" in res.text         # fecha de vencimiento real


# ── list template render (multi-deuda: Lucía → 2 créditos) ───────────────────

def test_render_list_multideuda_lists_two_credits():
    spec = _spec()
    prof = _profile(LUCIA)
    res = R.render_intent(spec, "consulta_deuda", prof, source=R.SOURCE_KEYWORD)
    assert res is not None
    assert "2 créditos" in res.text
    assert "P05012" in res.text and "P05119" in res.text   # ambos créditos
    assert "S/ 9,120.50" in res.text and "S/ 26,340.00" in res.text  # saldos reales
    assert "S/ 35,460.50" in res.text                       # total = suma de saldos


# ── grupal block render (Rosa → codeudores) ──────────────────────────────────

def test_render_grupal_lists_codeudores_masked():
    spec = _spec()
    prof = _profile(ROSA)
    res = R.render_intent(spec, "consulta_deuda", prof, source=R.SOURCE_KEYWORD)
    assert res is not None
    assert "grupal" in res.text.lower()
    assert "Miguel Angel Paredes Quispe" in res.text
    assert "Elena Sofia Quispe Mamani" in res.text
    # codeudor DNIs masked (never full)
    assert "40517265" not in res.text
    assert "*" in res.text


# ── variant selection (no immediate repeat) ──────────────────────────────────

def test_variant_picker_avoids_last_index():
    variants = ["a", "b", "c"]
    for _ in range(20):
        _, idx = R.pick_variant(variants, last_index=1)
        assert idx != 1


def test_variant_single_element_returns_it():
    chosen, idx = R.pick_variant(["solo"], last_index=None)
    assert chosen == "solo" and idx == 0


def test_variant_intent_persists_index_in_session_state():
    spec = _spec()
    prof = _profile(LUIS)
    session: dict = {}
    out = R.route_layer1("hola", spec, prof, session_state=session)
    assert out.handled is True
    assert out.source == R.SOURCE_KEYWORD
    # the chosen variant index is remembered for no-repeat next turn
    assert session.get("_responses_variant_idx", {}).get("saludo") == out.variant_index


# ── Layer 1 router: resolves frequent intents with ZERO LLM ───────────────────

@pytest.mark.parametrize(
    "text,intent",
    [
        ("Hola buenas tardes", "saludo"),
        ("cuánto debo?", "consulta_deuda"),
        ("¿a qué cuenta pago?", "donde_pagar"),
        ("muchas gracias", "despedida"),
        ("quiero hablar con un asesor", "derivar_asesor"),
        ("se puede refinanciar?", "politica_pago"),
    ],
)
def test_layer1_keyword_resolves_without_llm(text, intent):
    spec = _spec()
    prof = _profile(LUIS)
    # identity verified so gated intents (consulta_deuda, donde_pagar) resolve.
    out = R.route_layer1(text, spec, prof, session_state={}, identity_verified=True)
    assert out.handled is True
    assert out.intent == intent
    assert out.source == R.SOURCE_KEYWORD     # resolved with NO LLM call
    assert out.text


def test_layer1_regex_pattern_resolves():
    spec = _spec()
    prof = _profile(LUIS)
    out = R.route_layer1("oye, ¿cuánto pago este mes?", spec, prof,
                         session_state={}, identity_verified=True)
    assert out.handled is True
    assert out.intent == "consulta_deuda"     # matched via patterns regex
    assert out.source == R.SOURCE_KEYWORD


def test_requires_identity_gate_blocks_unverified():
    spec = _spec()
    # consulta_deuda requires identity → unverified user gets the gate prompt.
    out = R.route_layer1("cuánto debo", spec, {}, session_state={}, identity_verified=False)
    assert out.handled is True
    assert out.intent == "identidad_requerida"
    assert "dni" in out.text.lower()


def test_requires_identity_gate_passes_when_verified():
    spec = _spec()
    out = R.route_layer1("cuánto debo", spec, _profile(LUIS),
                         session_state={}, identity_verified=True)
    assert out.intent == "consulta_deuda"
    assert out.run_tool == "consultar_deuda"   # data-driven intent→tool


def test_classifier_menu_is_data_driven_from_json():
    spec = _spec()
    menu = R.classifier_menu(spec)
    # every classifiable intent contributes a {name: description}; nothing hard-coded.
    assert "consulta_deuda" in menu
    assert menu["consulta_deuda"]   # has a description string
    # The menu is the classifiable subset of the spec's intents.
    classifiable = {i for i, c in spec.intents.items() if c.get("classifiable", True)}
    assert set(menu) == classifiable


def test_comprobante_resultado_not_in_classifier_menu():
    """Regression (deterministic, no LLM): a payment-report turn must reach the
    tool-loop (validar_comprobante), not be hijacked by the acuse intent.

    ``comprobante_resultado`` is the voucher ACUSE — its template is rendered by
    the photo path / tool result, never picked by the LLM classifier for a fresh
    user message. With ``classifiable: false`` it must be ABSENT from the menu;
    otherwise (via the ``description or intent`` fallback) it re-enters the menu
    under its own name and confirms a payment in false without registering it.
    """
    spec = _spec()
    menu = R.classifier_menu(spec)
    assert "comprobante_resultado" not in menu
    # But the intent still EXISTS in the spec (its template is used elsewhere).
    assert "comprobante_resultado" in spec.intents


def test_comprobante_reportar_is_classifiable_and_passes_through_to_llm():
    """Camino A routing (deterministic, no LLM): the 'report a payment' intent is
    in the classifier menu, but it has NO template on purpose — when the verified
    user is classified into it, the router renders empty and HANDS OFF to the LLM
    agent (handled=False), which gathers fields and runs validar_comprobante."""
    spec = _spec()
    menu = R.classifier_menu(spec)
    assert "comprobante_reportar" in menu
    # Verified user → no canned text → fall through to the agent tool-loop.
    out = R.resolve_classified_intent(
        "comprobante_reportar", spec, _profile(LUIS),
        session_state={}, identity_verified=True,
    )
    assert out.handled is False
    assert out.source == R.SOURCE_LLM


def test_comprobante_reportar_unverified_asks_dni():
    """An unverified user reporting a payment is gated to DNI first (data-driven
    requires_identity), never a false acuse."""
    spec = _spec()
    out = R.resolve_classified_intent(
        "comprobante_reportar", spec, {},
        session_state={}, identity_verified=False,
    )
    assert out.handled is True
    assert out.intent == "identidad_requerida"


def test_classifiable_flag_defaults_true_and_can_opt_out():
    """``classifiable`` is data-driven: absent → in menu; false → excluded."""
    spec = ResponsesSpec(
        response_mode="hybrid",
        intents={
            "a": {"description": "intent a", "template": "A"},
            "b": {"description": "intent b", "template": "B", "classifiable": False},
            "c": {"template": "C"},  # no description, classifiable by default
        },
    )
    menu = R.classifier_menu(spec)
    assert "a" in menu
    assert "b" not in menu
    assert menu["c"] == "c"  # falls back to the intent name


def test_intent_tool_and_requires_identity_read_from_json():
    spec = _spec()
    assert R.intent_tool(spec, "consulta_deuda") == "consultar_deuda"
    assert R.intent_tool(spec, "saludo") is None
    assert R.intent_requires_identity(spec, "consulta_deuda") is True
    assert R.intent_requires_identity(spec, "saludo") is False


def test_layer1_miss_in_hybrid_requests_classification():
    spec = _spec()
    prof = _profile(LUIS)
    out = R.route_layer1("xyzzy mensaje sin keyword", spec, prof, session_state={})
    assert out.handled is False
    assert out.needs_llm_classification is True


def test_scripted_mode_falls_back_to_no_entendido_without_llm():
    spec = ResponsesSpec.from_dir(_tenant_dir(), response_mode="scripted")
    prof = _profile(LUIS)
    out = R.route_layer1("xyzzy sin keyword", spec, prof, session_state={})
    assert out.handled is True
    assert out.intent == "no_entendido"
    assert out.source == R.SOURCE_KEYWORD     # minimal LLM: canned fallback


# ── Layer 2 resolution: classified intent → canned ───────────────────────────

def test_layer2_classified_intent_renders_canned():
    spec = _spec()
    prof = _profile(LUIS)
    out = R.resolve_classified_intent("donde_pagar", spec, prof,
                                      session_state={}, identity_verified=True)
    assert out.handled is True
    assert out.source == R.SOURCE_INTENT
    assert prof["cci"] in out.text and prof["banco"] in out.text


def test_layer2_unknown_intent_falls_through_to_llm():
    spec = _spec()
    out = R.resolve_classified_intent("no_existe_este_intent", spec, {}, session_state={})
    assert out.handled is False
    assert out.source == R.SOURCE_LLM


# ── desambiguación: elegir_credito lists numbered credits ────────────────────

def test_elegir_credito_numbers_the_credits():
    spec = _spec()
    prof = _profile(LUCIA)  # 2 créditos
    res = R.render_intent(spec, "elegir_credito", prof, source=R.SOURCE_INTENT)
    assert res is not None
    assert "1." in res.text and "2." in res.text
    assert "P05012" in res.text and "P05119" in res.text


# ── profile normalization (generic) ──────────────────────────────────────────

def test_normalize_credits_single_vs_multi():
    assert len(R.normalize_credits(_profile(LUIS))) == 1
    assert len(R.normalize_credits(_profile(LUCIA))) == 2


def test_build_variables_fills_from_profile():
    v = R.build_variables(_profile(LUIS))
    assert v["nombre"] == "Carlos"
    assert v["saldo"] == "S/ 18,420.00"
    assert v["cci"] == "00389801338381007048"


# ── agent integration: canned short-circuits the LLM ─────────────────────────

from features.conversation.agent import SoreliaAgent  # noqa: E402
from shared.llm import LLMProvider, LLMResponse, ToolCall  # noqa: E402


class _CountingProvider(LLMProvider):
    """Counts complete() calls so we can prove keyword hits skip the LLM."""

    model = "claude-haiku-test"
    name = "anthropic"

    def __init__(self, classify_as: str | None = None):
        self.calls = 0
        self._classify_as = classify_as

    async def complete(self, *, system, messages, tools, model=None, max_tokens=1024, force_tool=None):
        self.calls += 1
        # When used as the intent classifier, echo the configured label.
        if "clasificador" in (system or "").lower() and self._classify_as:
            return LLMResponse(text=self._classify_as, tool_calls=[])
        return LLMResponse(text="respuesta generada por LLM", tool_calls=[])


def _agent(provider, *, identity_dni: str | None = None):
    from shared.tool_registry import ToolRegistry

    debt_ctx = _profile(identity_dni) if identity_dni else {}
    reg = ToolRegistry(
        identity_verified=bool(identity_dni),
        debt_context=debt_ctx,
        tenant_id=TENANT,
    )
    return SoreliaAgent(
        provider=provider,
        tool_registry=reg,
        tenant=TenantConfig.from_directory(_tenant_dir()),
    )


async def test_agent_keyword_saludo_resolves_with_zero_llm_calls():
    provider = _CountingProvider()
    agent = _agent(provider, identity_dni=LUIS)
    res = await agent.process_message(
        text="hola buenas",
        conversation_id="c1",
        history=[],
        debtor_state={},
        page_context={},
        session_state={},
    )
    assert provider.calls == 0                          # NO LLM call at all
    assert res["response_source"] == R.SOURCE_KEYWORD
    assert res["usage"]["input_tokens"] == 0


async def test_agent_consulta_deuda_keyword_uses_real_data_no_llm():
    provider = _CountingProvider()
    agent = _agent(provider, identity_dni=LUCIA)         # multi-deuda
    res = await agent.process_message(
        text="cuánto debo", conversation_id="c2", history=[],
        debtor_state={}, page_context={}, session_state={},
    )
    assert provider.calls == 0
    assert "P05012" in res["content"] and "P05119" in res["content"]
    # data-driven intent→tool: consultar_deuda ran and is surfaced for UI/analytics
    assert res["tools_called"] == ["consultar_deuda"]


async def test_agent_gated_intent_without_identity_asks_for_dni_no_llm():
    provider = _CountingProvider()
    agent = _agent(provider, identity_dni=None)          # unverified
    res = await agent.process_message(
        text="cuánto debo", conversation_id="c5", history=[],
        debtor_state={}, page_context={}, session_state={},
    )
    assert provider.calls == 0
    assert res["metadata"]["intent"] == "identidad_requerida"
    assert "dni" in res["content"].lower()
    assert res["tools_called"] == []                     # gated: tool never ran


async def test_agent_hybrid_miss_classifies_then_canned():
    # Layer1 misses → ONE cheap classification call → canned (Layer 2).
    provider = _CountingProvider(classify_as="donde_pagar")
    agent = _agent(provider, identity_dni=LUIS)
    res = await agent.process_message(
        text="necesito los datos para abonar", conversation_id="c3", history=[],
        debtor_state={}, page_context={}, session_state={},
    )
    assert provider.calls == 1                          # only the classifier
    assert res["response_source"] == R.SOURCE_INTENT
    assert _profile(LUIS)["cci"] in res["content"]


async def test_agent_hybrid_no_canned_falls_through_to_llm():
    # Classifier returns 'ninguna' → no canned → full agent loop generates.
    provider = _CountingProvider(classify_as="ninguna")
    agent = _agent(provider, identity_dni=LUIS)
    res = await agent.process_message(
        text="cuéntame un chiste sobre finanzas", conversation_id="c4", history=[],
        debtor_state={}, page_context={}, session_state={},
    )
    assert res["response_source"] == R.SOURCE_LLM
    assert provider.calls >= 2                          # classify + generate


# ── Sticky LLM flow: router bypass while gathering tool data ─────────────────
# Regression: in the middle of reporting a payment, "es un CCI" hit the keyword
# "cci" → layer-1 hijacked the turn to donde_pagar → validar_comprobante never
# ran. With an active llm_flow the router is bypassed deterministically.


class _ToolProvider(LLMProvider):
    """First agent call → validar_comprobante; follow-up → text. Answers the
    classifier (system mentions 'clasificador') with the configured label."""

    model = "claude-haiku-test"
    name = "anthropic"

    def __init__(self, classify_as: str | None = None):
        self.calls = 0
        self.agent_calls = 0
        self._classify_as = classify_as

    async def complete(self, *, system, messages, tools, model=None, max_tokens=1024, force_tool=None):
        self.calls += 1
        if "clasificador" in (system or "").lower():
            return LLMResponse(text=self._classify_as or "ninguna", tool_calls=[])
        self.agent_calls += 1
        if self.agent_calls == 1:
            return LLMResponse(text="", tool_calls=[ToolCall(
                id="vc", name="validar_comprobante",
                input={"monto": 462.14, "nro_operacion": "OP-STICKY",
                       "cuenta_destino": "00389801338381007048", "account_type": "cci"},
            )])
        return LLMResponse(text="Tu comprobante quedó en revisión.", tool_calls=[])


async def test_sticky_flow_bypasses_router_for_cci_keyword(monkeypatch, tmp_path):
    # An active llm_flow must bypass layer-1 even for "es un cci" (which normally
    # matches donde_pagar) → the turn reaches the LLM, NOT a canned reply.
    import features.comprobantes.validator as _validator
    monkeypatch.setattr(_validator, "_COMPROBANTES_PATH", tmp_path / "c.json")
    provider = _ToolProvider()
    agent = _agent(provider, identity_dni=LUIS)
    ss = {"llm_flow": {"intent": "comprobante_reportar", "turns": 1}}
    res = await agent.process_message(
        text="es un cci, la cuenta 00389801338381007048",
        conversation_id="sf1", history=[], debtor_state={}, page_context={},
        session_state=ss,
    )
    # NOT canned donde_pagar → the LLM handled it (and ran the tool).
    assert res["response_source"] == R.SOURCE_LLM
    assert "validar_comprobante" in res["tools_called"]
    # Tool succeeded → flag cleared.
    assert "llm_flow" not in ss


async def test_sticky_flow_cap_releases_after_max_turns():
    # Past the cap, the flag is released and normal routing resumes (the same
    # "es un cci" text then hits donde_pagar via layer-1).
    provider = _CountingProvider()
    agent = _agent(provider, identity_dni=LUIS)
    ss = {"llm_flow": {"intent": "comprobante_reportar", "turns": 6}}  # next inc → 7 > cap
    res = await agent.process_message(
        text="es un cci", conversation_id="sf2", history=[],
        debtor_state={}, page_context={}, session_state=ss,
    )
    assert "llm_flow" not in ss                  # released by the cap
    assert res["response_source"] == R.SOURCE_KEYWORD   # routed to donde_pagar
    assert res["metadata"]["intent"] == "donde_pagar"


async def test_sticky_flow_armed_when_flow_intent_classified():
    # Classifying into comprobante_reportar (flow:true, identified, no template)
    # falls through to the LLM AND arms the sticky flag for next turns.
    provider = _CountingProvider(classify_as="comprobante_reportar")
    agent = _agent(provider, identity_dni=LUIS)
    ss: dict = {}
    res = await agent.process_message(
        text="ya hice mi transferencia, te paso los datos",
        conversation_id="sf3", history=[], debtor_state={}, page_context={},
        session_state=ss,
    )
    assert res["response_source"] == R.SOURCE_LLM
    assert ss.get("llm_flow", {}).get("intent") == "comprobante_reportar"


async def test_sticky_flow_not_cleared_when_llm_only_converses():
    # If the armed flow turn produces NO tool (LLM just asks for more data), the
    # flag stays armed so the next turn is still bypassed.
    provider = _CountingProvider()  # never returns a tool
    agent = _agent(provider, identity_dni=LUIS)
    ss = {"llm_flow": {"intent": "comprobante_reportar", "turns": 1}}
    res = await agent.process_message(
        text="es un cci", conversation_id="sf4", history=[],
        debtor_state={}, page_context={}, session_state=ss,
    )
    assert res["response_source"] == R.SOURCE_LLM
    assert ss.get("llm_flow", {}).get("intent") == "comprobante_reportar"
    assert ss["llm_flow"]["turns"] == 2          # incremented, still armed


# ── identificar: typed DNI opens the gate (the bug this fix addresses) ────────

def test_identificar_intent_is_an_identity_opener():
    spec = _spec()
    # data-driven: requires_identity=false + capture + tool → it can open the gate
    assert "identificar" in R.identity_opening_intents(spec)
    assert R.intent_requires_identity(spec, "identificar") is False
    assert R.intent_tool(spec, "identificar") == "identificar_cliente"


def test_typed_dni_routes_to_identificar_not_gate_when_unverified():
    spec = _spec()
    # Unverified user types a bare DNI → must hit `identificar` (capture the DNI),
    # NOT fall into the identidad_requerida gate.
    out = R.route_layer1("76310582", spec, {}, session_state={}, identity_verified=False)
    assert out.handled is True
    assert out.intent == "identificar"
    assert out.run_tool == "identificar_cliente"
    assert out.tool_args == {"dni": "76310582"}
    assert out.source == R.SOURCE_KEYWORD               # zero LLM


def test_typed_dni_prioritized_over_gated_intent_when_unverified():
    spec = _spec()
    # A message that carries BOTH a DNI and gated-intent keywords ("cuánto debo"):
    # while unverified, identification must win so the gate actually opens.
    out = R.route_layer1(
        "cuánto debo, mi dni es 44218903", spec, {},
        session_state={}, identity_verified=False,
    )
    assert out.intent == "identificar"
    assert out.tool_args == {"dni": "44218903"}
    assert out.intent != "identidad_requerida"


def test_typed_dni_capture_extracts_value_via_named_group():
    spec = _spec()
    match = R.match_keyword_intent("mi documento 40517264", spec,
                                   only_intents={"identificar"})
    assert match is not None
    intent, captured = match
    assert intent == "identificar"
    assert captured == "40517264"


@pytest.mark.parametrize(
    "dni,first_name,must_contain",
    [
        (LUIS, "Carlos", ["P02137"]),                       # single credit
        (LUCIA, "Lucia", ["P05012", "P05119", "2 créditos"]),  # multi-credit list
        (ROSA, "Rosa", ["grupal"]),                         # grupal w/ codeudores
    ],
)
async def test_agent_typed_dni_identifies_then_allows_consulta(dni, first_name, must_contain):
    provider = _CountingProvider()
    agent = _agent(provider, identity_dni=None)             # starts UNVERIFIED
    session: dict = {}

    # Turn 1: user types the DNI → identifies with ZERO LLM, gate opens.
    r1 = await agent.process_message(
        text=dni, conversation_id="id1", history=[],
        debtor_state={}, page_context={}, session_state=session,
    )
    assert provider.calls == 0
    assert r1["metadata"]["intent"] == "identificar"
    assert r1["tools_called"] == ["identificar_cliente"]
    assert first_name in r1["content"]                      # confirmation w/ real name
    assert agent.tool_registry._identity_verified is True   # gate now open

    # Turn 2: same session asks for the debt → gated intent now passes the gate.
    r2 = await agent.process_message(
        text="cuánto debo", conversation_id="id1", history=[],
        debtor_state={}, page_context={}, session_state=session,
    )
    assert provider.calls == 0
    assert r2["metadata"]["intent"] == "consulta_deuda"
    for token in must_contain:
        assert token in r2["content"]


async def test_agent_typed_unknown_dni_returns_canned_not_found_no_500():
    provider = _CountingProvider()
    agent = _agent(provider, identity_dni=None)
    res = await agent.process_message(
        text="99999999", conversation_id="idx", history=[],
        debtor_state={}, page_context={}, session_state={},
    )
    assert provider.calls == 0
    assert res["metadata"]["intent"] == "identificar"
    assert "no encontré" in res["content"].lower()
    assert agent.tool_registry._identity_verified is False  # gate stays closed


async def test_token_identity_flow_still_works_with_verified_profile():
    # The pre-existing token path: identity already verified (as chathub sets it
    # from ?ct=demo-N) → consulta_deuda passes the gate, no DNI typed.
    provider = _CountingProvider()
    agent = _agent(provider, identity_dni=LUIS)             # verified upfront
    res = await agent.process_message(
        text="cuánto debo", conversation_id="tok1", history=[],
        debtor_state={}, page_context={}, session_state={},
    )
    assert provider.calls == 0
    assert res["metadata"]["intent"] == "consulta_deuda"
    assert "P02137" in res["content"]


def test_tenant_without_identificar_intent_unchanged():
    # A tenant whose spec declares no identity-opener keeps the old behavior:
    # a gated intent while unverified → identidad_requerida (no priority pass).
    import copy
    spec = _spec()
    stripped = ResponsesSpec(
        intents={k: v for k, v in copy.deepcopy(spec.intents).items() if k != "identificar"},
        response_mode="hybrid",
    )
    assert R.identity_opening_intents(stripped) == set()
    out = R.route_layer1("cuánto debo", stripped, {}, session_state={}, identity_verified=False)
    assert out.intent == "identidad_requerida"


# ── Quick-reply chips (data-driven, CORE) ────────────────────────────────────
# The tenant OWNS the chips in its responses.json (per-intent + per-state); the
# LLM never authors them. These assert the engine resolves them correctly and
# that a tenant WITHOUT chips returns None (legacy behavior, no break).


def test_prestamype_declares_chips():
    spec = _spec()
    assert spec.has_chips is True
    # State block parsed from the reserved _chips key.
    assert spec.chips.get("cold")
    assert spec.chips.get("identified")


def test_chips_per_intent_take_precedence():
    # A resolved intent with its own chips → those chips (contextual), regardless
    # of the conversation state.
    spec = _spec()
    chips = R.resolve_chips(spec, intent="consulta_deuda", identity_verified=True)
    assert chips == ["Subir comprobante", "Datos de pago", "Hablar con un asesor"]


def test_chips_fall_back_to_state_when_intent_has_none():
    # No intent (or an intent without chips) → state default. Cold = unidentified.
    spec = _spec()
    assert R.resolve_chips(spec, intent=None, identity_verified=False) == [
        "Consultar mi deuda",
        "Subir comprobante",
    ]
    # identified state when verified.
    assert R.resolve_chips(spec, intent=None, identity_verified=True) == [
        "Ver mi deuda",
        "Subir comprobante",
        "Datos de pago",
    ]


def test_chips_unknown_intent_falls_back_to_state():
    spec = _spec()
    chips = R.resolve_chips(spec, intent="intent_inexistente", identity_verified=False)
    assert chips == ["Consultar mi deuda", "Subir comprobante"]


def test_chips_truncated_to_max():
    spec = _spec()
    chips = R.resolve_chips(spec, intent="consulta_deuda", max_chips=2)
    assert len(chips) == 2


def test_chips_no_ver_proyectos_anywhere():
    # Regression: the off-domain real-estate chip must NEVER appear for prestamype.
    spec = _spec()
    all_chips: list[str] = []
    for vals in spec.chips.values():
        if isinstance(vals, list):
            all_chips.extend(vals)
    for cfg in spec.intents.values():
        all_chips.extend(cfg.get("chips") or [])
    joined = " ".join(all_chips).lower()
    assert "proyecto" not in joined
    assert "ver proyectos" not in joined


def test_tenant_without_chips_returns_none():
    # A spec with neither _chips nor per-intent chips → has_chips False, no chips
    # (legacy LLM/heuristic path stays intact; nothing breaks for that tenant).
    spec = ResponsesSpec(
        intents={"saludo": {"mode": "verbatim", "template": "Hola"}},
        response_mode="hybrid",
    )
    assert spec.has_chips is False
    assert R.resolve_chips(spec, intent="saludo", identity_verified=False) is None
    assert R.resolve_chips(spec, intent=None, identity_verified=True) is None
