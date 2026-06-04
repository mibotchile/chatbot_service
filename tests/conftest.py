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


@pytest.fixture(autouse=True)
def _force_mock_data_source(monkeypatch):
    """Tests run against the deterministic seeded fixture, never live Doris.

    Production tenants (e.g. prestamype) set ``data_source: "doris"`` in their
    tenant.config.json, which the test env has no live Doris to satisfy. We force
    ``_data_source`` to "mock" so the end-to-end flow resolves against the fixture
    (the pre-prod-flip behaviour the suite was written against). Doris-specific
    behaviour is covered separately by tests that call ``doris_debt_source``
    directly — they bypass ``_data_source``/``_backend`` and are unaffected.
    """
    try:
        import features.cobranza.debt_source as _dsrc
        monkeypatch.setattr(_dsrc, "_data_source", lambda tenant_id="": "mock")
    except Exception:
        pass
    yield
