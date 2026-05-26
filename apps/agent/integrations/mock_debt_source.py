"""Mock debt source for the cobranza DEMO.

Resolves a borrower profile from ``tenants/<tenant>/mock/borrowers.json`` by:
  - campaign token (e.g. ``demo-juan``)  — pre-identified link, or
  - DNI (8 digits)                       — DNI-first identification flow.

This replaces the real read-only debt API for the demo. There is NO database
and NO network call — everything is read from a JSON fixture with 100%
fictitious data. The ``account_id`` is ALWAYS resolved server-side from the
profile, never dictated by the LLM.

NOTE (demo): DNI is treated as a SINGLE identification factor. TODO production:
require a 2nd factor (OTP / código del aviso) before opening the debt gate.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path


def _tenants_root() -> Path:
    """Locate the tenants/ directory in both Docker and local-dev layouts."""
    docker_path = Path("/app/tenants")
    if docker_path.exists():
        return docker_path
    # apps/agent/integrations/ -> repo root -> tenants/
    return Path(__file__).resolve().parent.parent.parent.parent / "tenants"


@lru_cache(maxsize=8)
def _load_mock(tenant_id: str) -> dict:
    """Load (and cache) the borrowers fixture for a tenant. Empty dict if absent."""
    path = _tenants_root() / tenant_id / "mock" / "borrowers.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_token(token: str, tenant_id: str = "prestaunion") -> dict | None:
    """Resolve a demo campaign token to a borrower profile.

    Returns the full borrower profile dict (including ``account_id``) when the
    token is valid, or ``None`` when it is unknown. The caller stores this in
    the ConversationState as the verified ``debt_context``.
    """
    if not token:
        return None
    data = _load_mock(tenant_id)
    account_id = (data.get("tokens") or {}).get(token.strip())
    if not account_id:
        return None
    profile = (data.get("borrowers") or {}).get(account_id)
    return dict(profile) if profile else None


def get_borrower(account_id: str, tenant_id: str = "prestaunion") -> dict | None:
    """Look up a borrower profile by account_id (server-side resolution)."""
    if not account_id:
        return None
    data = _load_mock(tenant_id)
    profile = (data.get("borrowers") or {}).get(account_id)
    return dict(profile) if profile else None


def _normalize_dni(dni: str) -> str:
    """Keep only digits — tolerates spaces/dots the user might type."""
    return re.sub(r"\D", "", dni or "")


def resolve_dni(dni: str, tenant_id: str = "prestaunion") -> dict | None:
    """Resolve a DNI (8-digit document number) to a borrower profile.

    DNI-first identification: the user types their DNI; the lookup happens
    server-side against the fixture's ``dni`` field. Returns the full profile
    (incl. ``account_id``) when found, else ``None``. Single factor (demo only).
    """
    norm = _normalize_dni(dni)
    if len(norm) != 8:
        return None
    for profile in (_load_mock(tenant_id).get("borrowers") or {}).values():
        if _normalize_dni(profile.get("dni", "")) == norm:
            return dict(profile)
    return None
