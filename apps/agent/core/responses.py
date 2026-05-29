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
from pathlib import Path
from typing import Any

from loguru import logger

# Result "source" tags surfaced to analytics so we can measure LLM savings.
SOURCE_KEYWORD = "canned_keyword"   # Layer 1 — resolved with zero LLM
SOURCE_INTENT = "canned_intent"     # Layer 2 — LLM classified, canned rendered
SOURCE_LLM = "llm"                  # free generation (no canned for the case)


# ── Profile normalization (generic: principal + additional_credits → creditos) ──

def normalize_credits(profile: dict) -> list[dict]:
    """Flatten a borrower profile into a uniform list of credits.

    The principal credit (the profile itself) plus every entry in
    ``additional_credits`` become one homogeneous list, so the renderer never
    branches on "1 vs N credits". Each credit dict carries the per-credit fields
    (loan, saldo, cuota, fecha_venc, …) the templates reference. Purely
    in-memory — the fixture on disk is untouched.
    """
    sym = profile.get("currency_symbol", "S/")

    def _one(c: dict, fallback_sym: str) -> dict:
        csym = c.get("currency_symbol", fallback_sym)
        return {
            "loan": c.get("loan_number") or c.get("account_id") or "",
            "account_id": c.get("account_id") or c.get("loan_number") or "",
            "moneda": csym,
            "saldo": _money(c.get("balance"), csym),
            "saldo_raw": c.get("balance", 0.0) or 0.0,
            "cuota": _money(c.get("next_installment_amount"), csym),
            "fecha_venc": c.get("next_due_date") or "",
            "dias_mora": str(c.get("days_overdue", 0) or 0),
            "estado": c.get("status_label") or c.get("status") or "",
            "cci": c.get("cci") or "",
            "banco": c.get("banco") or "",
        }

    credits = [_one(profile, sym)]
    for extra in profile.get("additional_credits") or []:
        credits.append(_one(extra, sym))
    return credits


def build_variables(profile: dict) -> dict[str, str]:
    """Top-level template variables for single templates, from the profile.

    These mirror the principal credit + borrower identity. For list/grupal the
    renderer iterates ``normalize_credits`` / ``codeudores`` instead.
    """
    sym = profile.get("currency_symbol", "S/")
    first_name = str(profile.get("borrower_name", "")).split(" ")[0].title()
    return {
        "nombre": first_name,
        "nombre_completo": _title(profile.get("borrower_name", "")),
        "saldo": _money(profile.get("balance"), sym),
        "moneda": sym,
        "fecha_venc": profile.get("next_due_date") or "",
        "cuota": _money(profile.get("next_installment_amount"), sym),
        "loan": profile.get("loan_number") or profile.get("account_id") or "",
        "dias_mora": str(profile.get("days_overdue", 0) or 0),
        "estado": profile.get("status_label") or profile.get("status") or "",
        "cci": profile.get("cci") or "",
        "banco": profile.get("banco") or "",
    }


def _money(amount: Any, sym: str = "S/") -> str:
    try:
        return f"{sym} {float(amount or 0.0):,.2f}"
    except (TypeError, ValueError):
        return f"{sym} 0.00"


def _title(s: str) -> str:
    return " ".join(w.capitalize() for w in str(s or "").split())


def _fill(template: str, variables: dict[str, str]) -> str:
    """Substitute ``{var}`` tokens. Unknown tokens are left as-is (visible bug
    surface in the client's script, not a crash)."""
    def _sub(m: re.Match) -> str:
        key = m.group(1)
        return str(variables.get(key, m.group(0)))

    return re.sub(r"\{(\w+)\}", _sub, template or "")


# ── Template rendering (single / list / grupal) ─────────────────────────────

def render_template(tpl: Any, profile: dict) -> str:
    """Render one template (single str/dict or list dict) against the profile.

    - str          → single fill with top-level variables.
    - {"template"} → single fill.
    - {"header"/"item"/"footer"} → list: header + item-per-credit + footer.
    Returns the assembled message. Empty when ``tpl`` is falsy.
    """
    if not tpl:
        return ""
    variables = build_variables(profile)

    if isinstance(tpl, str):
        return _fill(tpl, variables).strip()

    if "template" in tpl:  # single shape
        return _fill(tpl["template"], variables).strip()

    if "item" in tpl:  # list shape (multi-credit)
        credits = normalize_credits(profile)
        parts: list[str] = []
        header = tpl.get("header")
        if header:
            parts.append(_fill(header, {**variables, "n_creditos": str(len(credits))}).strip())
        total = 0.0
        for i, c in enumerate(credits, start=1):
            total += c.get("saldo_raw", 0.0)
            parts.append(_fill(tpl["item"], {**variables, **c, "n": str(i)}).strip())
        footer = tpl.get("footer")
        if footer:
            total_vars = {
                **variables,
                "n_creditos": str(len(credits)),
                "total": _money(total, profile.get("currency_symbol", "S/")),
            }
            parts.append(_fill(footer, total_vars).strip())
        return "\n".join(p for p in parts if p)

    return ""


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


# ── The responses spec (one tenant's responses.json) ─────────────────────────

@dataclass
class ResponsesSpec:
    """Parsed ``responses.json`` for a tenant. Empty when the tenant has none."""

    intents: dict[str, dict] = field(default_factory=dict)
    response_mode: str = "llm"
    # Data-driven SENDABLE info types (envío de info bajo demanda). Keyed by tipo
    # (e.g. estado_cuenta), each with per-channel copy (correo/whatsapp). Lives in
    # responses.json under the reserved ``_deliverables`` key (ignored as an
    # intent). Empty for tenants that don't ship it. See docs/deliverables-format.md.
    deliverables: dict[str, dict] = field(default_factory=dict)
    # Data-driven quick-reply CHIPS by conversation state. Keyed by state name
    # (e.g. ``cold`` = unidentified, ``identified`` = verified). Lives in
    # responses.json under the reserved ``_chips`` key. Per-intent chips live on
    # each intent under ``chips``. Empty → no tenant chips (LLM/heuristic chips,
    # backward compatible). See docs/responses-format.md.
    chips: dict[str, list[str]] = field(default_factory=dict)

    @property
    def has_chips(self) -> bool:
        """True when the tenant declares chips (per-state or per-intent).

        When True, the BACKEND owns the quick-replies (data-driven, zero LLM
        hallucination) and any LLM-suggested chips are ignored. When False, the
        tenant keeps the legacy LLM/heuristic chip behavior (no break)."""
        if self.chips:
            return True
        return any((cfg or {}).get("chips") for cfg in self.intents.values())

    @property
    def enabled(self) -> bool:
        """True when canned responses are active (any mode but plain ``llm``)."""
        return self.response_mode in ("scripted", "hybrid") and bool(self.intents)

    def has_intent(self, intent: str) -> bool:
        return intent in self.intents

    @classmethod
    def from_dir(cls, tenant_dir: str | Path, response_mode: str = "llm") -> ResponsesSpec:
        """Load ``responses.json`` from a tenant directory. Missing → empty spec.

        A missing file is the normal "tenant uses pure LLM" case — never an
        error. A malformed file logs a warning and degrades to empty (LLM).
        """
        path = Path(tenant_dir) / "responses.json"
        if not path.exists():
            return cls(intents={}, response_mode=response_mode or "llm")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("responses.json malformed for {}; falling back to llm", tenant_dir)
            return cls(intents={}, response_mode=response_mode or "llm")
        # Allow an in-file ``response_mode`` override; the tenant.config flag wins
        # when provided (passed in), else the file's own, else llm.
        file_mode = data.pop("_response_mode", None)
        deliverables = data.get("_deliverables") or {}
        chips = data.get("_chips") or {}
        intents = {k: v for k, v in data.items() if not k.startswith("_")}
        return cls(
            intents=intents,
            response_mode=(response_mode or file_mode or "llm"),
            deliverables=deliverables if isinstance(deliverables, dict) else {},
            chips=chips if isinstance(chips, dict) else {},
        )


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

    # Build the tool args: if the intent captures a value, pass it as the named
    # argument (the ``capture`` name == the tool's parameter name, data-driven).
    tool_args: dict = {}
    capture_name = cfg.get("capture")
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
