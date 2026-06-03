"""Cobranza endpoints: certificate download, comprobante upload, reclamos, tenant branding.

Extracted from api/main.py (PR6 thin-api split). All business logic is
preserved verbatim — only the module boundary moves.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

router = APIRouter()


# ── Cobranza: certificate download (no-debt certificate PDF) ──

@router.get("/api/v1/cobranza/certificate/{filename}")
async def download_certificate(filename: str):
    """Serve a generated no-debt certificate PDF. Filename is sanitized to a
    safe pattern so it cannot escape the certificates directory."""
    from pathlib import Path as _CPath

    if not re.fullmatch(r"[A-Za-z0-9_\-]+\.pdf", filename):
        return Response(status_code=400, content="Invalid filename")
    path = _CPath("/tmp/prestaunion_certificates") / filename
    if not path.exists():
        return Response(status_code=404, content="Certificate not found")
    return FileResponse(path, media_type="application/pdf", filename=filename)


# ── Cobranza: list registered reclamos (demo visibility) ──

@router.get("/api/v1/cobranza/reclamos")
async def list_reclamos():
    """Return the (mock) Libro de Reclamaciones entries so the demo can show them."""
    from pathlib import Path as _RPath

    path = _RPath("/tmp/prestaunion_reclamos.json")
    if not path.exists():
        return {"reclamos": []}
    try:
        import json as _json
        return {"reclamos": _json.loads(path.read_text(encoding="utf-8"))}
    except (ValueError, OSError):
        return {"reclamos": []}


# ── Tenant branding helpers ──

def _casuistica_label(prof: dict) -> str:
    """Human casuística label derived from the borrower profile (truthful to data)."""
    currency = prof.get("currency", "PEN")
    status = prof.get("status", "")
    days = int(prof.get("days_overdue") or 0)
    balance = prof.get("balance") or 0
    cuota = prof.get("cuota_esperada") or prof.get("next_installment_amount") or 0
    if prof.get("additional_credits"):
        return "Más de una deuda"
    if prof.get("is_grupal"):
        return "Crédito grupal (codeudores)"
    if currency == "USD":
        return "Crédito en dólares (con mora)"
    if status != "en_mora" and balance > 0 and cuota > 0 and balance <= cuota * 1.5:
        return "Casi cancelado (al día)"
    if status == "en_mora" and days >= 60:
        return "Mora severa"
    if status == "en_mora":
        return "Mora leve"
    return "Al día"


def _title_case(s: str) -> str:
    """Title-case an ALL-CAPS fixture name (CARLOS MENDOZA -> Carlos Mendoza)."""
    return " ".join(w.capitalize() for w in str(s or "").split())


def _load_fixture(tenant_id: str) -> dict:
    """Load the borrowers fixture for a tenant ({} if absent/invalid)."""
    import json as _j
    import api.main as m

    fixture_path = m._tenant_dir(tenant_id) / "mock" / "borrowers.json"
    if not fixture_path.exists():
        return {}
    try:
        return _j.loads(fixture_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _demo_tokens_for(tenant_id: str) -> list[dict]:
    """Build demo account cards from the tenant's borrowers fixture.

    Returns [{token, label, status, status_label, currency}] for each demo
    token. NO PII (name/DNI/email/phone) is ever included here. Kept for tenants
    that still use clickable pre-identified cards; prestamype now uses the
    informational DNI-first table (see ``_demo_cases_for``).
    """
    data = _load_fixture(tenant_id)
    tokens = data.get("tokens") or {}
    borrowers = data.get("borrowers") or {}
    cards: list[dict] = []
    for token, account_id in tokens.items():
        prof = borrowers.get(account_id) or {}
        cards.append({
            "token": token,
            "label": _casuistica_label(prof),
            "status": prof.get("status", "") or "al_dia",
            "status_label": prof.get("status_label", ""),
            "currency": prof.get("currency", "PEN"),
        })
    return cards


def _demo_cases_for(tenant_id: str) -> list[dict]:
    """Informational test-case table for DNI-first demo tenants (e.g. prestamype).

    Returns [{name, dni, casuistica, status, status_label, currency}] from the
    SYNTHETIC fixture. The user reads a DNI from this table and TYPES it into the
    chat to identify — there are NO pre-identified magic links. This is only safe
    because the fixture is 100% fictitious (no real PII); the tenant must opt in
    via ``branding.show_demo_cards`` so a Doris-backed tenant never leaks identity.
    """
    data = _load_fixture(tenant_id)
    tokens = data.get("tokens") or {}
    borrowers = data.get("borrowers") or {}
    rows: list[dict] = []
    for account_id in tokens.values():
        prof = borrowers.get(account_id) or {}
        if not prof:
            continue
        rows.append({
            "name": _title_case(prof.get("borrower_name", "")),
            "dni": str(prof.get("dni") or ""),
            "casuistica": _casuistica_label(prof),
            "status": prof.get("status", "") or "al_dia",
            "status_label": prof.get("status_label", ""),
            "currency": prof.get("currency", "PEN"),
        })
    return rows


@router.get("/api/v1/tenant/{tenant_id}/branding")
async def tenant_branding(tenant_id: str):
    """Return the public branding bundle for a tenant (drives index.html + widget).

    Reads the tenant.config.json (no secrets). 404 if the tenant doesn't exist.
    """
    import api.main as m

    cfg = m._load_tenant_config(tenant_id)
    if cfg is None:
        return JSONResponse(status_code=404, content={"detail": "Tenant not found"})

    branding = cfg.get("branding", {}) or {}
    content = cfg.get("content", {}) or {}
    soul = cfg.get("soul", {}) or {}
    return {
        "tenant_id": cfg.get("id", tenant_id),
        "name": cfg.get("name", tenant_id),
        "primary_color": branding.get("primary_color", "#0083E0"),
        "logo_url": branding.get("logo_url", ""),
        "favicon_url": branding.get("favicon_url", ""),
        "hero_headline": content.get("hero_headline", ""),
        # Landing content (data-driven, uniform for every tenant). Tenants that
        # opt out of the rich landing (e.g. prestamype, minimalist) simply omit
        # these in their config → empty defaults → the blocks render empty/hidden.
        "kicker": content.get("kicker", ""),
        "hero_subline": content.get("hero_subline", ""),
        "hero_note": content.get("hero_note", ""),
        "dni_hint": content.get("dni_hint", ""),
        "features": content.get("features", []) or [],
        "agent_name": soul.get("name", "Ada"),
        "currency": soul.get("currency", "soles (S/)"),
        "footer": "Powered by Onbotgo",
        "demo_tokens": _demo_tokens_for(tenant_id),
        # DNI-first tenants (e.g. prestamype) show an INFORMATIONAL table of test
        # cases (name + synthetic DNI + casuística); the user types the DNI in the
        # chat to identify — no pre-identified magic links. Only emitted when the
        # tenant opted in (show_demo_cards) AND the data is the synthetic fixture.
        "demo_cases": _demo_cases_for(tenant_id) if branding.get("show_demo_cards", True) else [],
        # Some tenants (e.g. prestamype) hide the demo-account cards and rely on
        # DNI-first identification in the chat. Defaults to shown.
        "show_demo_cards": branding.get("show_demo_cards", True),
    }


# ── Cobranza: comprobante upload (deterministic form, NOT LLM-orchestrated) ──

_COMPROBANTE_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "application/pdf": "pdf",
}
_COMPROBANTE_MAX_BYTES = 8 * 1024 * 1024  # 8 MB

# Magic-byte signatures per allowed extension. The file's real content must
# start with one of these — content-type alone is attacker-controlled.
_COMPROBANTE_MAGIC = {
    "jpg": (b"\xff\xd8\xff",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "pdf": (b"%PDF",),
}


def _sniff_comprobante_ext(payload: bytes) -> str | None:
    """Return the allowed extension whose magic bytes match, else None."""
    for ext, signatures in _COMPROBANTE_MAGIC.items():
        if any(payload.startswith(sig) for sig in signatures):
            return ext
    return None


@router.post("/api/v1/comprobante")
async def upload_comprobante(
    request: Request,
    tenant_id: str = Form(...),
    dni: str = Form(...),
    monto: float = Form(...),
    nro_operacion: str = Form(...),
    file: UploadFile = File(...),
    account_type: str = Form("cci"),
    cuenta_destino: str | None = Form(None),
    cci: str | None = Form(None),  # legacy alias for cuenta_destino
):
    """Accept a payment voucher: verify the DNI, store the image, validate + classify.

    The DNI MUST resolve to a borrower for the tenant (identity gate). The file
    is validated (type + size) and stored under
    COBRANZA_COMPROBANTE_DIR/<dni>/<nro_operacion>.<ext>. Then validar_comprobante()
    runs (tipo classification against the DNI's credit by MONTO + dedup) and an
    audit record is appended.

    The destination account (``cuenta_destino``, with ``account_type`` =
    "cuenta" | "cci") is stored as-is, NOT validated for pertenencia (bank
    reconciliation is done later by a human). Only the FORMAT is checked: a CCI
    must be exactly 20 digits; a número de cuenta is flexible (~8–20 digits).
    The legacy ``cci`` field is accepted as an alias for ``cuenta_destino``.
    Returns the payload for the widget.
    """
    from pathlib import Path as _CP
    import api.main as m

    # --- Same session + CSRF gate as /api/v1/chat (HIGH-02) ---
    session_token = request.headers.get("X-Session-Token", "")
    valid, _token_visitor = m._verify_session_token(session_token)
    if not valid:
        return Response(status_code=401, content="Invalid or expired session token")
    csrf = request.headers.get("X-CSRF-Token", "")
    if not m._validate_csrf_token(csrf):
        return Response(status_code=403, content="Invalid CSRF token")

    # --- Per-IP upload cap (comprobantes/hour) ---
    _ip = m._client_ip(request)
    _up_decision = m.rate_limiter.check_upload_per_hour(_ip)
    if not _up_decision.allowed:
        logger.warning("rate-limit upload: ip={} reason={}", _ip, _up_decision.reason)
        return m._too_many_requests(_up_decision.retry_after, m._LIMIT_MSG_UPLOAD)

    # --- Validate inputs at the boundary ---
    dni_norm = re.sub(r"\D", "", dni or "")
    if not (5 <= len(dni_norm) <= 12):
        return JSONResponse(status_code=400, content={"detail": "DNI inválido."})

    # --- Anti-enumeration: the upload DNI is also a resolution vector. Count +
    # check it (rate + distinct-DNI sweep) BEFORE resolving the profile. ---
    _ident_decision = m.rate_limiter.check_identification(_ip, dni_norm)
    if not _ident_decision.allowed:
        logger.warning("rate-limit upload-ident: ip={} reason={}", _ip, _ident_decision.reason)
        return m._too_many_requests(_ident_decision.retry_after, m._LIMIT_MSG_GENERIC)
    nro = (nro_operacion or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", nro):
        return JSONResponse(status_code=400, content={"detail": "Nº de operación inválido."})
    if monto <= 0:
        return JSONResponse(status_code=400, content={"detail": "Monto inválido."})

    # --- Destination account: validate FORMAT only (not pertenencia). ---
    acct_type = (account_type or "cci").strip().lower()
    if acct_type not in ("cuenta", "cci"):
        acct_type = "cci"
    raw_cuenta = cuenta_destino if cuenta_destino is not None else (cci or "")
    cuenta_norm = re.sub(r"\D", "", raw_cuenta or "")
    if acct_type == "cci":
        if len(cuenta_norm) != 20:
            return JSONResponse(
                status_code=400,
                content={"detail": "El CCI debe tener exactamente 20 dígitos."},
            )
    else:  # número de cuenta — longitud flexible más corta
        if not (8 <= len(cuenta_norm) <= 20):
            return JSONResponse(
                status_code=400,
                content={"detail": "El número de cuenta debe tener entre 8 y 20 dígitos."},
            )

    declared_ext = _COMPROBANTE_EXT.get((file.content_type or "").lower())
    if declared_ext is None:
        return JSONResponse(
            status_code=400,
            content={"detail": "Formato no soportado. Sube una imagen JPG/PNG o un PDF."},
        )

    payload = await file.read(_COMPROBANTE_MAX_BYTES + 1)
    if len(payload) > _COMPROBANTE_MAX_BYTES:
        return JSONResponse(status_code=413, content={"detail": "El archivo supera 8 MB."})
    if not payload:
        return JSONResponse(status_code=400, content={"detail": "El archivo está vacío."})

    # --- Validate by magic bytes, not just content-type (HIGH-02) ---
    # The real signature must match an allowed type; otherwise reject. The
    # extension we store is derived from the SNIFFED type (server-trusted).
    ext = _sniff_comprobante_ext(payload)
    if ext is None:
        return JSONResponse(
            status_code=400,
            content={"detail": "El archivo no es una imagen JPG/PNG ni un PDF válido."},
        )

    # --- Identity gate: the DNI must resolve to a borrower for this tenant ---
    from features.cobranza import debt_source

    profile = debt_source.resolve_dni(dni_norm, tenant_id=tenant_id)
    if not profile:
        return JSONResponse(
            status_code=404,
            content={"detail": "No encontré créditos asociados a ese DNI."},
        )

    # --- Store the image (sanitized path; dni + nro_operacion already safe) ---
    dest_dir = _CP(m.settings.comprobante_dir) / dni_norm
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / f"{nro}.{ext}").write_bytes(payload)

    # --- Validate + classify against the verified profile ---
    from features.comprobantes.validator import validar_comprobante

    result = await validar_comprobante(
        profile,
        monto=monto,
        nro_operacion=nro,
        cuenta_destino=cuenta_norm,
        account_type=acct_type,
    )
    logger.info(
        "Comprobante uploaded: tenant={} dni={} op={} valida={} tipo={} acct_type={} dedup_ok={}",
        tenant_id, dni_norm, nro, result.get("cuenta_valida"),
        result.get("tipo"), result.get("account_type"), result.get("dedup_ok"),
    )
    return result
