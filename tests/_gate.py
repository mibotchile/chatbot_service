"""Shared test helper for the widget publishable-key gate.

Reads a tenant's CURRENT publishable key from its committed tenant.config.json
so tests stay correct across key rotations (no hard-coded key literals).
"""

from __future__ import annotations

import json
from pathlib import Path

_TENANTS_DIR = Path(__file__).resolve().parent.parent / "tenants"

GATE_ORIGIN = "https://demos.mibot.cl"


def current_pk(tenant: str) -> str:
    """Return the `status == "current"` publishable key for *tenant*."""
    cfg = json.loads((_TENANTS_DIR / tenant / "tenant.config.json").read_text())
    keys = cfg.get("publishable_keys", [])
    return next(
        (k["key"] for k in keys if k.get("status") == "current"),
        keys[0]["key"] if keys else "",
    )
