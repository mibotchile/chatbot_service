"""Shared pytest fixtures.

The hardened rate limiter (``api.main.rate_limiter``) is a module-level
singleton with per-IP state. Under TestClient every request shares the same
``testclient`` source IP, so without isolation one test's requests would burn
another test's budget (upload/hour, DNI sweep, chat/min). Reset it before each
test so endpoint tests stay independent of execution order.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Clear the global rate-limiter state before every test."""
    try:
        from api.main import rate_limiter
    except Exception:
        yield
        return
    rate_limiter.reset()
    yield
    rate_limiter.reset()
