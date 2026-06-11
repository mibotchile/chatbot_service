"""Curated responses engine — tenant-agnostic canned/scripted replies (hybrid).

WHY this exists
---------------
Corporate collections clients want to APPROVE and DICTATE the exact copy the bot
shows (compliance + brand), and generating every reply with the LLM is the
expensive part (on Haiku, OUTPUT costs ~5x input). This engine lets each tenant
ship a ``responses.json`` "script": per-intent canned text the BACKEND fills with
real profile data (zero hallucination), plus a free keyword router that resolves
the obvious intents WITHOUT calling the LLM at all.

The engine is 100% generic. It hard-codes NO tenant: it reads the active
tenant's ``responses.json`` + ``response_mode`` flag and the verified borrower
profile. A new tenant turns the feature on by shipping its own ``responses.json``
and setting ``response_mode``; a tenant without one defaults to ``llm`` (the
current full-agent behavior) and nothing breaks.

Response modes (per tenant, in ``tenant.config.json`` → ``response_mode``)
--------------------------------------------------------------------------
- ``llm``      : agent generates everything (default — backward compatible).
- ``scripted`` : ONLY canned + keyword router; no LLM intent classification. If
                 nothing matches → canned ``no_entendido`` fallback (minimal LLM).
- ``hybrid``   : keyword router → canned; on miss the LLM classifies the intent →
                 canned; only if there's no canned for the case (and the mode
                 allows it) does the LLM generate a free reply.

responses.json format (the client's "script")
----------------------------------------------
Top-level object keyed by intent. Each intent has:
  - ``mode``: ``"verbatim"`` (exact dictated text) | ``"variant"`` (array, one is
    picked at random for naturalness without repeating).
  - a template, in one of two shapes:
      single : ``"template": "Tu saldo es {saldo}, vence {fecha_venc}."``
      list   : ``{"header": "...", "item": "... {loan}/{saldo} ...", "footer": "..."}``
               the engine iterates the borrower's credits and repeats ``item``.
  - optional ``grupal`` block (single or list shape) appended when the credit is
    grupal; ``item`` inside it loops over the codeudores.
  - ``keywords`` (optional): list of substrings/regex the Layer-1 router matches
    to resolve this intent for FREE (no LLM).

For ``variant`` mode the value is a list; each element is itself a template
(single dict/str or list dict). The picker avoids repeating the last variant.

See ``docs/responses-format.md`` for the full client-facing contract.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from tenancy.responses_spec import ResponsesSpec
from shared.templates import (  # moved to shared/ (W1 cleanup — removes cobranza→conversation edge)
    normalize_credits,
    build_variables,
    render_template,
    _money,
    _title,
    _fill,
)

# Result "source" tags surfaced to analytics so we can measure LLM savings.
SOURCE_KEYWORD = "canned_keyword"   # Layer 1 — resolved with zero LLM
SOURCE_INTENT = "canned_intent"     # Layer 2 — LLM classified, canned rendered
SOURCE_LLM = "llm"                  # free generation (no canned for the case)


def render_grupal(block: Any, profile: dict) -> str:
    """Render the optional ``grupal`` block, looping over codeudores.

    Supports the same single/list shapes as ``render_template``; in list shape
    the ``item`` is repeated per codeudor with ``{codeudor}`` / ``{rol}`` /
    ``{dni}`` (masked) variables.
    """
    if not block:
        return ""
    variables = build_variables(profile)
    codeudores = profile.get("codeudores") or []

    if isinstance(block, str):
        return _fill(block, variables).strip()

    if "item" in block:
        parts: list[str] = []
        header = block.get("header")
        if header:
            parts.append(_fill(header, {**variables, "n_codeudores": str(len(codeudores))}).strip())
        for c in codeudores:
            cvars = {
                **variables,
                "codeudor": _title(c.get("borrower_name", "")),
                "rol": c.get("rol", "codeudor"),
                "dni": _mask_dni(c.get("dni")),
            }
            parts.append(_fill(block["item"], cvars).strip())
        footer = block.get("footer")
        if footer:
            parts.append(_fill(footer, variables).strip())
        return "\n".join(p for p in parts if p)

    if "template" in block:
        names = ", ".join(_title(c.get("borrower_name", "")) for c in codeudores)
        return _fill(block["template"], {**variables, "codeudores": names}).strip()

    return ""


def _mask_dni(dni: str) -> str:
    d = str(dni or "")
    if len(d) < 4:
        return ""
    return f"{d[:2]}{'*' * (len(d) - 3)}{d[-1]}"


# ── Variant selection (no immediate repeat) ──────────────────────────────────

def pick_variant(variants: list, *, last_index: int | None = None) -> tuple[Any, int]:
    """Pick one variant, avoiding ``last_index`` when there's a choice.

    Returns ``(variant, chosen_index)`` so the caller can persist the index in
    session state and avoid repeating it on the next turn.
    """
    if not variants:
        return None, -1
    if len(variants) == 1:
        return variants[0], 0
    candidates = [i for i in range(len(variants)) if i != last_index]
    idx = random.choice(candidates)
    return variants[idx], idx


# ── Rendering an intent (resolves mode: verbatim vs variant) ──────────────────

@dataclass
class CannedResult:
    """A rendered canned response ready to return to the user."""

    text: str
    intent: str
    source: str                 # SOURCE_KEYWORD | SOURCE_INTENT
    variant_index: int = -1     # for no-repeat persistence (variant mode)


def render_intent(
    spec: ResponsesSpec,
    intent: str,
    profile: dict,
    *,
    source: str,
    last_variant_index: int | None = None,
) -> CannedResult | None:
    """Render a canned response for ``intent`` from the spec + profile.

    Handles both ``verbatim`` (single template, optionally with list/grupal) and
    ``variant`` (pick one of N). Appends the ``grupal`` block when the credit is
    grupal. Returns None if the intent isn't in the spec or renders empty.
    """
    cfg = spec.intents.get(intent)
    if not cfg:
        return None

    mode = (cfg.get("mode") or "verbatim").lower()
    variant_index = -1

    if mode == "variant":
        variants = cfg.get("variants") or cfg.get("template") or []
        if not isinstance(variants, list):
            variants = [variants]
        chosen, variant_index = pick_variant(variants, last_index=last_variant_index)
        text = render_template(chosen, profile)
    else:  # verbatim (default)
        # Prefer a list template when the borrower has >1 credit and the intent
        # ships one; otherwise the single template.
        credits = normalize_credits(profile)
        list_tpl = cfg.get("list")
        single_tpl = cfg.get("template")
        if list_tpl and len(credits) > 1:
            text = render_template(list_tpl, profile)
        elif single_tpl:
            text = render_template(single_tpl, profile)
        elif list_tpl:
            text = render_template(list_tpl, profile)
        else:
            text = ""

    # Append the grupal block when applicable (generic — any intent may carry it).
    if profile.get("is_grupal") and profile.get("codeudores") and cfg.get("grupal"):
        grupal_text = render_grupal(cfg["grupal"], profile)
        if grupal_text:
            text = f"{text}\n{grupal_text}".strip() if text else grupal_text

    text = (text or "").strip()
    if not text:
        return None
    return CannedResult(text=text, intent=intent, source=source, variant_index=variant_index)


# ── Layer 1 router: keyword/pattern matching (FREE — no LLM) ──────────────────

def match_keyword_intent(
    text: str, spec: ResponsesSpec, *, only_intents: set[str] | None = None,
) -> tuple[str, str | None] | None:
    """Match the user text to an intent via the spec's ``keywords``/``patterns``.

    Layer 1 is 100% data-driven: each intent declares its own matchers in the
    tenant's responses.json — there is NO hard-coded vocabulary in the engine.
      - ``keywords``: case-insensitive substrings (e.g. "saldo", "cuánto debo").
      - ``patterns``: regex strings (case-insensitive), for richer matching.
    The most specific match wins (longest keyword / a regex hit scores by its
    matched span).

    Returns ``(intent, captured)`` or None. ``captured`` is the value extracted
    from a winning regex match when the intent declares a ``capture`` config
    (see ``_capture_from_match``) — this is what lets an intent pass a value
    parsed from the message (e.g. a typed DNI) to its ``tool``, fully generically.
    ``only_intents`` restricts the search to a subset (used to prioritize the
    identification intents while a user is unverified).
    """
    if not text:
        return None
    low = text.lower().strip()
    best: tuple[int, str, str | None] | None = None  # (score, intent, captured)
    for intent, cfg in spec.intents.items():
        if only_intents is not None and intent not in only_intents:
            continue
        for kw in cfg.get("keywords") or []:
            k = str(kw).lower().strip()
            if k and k in low and (best is None or len(k) > best[0]):
                best = (len(k), intent, None)
        for pat in cfg.get("patterns") or []:
            try:
                m = re.search(str(pat), text, re.IGNORECASE)
            except re.error:
                logger.warning("responses: bad regex pattern in intent {}: {}", intent, pat)
                continue
            if m:
                span = max(1, m.end() - m.start())
                if best is None or span > best[0]:
                    best = (span, intent, _capture_from_match(cfg, m))
    if best is None:
        return None
    return best[1], best[2]


def _capture_from_match(cfg: dict, m: re.Match) -> str | None:
    """Extract the value an intent captures from its matched pattern, if declared.

    Data-driven: an intent opts in by setting ``"capture"`` in its config to the
    name of the tool argument to fill (e.g. ``"capture": "dni"``). The value is
    taken from the named regex group ``capture`` if present, else group 1, else
    the whole match. Returns None when the intent declares no capture.
    """
    name = cfg.get("capture")
    if not name:
        return None
    try:
        if name in (m.groupdict() or {}) and m.group(name) is not None:
            return str(m.group(name)).strip()
    except (IndexError, re.error):  # named group absent
        pass
    try:
        if m.lastindex:
            return str(m.group(1)).strip()
    except (IndexError, re.error):
        pass
    return str(m.group(0)).strip()


# ── The router (orchestrates layers per response_mode) ────────────────────────

@dataclass
class RouterOutcome:
    """What the router decided for a turn."""

    handled: bool                       # True → return ``text`` without the LLM
    text: str = ""
    intent: str | None = None
    source: str = SOURCE_LLM            # canned_keyword | canned_intent | llm
    variant_index: int = -1
    needs_llm_classification: bool = False  # hybrid miss → let the agent classify
    arm_flow_intent: str | None = None  # keyword-matched flow intent → agent arms the sticky flow + free-generates (no re-classification)
    run_tool: str | None = None         # tool the agent must execute before replying
    tool_args: dict = field(default_factory=dict)  # args parsed from the message (e.g. {"dni": "..."})
    # Re-render the intent's template AFTER its tool ran, with the tool result
    # merged into the variables (e.g. the masked destination from enviar_info).
    rerender_with_result: bool = False


def route_layer1(
    text: str,
    spec: ResponsesSpec,
    profile: dict,
    *,
    session_state: dict | None = None,
    identity_verified: bool = False,
) -> RouterOutcome:
    """Layer-1 routing: keyword/pattern → canned, zero LLM. Decides the next step.

    Returns:
      - handled=True with text     → a canned reply (keyword hit). ``run_tool`` is
        set when the matched intent declares a ``tool`` to execute first.
      - needs_llm_classification   → hybrid: Layer 1 missed, agent should classify.
      - handled=False, source=llm  → no canned path; the agent generates freely.

    The intent's ``requires_identity`` flag is honored: an unverified user hitting
    a gated intent gets the canned identity prompt instead (data-driven gate).
    ``session_state`` holds per-intent last variant index (no-repeat). Mutated on
    a hit.
    """
    if not spec.enabled:
        return RouterOutcome(handled=False, source=SOURCE_LLM)

    # ── IDC-01 (GAP-2): two-step id_contrato+DNI flow — runs before any other
    # routing so a pending DNI input is never hijacked by a keyword match. ──
    if is_id_contrato_flow_active(session_state):
        _tenant_id = (getattr(spec, "_tenant_id", None) or "prestamype")
        id_contrato_outcome = handle_id_contrato_step(
            text, spec, profile,
            session_state=session_state,
            source=SOURCE_KEYWORD,
            tenant_id=_tenant_id,
        )
        if id_contrato_outcome is not None:
            return id_contrato_outcome
        # None means resolved successfully → fall through to normal routing
        # so the identity tool can re-render the confirmation copy.

    # ── Comprobante pre-question gate (CPR-01): while the pre-question is pending,
    # intercept Sí/No replies before normal routing. ──
    if identity_verified:
        prequestion_reply = _handle_prequestion_reply(
            text, session_state, spec, profile, SOURCE_KEYWORD,
        )
        if prequestion_reply is not None:
            return prequestion_reply

    # ── Identification priority (data-driven): while the user is UNVERIFIED, an
    # identification intent (requires_identity=false + a capture + a tool) must
    # win over any gated intent. Otherwise a typed DNI that also looks like a
    # gated request would fall into the identity gate and never identify. We try
    # those intents FIRST, restricted to the matcher set — fully tenant-agnostic:
    # a tenant with no such intent simply has an empty set and nothing changes. ──
    if not identity_verified:
        opener_intents = identity_opening_intents(spec)
        if opener_intents:
            match = match_keyword_intent(text, spec, only_intents=opener_intents)
            if match:
                intent, captured = match
                out = _emit_intent(
                    intent, spec, profile, SOURCE_KEYWORD,
                    session_state=session_state, identity_verified=identity_verified,
                    captured=captured,
                )
                if out is not None:
                    logger.info("responses: layer1 identification intent={} (no LLM)", intent)
                    return out

    match = match_keyword_intent(text, spec)
    if match:
        intent, captured = match
        out = _emit_intent(
            intent, spec, profile, SOURCE_KEYWORD,
            session_state=session_state, identity_verified=identity_verified,
            captured=captured,
        )
        if out is not None:
            logger.info("responses: layer1 hit intent={} (no LLM)", intent)
            return out
        # A flow intent (e.g. comprobante_reportar) renders empty ON PURPOSE — its
        # turn is handled by the LLM. A keyword hit must ARM that flow and hand to
        # the LLM directly, never fall through to re-classification (which can
        # misfire to derivar_asesor for a clear "subir comprobante").
        if (spec.intents.get(intent) or {}).get("flow"):
            logger.info("responses: layer1 flow intent={} → arm flow + LLM", intent)
            return RouterOutcome(
                handled=False, source=SOURCE_LLM, arm_flow_intent=intent
            )

    # Layer 1 missed.
    if spec.response_mode == "scripted":
        # scripted = minimal LLM: fall back to canned no_entendido if present.
        fb = render_intent(spec, "no_entendido", profile, source=SOURCE_KEYWORD)
        if fb:
            logger.info("responses: scripted fallback → no_entendido (no LLM)")
            return RouterOutcome(
                handled=True, text=fb.text, intent="no_entendido", source=SOURCE_KEYWORD
            )
        return RouterOutcome(handled=False, source=SOURCE_LLM)

    # hybrid: ask the agent to classify the intent (Layer 2).
    return RouterOutcome(handled=False, needs_llm_classification=True, source=SOURCE_LLM)


def resolve_classified_intent(
    intent: str,
    spec: ResponsesSpec,
    profile: dict,
    *,
    session_state: dict | None = None,
    identity_verified: bool = False,
) -> RouterOutcome:
    """Layer-2 resolution: an LLM-classified intent → canned (if one exists).

    Used by the agent after the cheap classification call. Honors the intent's
    ``requires_identity`` and surfaces its ``tool``. If the spec has no canned
    response for ``intent`` the agent falls through to free generation.
    """
    if not spec.enabled or not intent:
        return RouterOutcome(handled=False, source=SOURCE_LLM)
    out = _emit_intent(
        intent, spec, profile, SOURCE_INTENT,
        session_state=session_state, identity_verified=identity_verified,
    )
    if out is not None:
        logger.info("responses: layer2 classified intent={} → canned", intent)
        return out
    return RouterOutcome(handled=False, source=SOURCE_LLM)


def _emit_intent(
    intent: str,
    spec: ResponsesSpec,
    profile: dict,
    source: str,
    *,
    session_state: dict | None,
    identity_verified: bool,
    captured: str | None = None,
) -> RouterOutcome | None:
    """Shared resolver for both layers: gate → render → attach tool.

    Returns a handled RouterOutcome, or None when the intent renders empty (the
    caller then falls through to LLM generation). ``captured`` is the value
    parsed from the matched pattern (when the intent declares a ``capture``); it
    becomes the named argument passed to the intent's ``tool`` (e.g. the typed
    DNI → ``identificar_cliente(dni=...)``).
    """
    # Data-driven identity gate: a gated intent for an unverified user → ask DNI.
    if intent_requires_identity(spec, intent) and not identity_verified:
        # Remember WHAT the user wanted so that, once they identify, we answer it
        # directly instead of re-asking "¿qué quieres hacer?" (they already told us).
        if session_state is not None and intent and intent != "identidad_requerida":
            session_state["pending_intent"] = intent
        gate = render_intent(spec, "identidad_requerida", profile, source=source)
        gate_text = gate.text if gate else (
            "Para ver los datos de tu cuenta necesito identificarte. "
            "Por favor, indícame tu número de DNI."
        )
        return RouterOutcome(
            handled=True, text=gate_text, intent="identidad_requerida", source=source
        )

    cfg = spec.intents.get(intent) or {}

    last_idx = _last_variant(session_state, intent)
    result = render_intent(spec, intent, profile, source=source, last_variant_index=last_idx)
    if not result:
        return None
    _remember_variant(session_state, intent, result.variant_index)

    # Fail-closed: an intent that needs a captured value (e.g. ``identificar`` needs
    # a valid 8-digit DNI) but got NONE — typically the LLM-classified path, where
    # the capture pattern (\d{8}) didn't match (a 9-digit or malformed number) —
    # must NOT render its success template (that would falsely say "Verifiqué tu
    # identidad") nor run the tool with an empty argument. Ask for the value again.
    capture_name = cfg.get("capture")
    if capture_name and not captured:
        nf = cfg.get("not_found")
        if nf:
            return RouterOutcome(
                handled=True, text=render_template(nf, {}), intent=intent, source=source
            )
        return None

    # Build the tool args: if the intent captures a value, pass it as the named
    # argument (the ``capture`` name == the tool's parameter name, data-driven).
    tool_args: dict = {}
    if capture_name and captured:
        tool_args[str(capture_name)] = captured

    # ── Session writes (data-driven): an intent may stash values into the
    # session for a LATER turn. Used by the "ask the channel" flow: the deliverable
    # intent stores ``pending_deliverable=estado_cuenta`` now; the channel-choice
    # intent reads it next turn. Values support a literal or ``{capture}``. ──
    for skey, sval in (cfg.get("set_session") or {}).items():
        val = captured if (isinstance(sval, str) and sval == "{capture}") else sval
        _set_session(session_state, str(skey), val)

    # ── Session reads (data-driven): pull named keys from the session into the
    # tool args (e.g. the channel-choice intent feeds ``tipo`` from the pending
    # deliverable stored last turn + ``canal`` captured this turn into enviar_info). ──
    for need in cfg.get("needs_session") or []:
        sv = _get_session(session_state, str(need))
        if sv is not None:
            tool_args[str(need)] = sv

    return RouterOutcome(
        handled=True, text=result.text, intent=intent, source=source,
        variant_index=result.variant_index, run_tool=intent_tool(spec, intent),
        tool_args=tool_args,
        rerender_with_result=bool(cfg.get("rerender_with_result")),
    )


def resolve_chips(
    spec: ResponsesSpec,
    *,
    intent: str | None = None,
    identity_verified: bool = False,
    max_chips: int = 4,
) -> list[str] | None:
    """Resolve the quick-reply chips for a turn, 100% data-driven (zero LLM).

    Precedence:
      1. Per-intent chips — the resolved intent declares ``chips`` → contextual
         chips offered AFTER that intent's reply (e.g. consulta_deuda → subir
         comprobante / datos de pago / asesor).
      2. Per-state chips — the ``_chips`` block keyed by conversation state
         (``identified`` when the user is verified, else ``cold``) → the
         saludo/no-intent default.

    Returns the chip labels (truncated to ``max_chips``), or None when the
    tenant declares no chips for this case (caller keeps legacy behavior).
    Tenant-agnostic: a tenant with no ``chips``/``_chips`` always gets None.
    """
    if not spec.has_chips:
        return None
    if intent:
        cfg = spec.intents.get(intent) or {}
        intent_chips = cfg.get("chips")
        if intent_chips:
            return [str(c) for c in intent_chips][:max_chips]
    state = "identified" if identity_verified else "cold"
    state_chips = spec.chips.get(state)
    if state_chips:
        return [str(c) for c in state_chips][:max_chips]
    return None


def known_intents(spec: ResponsesSpec) -> list[str]:
    """Intent names the spec defines — the menu the LLM classifier chooses from."""
    return list(spec.intents.keys())


def classifier_menu(spec: ResponsesSpec) -> dict[str, str]:
    """``{intent: description}`` built dynamically from the tenant's responses.json.

    The Layer-2 LLM classifier picks from THIS menu — the catalog is data-driven,
    never hard-coded. Falls back to the intent name when no ``description`` is set.

    An intent can opt OUT of being classifiable with ``"classifiable": false``
    (default true). This is for intents whose template is rendered by another
    path (a tool result or a non-LLM flow, e.g. ``comprobante_resultado``, that is
    the voucher acuse) and must NEVER be picked by the LLM for a fresh user turn —
    otherwise it would hijack the turn with a false confirmation.
    """
    return {
        intent: (cfg.get("description") or intent)
        for intent, cfg in spec.intents.items()
        if cfg.get("classifiable", True)
    }


def intent_requires_identity(spec: ResponsesSpec, intent: str) -> bool:
    """Whether ``intent`` needs a verified identity (data-driven gate flag)."""
    return bool((spec.intents.get(intent) or {}).get("requires_identity"))


def identity_opening_intents(spec: ResponsesSpec) -> set[str]:
    """Intents that can OPEN the identity gate (data-driven, tenant-agnostic).

    An opener is any intent that does NOT require identity, captures a value from
    its pattern, and declares a tool to run with it — i.e. the identification
    intent(s). Used to prioritize identification over gated intents while the
    user is unverified, so a typed DNI never falls into the identity gate.
    Empty for tenants that declare no such intent (behavior unchanged for them).
    """
    out: set[str] = set()
    for intent, cfg in spec.intents.items():
        if not cfg.get("requires_identity") and cfg.get("capture") and cfg.get("tool"):
            out.add(intent)
    return out


def intent_tool(spec: ResponsesSpec, intent: str) -> str | None:
    """Optional tool name to run before responding (resolved vs the ToolRegistry).

    Keeps the intent→tool mapping data-driven: the JSON names the tool, the engine
    executes it against the existing registry (the tool itself is engine code).
    """
    tool = (spec.intents.get(intent) or {}).get("tool")
    return str(tool) if tool else None


# ── session_state variant memory helpers ─────────────────────────────────────

_VARIANT_KEY = "_responses_variant_idx"


def _last_variant(session_state: dict | None, intent: str) -> int | None:
    if not session_state:
        return None
    return (session_state.get(_VARIANT_KEY) or {}).get(intent)


def _remember_variant(session_state: dict | None, intent: str, index: int) -> None:
    if session_state is None or index < 0:
        return
    bucket = session_state.setdefault(_VARIANT_KEY, {})
    bucket[intent] = index


# ── Generic data-driven session scratch (set_session / needs_session) ─────────
# A small namespaced bucket so intent-declared keys never collide with the
# variant memory. Used by the "ask the channel" delivery flow (pending tipo +
# chosen canal carried across turns), but fully generic for any tenant intent.
_SESSION_KEY = "_responses_session"


def _set_session(session_state: dict | None, key: str, value) -> None:
    if session_state is None:
        return
    session_state.setdefault(_SESSION_KEY, {})[key] = value


def _get_session(session_state: dict | None, key: str):
    if not session_state:
        return None
    return (session_state.get(_SESSION_KEY) or {}).get(key)


# ── PR2 net-new: prequestion gate / consulta_deuda / horario / credit-selector / id_contrato ──


_COMPROBANTE_INTENTS = frozenset({"comprobante_reportar"})

_PREQUESTION_ANSWERED_KEY = "comprobante_prequestion_answered"
_PREQUESTION_INTENT = "comprobante_proxima_cuota_pregunta"

_MISUNDERSTOOD_COUNT_KEY = "misunderstood_count"

_VENCIDO_ONLY_INTENTS = frozenset({"compromiso_pago", "realizar_pago_vencido"})


_PENDING_INTENT_KEY = "pending_intent"



def _handle_prequestion_reply(
    text: str,
    session_state: dict | None,
    spec: ResponsesSpec,
    profile: dict,
    source: str,
) -> RouterOutcome | None:
    """Handle 'Sí'/'No' replies to the comprobante pre-question gate.

    Called from route_layer1 when a pending comprobante pre-question is active
    (pending_intent == 'comprobante'). Returns a handled RouterOutcome or None
    if the text is not a Sí/No reply (caller continues normal routing).
    """
    if session_state is None:
        return None
    if session_state.get(_PENDING_INTENT_KEY) != "comprobante":
        return None
    low = (text or "").lower().strip()
    # "Sí" → clear pending, mark answered, continue to comprobante flow
    if low in ("sí", "si", "s", "yes"):
        session_state[_PREQUESTION_ANSWERED_KEY] = True
        session_state.pop(_PENDING_INTENT_KEY, None)
        # Emit comprobante_reportar so the LLM flow continues
        out = _emit_intent(
            "comprobante_reportar", spec, profile, source,
            session_state=session_state, identity_verified=True,
        )
        return out
    # "No" → escalate to asesor
    if low in ("no", "n"):
        session_state.pop(_PENDING_INTENT_KEY, None)
        session_state.pop(_PREQUESTION_ANSWERED_KEY, None)
        result = render_intent(spec, "derivar_asesor", profile, source=source)
        text_out = result.text if result else "Te derivo con un asesor de PrestamYpe."
        return RouterOutcome(
            handled=True,
            text=text_out,
            intent="derivar_asesor",
            source=source,
            run_tool=intent_tool(spec, "derivar_asesor"),
        )
    return None



def handle_consulta_deuda(
    spec: ResponsesSpec,
    profile: dict,
    *,
    session_state: dict | None,
    source: str,
) -> RouterOutcome | None:
    """SCR-02: Render ``consulta_deuda`` with internal branching on ``credit_state``.

    Reads ``session_state["credit_state"]`` to choose the branch copy from the
    spec's ``credit_state_branches`` map inside the ``consulta_deuda`` intent.
    Returns None when the spec carries no ``consulta_deuda`` intent (tenant opt-out).
    """
    cfg = spec.intents.get("consulta_deuda")
    if not cfg:
        return None

    credit_state = (session_state or {}).get("credit_state", "al_dia")
    branches = cfg.get("credit_state_branches") or {}
    branch_cfg = branches.get(credit_state) or {}

    # Render the branch template
    branch_template = branch_cfg.get("template") or cfg.get("template", "")
    from shared.templates import render_template  # noqa: PLC0415
    text = render_template(branch_template, profile).strip()

    # Append options list from the branch (for display)
    options = branch_cfg.get("options") or []
    if options and text:
        opts_text = "\n".join(f"• {o['label']}" for o in options if o.get("label"))
        if opts_text:
            text = f"{text}\n{opts_text}"

    if not text:
        return None

    _reset_misunderstood(session_state)
    return RouterOutcome(
        handled=True,
        text=text,
        intent="consulta_deuda",
        source=source,
        run_tool=intent_tool(spec, "consulta_deuda"),
    )



def handle_vencido_only_intent(
    intent: str,
    spec: ResponsesSpec,
    profile: dict,
    *,
    session_state: dict | None,
    source: str,
) -> RouterOutcome | None:
    """Guard vencido-only intents (compromiso_pago, realizar_pago_vencido).

    Returns a redirect to the credit-state menu when ``credit_state != 'vencido'``.
    Returns None when the intent is not in the vencido-only set (caller continues).
    """
    if intent not in _VENCIDO_ONLY_INTENTS:
        return None

    credit_state = (session_state or {}).get("credit_state", "al_dia")
    if credit_state != "vencido":
        # Redirect to the state-appropriate menu by re-triggering consulta_deuda
        return handle_consulta_deuda(spec, profile, session_state=session_state, source=source)

    return None  # allowed — caller proceeds


def record_misunderstood(
    spec: ResponsesSpec,
    profile: dict,
    *,
    session_state: dict | None,
    source: str,
) -> RouterOutcome:
    """INF-10: 2-strike fallback for unrecognized input.

    Strike 1: emit ``no_comprendida_1`` and increment ``misunderstood_count``.
    Strike 2+: escalate to asesor via ``no_comprendida_2_asesor``.
    ``misunderstood_count`` is reset on any successfully handled intent.
    """
    if session_state is None:
        session_state = {}

    count = session_state.get(_MISUNDERSTOOD_COUNT_KEY, 0) + 1
    session_state[_MISUNDERSTOOD_COUNT_KEY] = count

    if count >= 2:
        result = render_intent(spec, "no_comprendida_2_asesor", profile, source=source)
        text = result.text if result else "Te derivo con un asesor."
        return RouterOutcome(
            handled=True,
            text=text,
            intent="no_comprendida_2_asesor",
            source=source,
            run_tool=intent_tool(spec, "no_comprendida_2_asesor"),
        )

    result = render_intent(spec, "no_comprendida_1", profile, source=source)
    text = result.text if result else "No entendí bien. ¿Puedes reformular tu consulta?"
    return RouterOutcome(
        handled=True,
        text=text,
        intent="no_comprendida_1",
        source=source,
    )


def _reset_misunderstood(session_state: dict | None) -> None:
    """Reset the 2-strike counter after a successfully handled intent."""
    if session_state is not None:
        session_state.pop(_MISUNDERSTOOD_COUNT_KEY, None)


def check_out_of_hours(
    dt: datetime,
    spec: ResponsesSpec,
    profile: dict,
    *,
    source: str,
    tenant_id: str = "prestamype",
) -> RouterOutcome | None:
    """INF-09: Return a ``fuera_de_horario`` outcome when outside business hours.

    Uses ``is_business_hours`` from ``features.cobranza.horario`` — reads the
    feriados_peru_2026.json + tenant cobranza.horario config. No hardcoded dates.

    Args:
        dt: current datetime (Lima local, naive or aware).
        spec: tenant responses spec.
        profile: verified borrower profile.
        source: SOURCE_KEYWORD | SOURCE_INTENT.
        tenant_id: tenant whose config/feriados to use.

    Returns:
        A handled RouterOutcome with the ``fuera_de_horario`` template when the
        session falls outside business hours (including refrigerio 13:00–14:00).
        Returns None when the session IS within business hours (caller continues).
    """
    from features.cobranza.horario import is_business_hours  # avoid circular at module level

    if is_business_hours(dt, tenant_id=tenant_id):
        return None  # within hours — no gate

    result = render_intent(spec, "fuera_de_horario", profile, source=source)
    text = result.text if result else (
        "Nuestro horario de atención es lunes a viernes de 9:00 a 18:30. "
        "Te invitamos a contactarnos en ese horario."
    )
    return RouterOutcome(
        handled=True,
        text=text,
        intent="fuera_de_horario",
        source=source,
    )


# ── INF-08: Due-date holiday / domingo check (al_dia / por_vencer only) ───────



def check_due_date_holiday(
    intent: str,
    spec: ResponsesSpec,
    profile: dict,
    *,
    session_state: dict | None,
    source: str,
    tenant_id: str = "prestamype",
) -> RouterOutcome | None:
    """INF-08: Route the domingo/feriado intent based on credit_state + date check.

    When the resolved intent is the domingo/feriado query AND the profile's
    ``next_due_date`` is a feriado or sunday:
      - credit_state al_dia / por_vencer → emit ``domingo_feriado_al_dia_por_vencer``
        (business rule: next business day).
      - credit_state vencido → emit ``domingo_feriado_vencido_redirect`` (overdue
        context makes this irrelevant — redirect to vencido menu).

    Returns None for all other intents (caller continues normally).

    Uses ``is_feriado`` from ``features.cobranza.horario`` — reads
    ``feriados_peru_2026.json`` exclusively. No hardcoded date lists.
    """
    _DOMINGO_FERIADO_INTENTS = frozenset({
        "domingo_feriado_al_dia_por_vencer",
        "domingo_feriado_vencido_redirect",
    })
    if intent not in _DOMINGO_FERIADO_INTENTS:
        return None

    from datetime import date as _date
    from features.cobranza.horario import is_feriado  # avoid circular at module level

    credit_state = (session_state or {}).get("credit_state", "al_dia")

    if credit_state == "vencido":
        # Vencido users: redirect regardless of calendar — overdue context dominates
        result = render_intent(
            spec, "domingo_feriado_vencido_redirect", profile, source=source
        )
        text = result.text if result else (
            "Tienes cuotas vencidas. Te invitamos a regularizar tu situación."
        )
        return RouterOutcome(
            handled=True,
            text=text,
            intent="domingo_feriado_vencido_redirect",
            source=source,
        )

    # al_dia / por_vencer: check if next_due_date is a holiday or sunday
    next_due_raw = profile.get("next_due_date")
    is_holiday_or_sunday = False
    if next_due_raw:
        try:
            due = _date.fromisoformat(str(next_due_raw))
            is_holiday_or_sunday = is_feriado(due, tenant_id=tenant_id) or due.weekday() == 6
        except ValueError:
            pass

    # Emit the informational holiday template (spec wording: next business day)
    result = render_intent(
        spec, "domingo_feriado_al_dia_por_vencer", profile, source=source
    )
    text = result.text if result else (
        "Si tu fecha de pago cae en domingo o feriado, se traslada al siguiente "
        "día hábil."
    )
    out = RouterOutcome(
        handled=True,
        text=text,
        intent="domingo_feriado_al_dia_por_vencer",
        source=source,
    )
    # Stash whether the due date actually falls on a holiday/sunday (for callers
    # that want to surface this context proactively).
    if session_state is not None:
        session_state["due_date_is_holiday_or_sunday"] = is_holiday_or_sunday
    return out


# ── MCD-01: Multi-credit selector (Phase 8) ──────────────────────────────────



def emit_credit_selector(
    spec: ResponsesSpec,
    profile: dict,
    *,
    session_state: dict | None,
    source: str,
) -> RouterOutcome | None:
    """MCD-01: Emit the credit selector when the borrower has exactly 2 credits.

    Returns a handled RouterOutcome with ``intent="credit_selector"`` when
    ``len(profile["credits"]) == 2``. The selector text includes both credit IDs
    and inversionistas so the borrower can choose.

    Returns None when:
    - profile has no "credits" key, OR
    - len(credits) != 2 (single credit → skip selector; 0 or >2 → not handled here)

    ``handle_credit_selection`` must be called once the user responds with their
    choice to store ``session_state["selected_credit_id"]``.
    """
    credits: list[dict] = profile.get("credits") or []
    if len(credits) != 2:
        return None

    c1, c2 = credits[0], credits[1]
    label1 = (
        f"{c1.get('account_id') or c1.get('loan_number', '?')} — "
        f"{c1.get('inversionista', '')}"
    )
    label2 = (
        f"{c2.get('account_id') or c2.get('loan_number', '?')} — "
        f"{c2.get('inversionista', '')}"
    )

    cfg = spec.intents.get("credit_selector") or {}
    raw_tpl = cfg.get("template") or (
        "Tienes 2 créditos activos. ¿Sobre cuál deseas consultar?\n"
        "• {credit_label_1}\n• {credit_label_2}\n"
        "Responde con el número o código del crédito."
    )
    text = raw_tpl.replace("{credit_label_1}", label1).replace("{credit_label_2}", label2)

    return RouterOutcome(
        handled=True,
        text=text,
        intent="credit_selector",
        source=source,
    )



def handle_credit_selection(
    credit_id: str,
    profile: dict,  # noqa: ARG001 — reserved for future validation
    *,
    session_state: dict,
) -> None:
    """MCD-01: Store the borrower's selected credit ID in session_state.

    Called when the user replies to the credit selector with a credit ID.
    Downstream intent handlers (cuentas_bancarias, consulta_deuda, cronograma)
    read ``session_state["selected_credit_id"]`` to filter to the selected credit.

    For single-credit users ``selected_credit_id`` is set automatically to the
    only credit's account_id (no selector shown, no explicit call needed).
    """
    session_state["selected_credit_id"] = credit_id



def apply_comprobante_prequestion_gate(
    outcome: RouterOutcome,
    spec: ResponsesSpec,
    profile: dict,
    *,
    session_state: dict | None,
) -> RouterOutcome:
    """CPR-01: intercept a comprobante intent and gate it behind a pre-question.

    If the matched intent is a comprobante flow intent AND the pre-question has
    not been answered yet in this session, replace the outcome with the
    pre-question emit and store 'pending_intent=comprobante' in session_state.

    Called by the agent AFTER the canned router resolves an outcome, so the
    generic responses engine remains untouched (no behavior change for tenants
    that don't ship the comprobante_proxima_cuota_pregunta intent).

    Returns the (possibly replaced) outcome.
    """
    if session_state is None:
        return outcome
    if outcome.intent not in _COMPROBANTE_INTENTS:
        return outcome
    if session_state.get(_PREQUESTION_ANSWERED_KEY):
        return outcome
    prequestion = render_intent(spec, _PREQUESTION_INTENT, profile, source=outcome.source)
    if not prequestion:
        return outcome  # tenant has no pre-question intent — skip gate
    session_state[_PENDING_INTENT_KEY] = "comprobante"
    return RouterOutcome(
        handled=True,
        text=prequestion.text,
        intent=_PREQUESTION_INTENT,
        source=outcome.source,
    )


# ── IDC-01: ID-Contrato + DNI dual-factor identity path ──────────────────────



_ID_CONTRATO_RETRY_KEY = "id_contrato_retry_count"



_ID_CONTRATO_PENDING_KEY = "id_contrato_pending_contrato_id"
_ID_CONTRATO_EXPECTING_KEY = "id_contrato_expecting_contrato"



_ID_CONTRATO_RETRY_MAX = 3  # max failed attempts before asesor escalation



def handle_id_contrato_not_found(
    spec: "ResponsesSpec",
    profile: dict,
    *,
    session_state: dict | None,
    source: str,
) -> RouterOutcome:
    """IDC-01: Handle a failed contrato+DNI identification attempt.

    Increments the retry counter. At _ID_CONTRATO_RETRY_MAX escalates to asesor
    via ``id_contrato_max_retries``. Below the limit emits ``id_contrato_not_found``
    (neutral, no-reveal — same message for both 'not found' and 'DNI mismatch').

    The caller must NOT reveal whether the contract exists or whether the DNI
    matched — always use this function so the response is identical either way.
    """
    if session_state is None:
        session_state = {}

    count = session_state.get(_ID_CONTRATO_RETRY_KEY, 0) + 1
    session_state[_ID_CONTRATO_RETRY_KEY] = count

    if count >= _ID_CONTRATO_RETRY_MAX:
        # Clear pending state on max retries
        session_state.pop(_ID_CONTRATO_PENDING_KEY, None)
        result = render_intent(spec, "id_contrato_max_retries", profile, source=source)
        text = result.text if result else (
            "No pude verificar tu identidad. Te derivo con un asesor."
        )
        return RouterOutcome(
            handled=True,
            text=text,
            intent="id_contrato_max_retries",
            source=source,
            run_tool=intent_tool(spec, "id_contrato_max_retries"),
        )

    result = render_intent(spec, "id_contrato_not_found", profile, source=source)
    text = result.text if result else (
        "No pude verificar tu identidad con esos datos. "
        "Por favor, revísalos e inténtalo de nuevo, o indícame tu DNI directamente."
    )
    return RouterOutcome(
        handled=True,
        text=text,
        intent="id_contrato_not_found",
        source=source,
    )



def handle_id_contrato_step(
    text: str,
    spec: "ResponsesSpec",
    profile: dict,
    *,
    session_state: dict | None,
    source: str,
    tenant_id: str = "prestamype",
) -> RouterOutcome | None:
    """IDC-01: Two-step contrato+DNI identification flow.

    Step 1: user is in the id_contrato_prompt state (no pending contrato yet).
            The text IS the contrato_id — store it, emit the DNI prompt.
    Step 2: pending contrato_id is set — the text IS the DNI.
            Call resolve_contrato(contrato_id, dni). On profile → verify + classify.
            On None → handle_id_contrato_not_found (no-reveal, retry or asesor).

    Returns None when no id_contrato flow is active in session_state (caller
    continues normal routing).
    """
    if session_state is None:
        return None

    pending_contrato = session_state.get(_ID_CONTRATO_PENDING_KEY)

    if pending_contrato:
        # Step 2: user just typed their DNI — verify
        from features.cobranza.doris_debt_source import resolve_contrato  # noqa: PLC0415
        dni = text.strip()
        profile_result = resolve_contrato(pending_contrato, dni, tenant_id)

        # Clear pending regardless of outcome (avoid stale state)
        session_state.pop(_ID_CONTRATO_PENDING_KEY, None)

        if profile_result is not None:
            # Verified — reset retry counter, mark identity as pending-tool-verify
            # The profile is stashed in session for the tool registry to pick up.
            session_state.pop(_ID_CONTRATO_RETRY_KEY, None)
            session_state["id_contrato_verified_profile"] = profile_result
            # Emit the id_contrato prompt confirmation using the resolved profile
            # (same approach as DNI identification — re-render after tool success).
            return None  # let the caller proceed to identification tool

        return handle_id_contrato_not_found(
            spec, profile, session_state=session_state, source=source,
        )

    # Step 1: the user was asked for their contrato_id (id_contrato_prompt emitted).
    # The text IS the contrato_id — arm the DNI step and emit the DNI prompt.
    if session_state.get(_ID_CONTRATO_EXPECTING_KEY):
        contrato_id = text.strip()
        session_state.pop(_ID_CONTRATO_EXPECTING_KEY, None)
        arm_id_contrato_flow(session_state, contrato_id)
        result = render_intent(spec, "id_contrato_dni_prompt", profile, source=source)
        out_text = result.text if result else (
            "Gracias. Ahora ingresa tu número de DNI (8 dígitos) para validar tu identidad."
        )
        return RouterOutcome(
            handled=True, text=out_text, intent="id_contrato_dni_prompt", source=source,
        )

    return None



def is_id_contrato_flow_active(session_state: dict | None) -> bool:
    """Return True when a two-step id_contrato flow is awaiting the contrato or DNI."""
    return bool(
        session_state and (
            session_state.get(_ID_CONTRATO_PENDING_KEY)
            or session_state.get(_ID_CONTRATO_EXPECTING_KEY)
        )
    )


def arm_id_contrato_flow(session_state: dict | None, contrato_id: str) -> None:
    """Store the contrato_id and await the DNI step (arms the two-step flow)."""
    if session_state is not None:
        session_state[_ID_CONTRATO_PENDING_KEY] = contrato_id


# ── CMP-01/CMP-02: Bot-owned payment commitment ───────────────────────────────

_COMPROMISO_DATE_PENDING_KEY = "compromiso_pago_pending_date"


async def handle_compromiso_date_reply(
    text: str,
    spec: ResponsesSpec,
    profile: dict,
    *,
    session_state: dict | None,
    source: str,
    pool: "Any | None" = None,
    schema: str = "dev",
    conversation_id: str = "",
) -> RouterOutcome | None:
    """CMP-01/CMP-02: Handle the user's date reply to the compromiso_pago prompt.

    Flow:
      1. Called only when session_state["compromiso_pago_pending_date"] is True
         (set by _handle_compromiso_pending in route_layer1).
      2. Parses the date from text.
      3. Calls register_commitment — synchronous user-facing action (awaited).
      4. On success → emit confirmation (CMP-02) and return to vencido menu.
         On failure (out-of-window, unparseable, DB error) → escalate to asesor.

    Returns None when the compromiso_pago_pending_date flag is not active
    (caller continues normal routing).
    """
    if not (session_state or {}).get(_COMPROMISO_DATE_PENDING_KEY):
        return None

    from features.cobranza.commitment import register_commitment  # noqa: PLC0415

    amount: float = float(
        profile.get("saldo_por_cancelar")
        or profile.get("monto_cuota")
        or profile.get("cuota")
        or 0.0
    )

    result = await register_commitment(
        pool, schema, conversation_id,
        date_str=(text or "").strip(),
        amount=amount,
        profile=profile,
    )

    # Clear the pending flag regardless of outcome
    session_state.pop(_COMPROMISO_DATE_PENDING_KEY, None)

    if result.escalate:
        # Escalate to asesor (beyond window, unparseable, past, or DB failure)
        escalate_result = render_intent(spec, "derivar_asesor", profile, source=source)
        escalate_text = escalate_result.text if escalate_result else (
            "Te derivo con un asesor de PrestamYpe para coordinar tu compromiso de pago."
        )
        return RouterOutcome(
            handled=True,
            text=escalate_text,
            intent="derivar_asesor",
            source=source,
            run_tool=intent_tool(spec, "derivar_asesor"),
        )

    # CMP-02: success confirmation
    commitment_date_str = (
        result.commitment_date.strftime("%d/%m/%Y") if result.commitment_date else text.strip()
    )
    confirm_cfg = spec.intents.get("compromiso_pago_confirmado") or {}
    confirm_tpl = confirm_cfg.get("template") or (
        "Registramos tu compromiso de pago para el {commitment_date}. "
        "Te enviaremos un recordatorio ese día."
    )
    rendered_text = confirm_tpl.replace("{commitment_date}", commitment_date_str)

    _reset_misunderstood(session_state)
    return RouterOutcome(
        handled=True,
        text=rendered_text,
        intent="compromiso_pago_confirmado",
        source=source,
    )
