"""Mock debt source for the cobranza DEMO.

Maps a demo campaign token (e.g. ``demo-juan``) to a fictitious borrower
profile loaded from ``tenants/<tenant>/mock/borrowers.json``.

This replaces the real read-only debt API for the demo. There is NO database
and NO network call — everything is read from a JSON fixture with 100%
fictitious data. The token IS the identity (same contract as the design doc:
the borrower never types PII, and ``account_id`` is resolved server-side).
"""

from __future__ import annotations

import json
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
