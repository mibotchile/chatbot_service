"""Publishable-key gate: FastAPI dependency for widget API routes.

Validates X-Publishable-Key header + Origin/Referer against per-tenant
publishable_keys and embed_origins config. Composes alongside existing
CSRF + session-token checks (does not replace them).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from fastapi import HTTPException, Request
from loguru import logger

from shared.config.cors import _origin_pattern, _tenants_root


def resolve_tenant_by_pk(pk: str) -> str | None:
    """Scan all tenant configs and return the slug for a matching publishable key.

    Accepts:
      - publishable_keys: [{key, status, added}, ...] (list form, current or previous)
      - publishable_key: "pk_live_..." (legacy scalar form, treated as one current entry)

    Returns the tenant slug (config ``id``) or None if not found.
    """
    if not pk:
        return None
    root = _tenants_root()
    if not root.exists():
        return None
    for cfg_path in root.glob("*/tenant.config.json"):
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue

        # List form: publishable_keys: [{key, status, added}, ...]
        keys_list = cfg.get("publishable_keys")
        if isinstance(keys_list, list):
            for entry in keys_list:
                if isinstance(entry, dict) and entry.get("key") == pk:
                    return cfg.get("id") or cfg.get("slug") or cfg_path.parent.name

        # Legacy scalar form: publishable_key: "pk_live_..."
        scalar = cfg.get("publishable_key")
        if isinstance(scalar, str) and scalar == pk:
            return cfg.get("id") or cfg.get("slug") or cfg_path.parent.name

    return None


def _origin_allowed(tenant_slug: str, origin: str) -> bool:
    """Check whether origin matches the tenant's embed_origins list.

    Reuses _origin_pattern from cors.py to keep gate logic and CORS regex
    in sync — single source of truth for pattern compilation.
    """
    import re

    if not origin:
        return False
    root = _tenants_root()
    cfg_path = root / tenant_slug / "tenant.config.json"
    if not cfg_path.exists():
        return False
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False

    for raw in cfg.get("embed_origins") or []:
        if not isinstance(raw, str):
            continue
        raw = raw.strip()
        if not raw or raw == "*":
            continue
        frag = _origin_pattern(raw)
        if frag and re.fullmatch(frag, origin):
            return True
    return False


def require_publishable_key(*, allow_no_key: bool = False) -> Callable:
    """Factory that returns a FastAPI dependency enforcing the publishable-key gate.

    Args:
        allow_no_key: When True, a *missing* X-Publishable-Key header is allowed
            (bootstrap routes like /branding, /csrf-token). A *present but invalid*
            key still returns 403. Origin check is skipped when no key is present.

    The dependency sets ``request.state.tenant_slug`` on success so downstream
    handlers can read the resolved tenant without re-scanning configs.

    Raises:
        HTTPException(403): missing key (when allow_no_key=False), unrecognized key,
            or non-allowlisted origin.
    """

    async def _dep(request: Request) -> str | None:
        pk = request.headers.get("X-Publishable-Key")

        if pk is None:
            if allow_no_key:
                return None
            logger.warning("widget-gate: missing X-Publishable-Key on {}", request.url.path)
            raise HTTPException(status_code=403, detail="Missing publishable key")

        tenant_slug = resolve_tenant_by_pk(pk)
        if tenant_slug is None:
            logger.warning("widget-gate: unrecognized pk on {}", request.url.path)
            raise HTTPException(status_code=403, detail="Invalid publishable key")

        # Origin check — prefer Origin header, fall back to Referer host
        origin = request.headers.get("Origin") or ""
        if not origin:
            referer = request.headers.get("Referer") or ""
            # Extract scheme+host from Referer (strip path)
            if referer:
                from urllib.parse import urlparse
                parsed = urlparse(referer)
                if parsed.scheme and parsed.netloc:
                    origin = f"{parsed.scheme}://{parsed.netloc}"

        if not _origin_allowed(tenant_slug, origin):
            logger.warning(
                "widget-gate: origin '{}' not allowed for tenant '{}' on {}",
                origin, tenant_slug, request.url.path,
            )
            raise HTTPException(status_code=403, detail="Origin not allowed")

        request.state.tenant_slug = tenant_slug
        return tenant_slug

    return _dep
