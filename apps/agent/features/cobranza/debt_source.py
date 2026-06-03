"""Debt-source dispatcher — selects the backend per tenant.

Tenants declare ``"data_source"`` in their ``tenant.config.json``:
  - ``"mock"``  (default) → ``mock_debt_source`` (JSON fixture; e.g. prestaunion)
  - ``"doris"``           → ``doris_debt_source`` (real Doris, fixture fallback;
                            e.g. prestamype)

This is intentionally thin: it reads the tenant's ``data_source`` and forwards
``resolve_token`` / ``resolve_dni`` to the right module, keeping the SAME
interface so callers (ToolRegistry, api/main.py) don't branch. Backward
compatible: unknown / missing tenants resolve to ``mock``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from features.cobranza import mock_debt_source


def _tenants_root() -> Path:
    """Locate the tenants/ directory in both Docker and local-dev layouts."""
    docker_path = Path("/app/tenants")
    if docker_path.exists():
        return docker_path
    # apps/agent/features/cobranza/ -> repo root -> tenants/
    return Path(__file__).resolve().parent.parent.parent.parent.parent / "tenants"


@lru_cache(maxsize=16)
def _data_source(tenant_id: str) -> str:
    """Read ``data_source`` from the tenant config. Defaults to ``mock``."""
    path = _tenants_root() / tenant_id / "tenant.config.json"
    if not path.exists():
        return "mock"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "mock"
    return (config.get("data_source") or "mock").strip().lower()


def _backend(tenant_id: str):
    """Return the debt-source module for the tenant."""
    if _data_source(tenant_id) == "doris":
        from features.cobranza import doris_debt_source  # local import (lazy driver)

        return doris_debt_source
    return mock_debt_source


def resolve_token(token: str, tenant_id: str = "prestaunion") -> dict | None:
    """Resolve a campaign token to a borrower profile via the tenant backend."""
    return _backend(tenant_id).resolve_token(token, tenant_id=tenant_id)


def resolve_dni(dni: str, tenant_id: str = "prestaunion") -> dict | None:
    """Resolve a DNI to a borrower profile via the tenant backend."""
    return _backend(tenant_id).resolve_dni(dni, tenant_id=tenant_id)
