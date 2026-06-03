"""Per-tenant CORS allowlist → a single origin-matching regex.

The widget is embeddable on a client's own website (Intercom/Drift style). For
the browser to let that cross-origin page call the cobranza API, the client's
origin must be allowed by CORS. We can't use ``*`` because the widget sends
credentials (cookies for CSRF) — ``allow_credentials=True`` forbids ``*`` and
requires echoing the *exact* requesting Origin.

So we build an allowlist = global origins (settings.cors_origins, always
includes https://demos.mibot.cl) ∪ every tenant's ``embed_origins`` (declared in
``tenants/<id>/tenant.config.json``), compile it into ONE regex, and hand it to
Starlette's CORSMiddleware via ``allow_origin_regex``. Starlette then echoes the
matching Origin back (correct for credentialed requests) and handles preflight.

Origin patterns:
  · An exact origin   ``https://prestamype.com``  → matched literally.
  · A port wildcard   ``http://localhost:*``       → any port on that host
    (lets the demo owner test from any local dev server). The ``*`` ONLY stands
    in for the port; the scheme + host are still exact. ``http://localhost``
    (no port) is allowed too.
A pattern may contain at most one trailing ``:*``. Everything else is escaped,
so a tenant can't smuggle a permissive regex into the union.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def _tenants_root() -> Path:
    """Locate the tenants directory in both Docker (/app/tenants) and dev."""
    docker = Path("/app/tenants")
    if docker.exists():
        return docker
    # apps/agent/shared/config/cors.py → repo root is 5 levels up.
    return Path(__file__).resolve().parent.parent.parent.parent.parent / "tenants"


def _origin_pattern(origin: str) -> str | None:
    """Compile ONE origin spec to a regex fragment, or None if it's malformed.

    Supports a single trailing ``:*`` port wildcard (``http://localhost:*``).
    Everything else is regex-escaped so it can only match itself.
    """
    origin = (origin or "").strip().rstrip("/")
    if not origin:
        return None
    if origin.endswith(":*"):
        host = origin[:-2]
        if not host:
            return None
        # exact scheme+host, then an OPTIONAL :port (so :* also covers no-port).
        return re.escape(host) + r"(:\d+)?"
    return re.escape(origin)


def collect_embed_origins() -> list[str]:
    """Read ``embed_origins`` from every tenant.config.json (best-effort).

    A missing/invalid file or key is skipped silently — a bad tenant config must
    never widen or break CORS for the others.
    """
    out: list[str] = []
    root = _tenants_root()
    if not root.exists():
        return out
    for cfg_path in root.glob("*/tenant.config.json"):
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for o in cfg.get("embed_origins") or []:
            if isinstance(o, str) and o.strip():
                out.append(o.strip())
    return out


def build_cors_origin_regex(global_origins: list[str]) -> str:
    """Build the ``allow_origin_regex`` from global ∪ all tenants' embed origins.

    Returns an anchored alternation regex (deduped). The global origins are
    always included so https://demos.mibot.cl keeps working regardless of tenant
    config.
    """
    seen: set[str] = set()
    frags: list[str] = []
    for origin in [*(global_origins or []), *collect_embed_origins()]:
        frag = _origin_pattern(origin)
        if frag and frag not in seen:
            seen.add(frag)
            frags.append(frag)
    if not frags:
        # Degenerate (no origins) — match nothing rather than everything.
        return r"(?!)"
    return r"^(?:" + "|".join(frags) + r")$"
