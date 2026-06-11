"""Template rendering utilities — shared across features.

Provides profile normalization and ``{variable}`` template filling for
canned/scripted responses. Moved here from features/conversation/responses.py
so any feature can import without creating a cross-feature dependency.
"""

from __future__ import annotations

import re
from typing import Any


# ── Profile normalization (generic: principal + additional_credits → credits) ──

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
    monto_vencido = float(profile.get("monto_vencido") or 0.0)
    cuotas_vencidas = int(profile.get("cuotas_vencidas") or 0)
    _cp = profile.get("cuotas_pagadas")
    _cpe = profile.get("cuotas_pendientes")
    # Moratoria (INF-12): compute penalidad + interés when vencido and the
    # profile carries the fields (enriched on vencido). Empty string otherwise.
    _pen_fmt, _int_fmt = "", ""
    _saldo_ci = profile.get("saldo_capital_inicial") or profile.get("balance")
    _dov = int(profile.get("days_overdue") or 0)
    if _saldo_ci is not None and _dov > 0:
        try:
            from features.cobranza.scenario import (  # noqa: PLC0415
                calcular_interes_compensatorio,
                calcular_penalidad,
            )
            from loguru import logger as _log  # noqa: PLC0415

            _rate = profile.get("penalidad_rate_per_week")
            _rounding = profile.get("penalidad_rounding", "ceil_decimo")
            if _rate is None:
                _log.warning(
                    "templates: penalidad_rate_per_week not in profile; "
                    "falling back to engine default 0.00008"
                )
                _rate = 0.00008
            _pen_fmt = _money(
                calcular_penalidad(
                    float(_saldo_ci), _dov,
                    rate_per_week=float(_rate),
                    rounding=str(_rounding),
                ),
                sym,
            )
            _am = profile.get("amortizacion_cuota")
            _ta = profile.get("tasa_interes_mensual")
            if _am is not None and _ta is not None:
                _int_fmt = _money(
                    calcular_interes_compensatorio(float(_am), float(_ta), _dov), sym
                )
        except Exception:  # noqa: BLE001
            pass
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
        "inversionista": profile.get("inversionista") or "",
        "cuenta_bancaria": profile.get("cuenta_bancaria") or "",
        # Overdue aggregates (Slice E) — filled from profile; 0 when al día.
        "monto_vencido": _money(monto_vencido, sym),
        "cuotas_vencidas": str(cuotas_vencidas),
        # Schedule aggregates (INF-02/INF-03) — from the Doris profile (Fase 3).
        "cuotas_pagadas": "" if _cp is None else str(_cp),
        "cuotas_pendientes": "" if _cpe is None else str(_cpe),
        "fecha_venc_contrato": profile.get("fecha_venc_contrato") or "",
        # Moratoria (INF-12) — empty unless vencido with the required fields.
        "penalidad": _pen_fmt,
        "interes_compensatorio": _int_fmt,
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
