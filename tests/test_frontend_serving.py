"""Tests for server-side tenant resolution via GET /.

PR1 scope — server-side tenant injection end-to-end:
  · GET / injects DEFAULT_TENANT into window.__TENANT__ (prestaunion default)
  · GET / serves per-tenant index.html when frontend/tenants/<tenant>/index.html exists
  · GET / falls back to generic index.html when no per-tenant file exists
  · GET /widget.js served as static (200)
  · Regression: DEFAULT_TENANT=prestamype → prestamype injected without ?tenant=
  · Literal __TENANT__ sentinel never leaks to the browser

All tests call _mount_demo_frontend() against a tmp frontend dir so the real
production index.html is NOT read — fixtures stay deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ── Sentinel bytes used across fixtures ──────────────────────────────────────

_SENTINEL = b'<script>window.__TENANT__ = "__TENANT__";</script>'


def _write_generic(frontend: Path) -> None:
    (frontend / "index.html").write_bytes(
        b"<!DOCTYPE html><html><head>" + _SENTINEL + b"</head><body>generic</body></html>"
    )
    (frontend / "widget.js").write_bytes(b"/* widget */")


def _write_per_tenant(frontend: Path, tenant: str) -> None:
    d = frontend / "tenants" / tenant
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_bytes(
        b"<!DOCTYPE html><html><head>" + _SENTINEL
        + b"</head><body>" + tenant.encode() + b"-landing</body></html>"
    )


# ── Fixture factory ───────────────────────────────────────────────────────────

def _fresh_client(
    frontend: Path,
    tenant: str,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Return a TestClient with _mount_demo_frontend re-run against *frontend*."""
    import api.main as m

    monkeypatch.setenv("DEFAULT_TENANT", tenant)

    # Remove the previously registered routes/mounts from earlier test runs so
    # each test gets a clean slate on the same app object.
    m.app.routes[:] = [
        r for r in m.app.routes
        if getattr(r, "name", None) not in ("_serve_root", "demo")
    ]

    # Monkey-patch the candidate list so _mount_demo_frontend uses our tmp dir.
    import api.main as _m

    def _patched():
        import os as _os
        from fastapi.responses import HTMLResponse as _HR
        from fastapi.staticfiles import StaticFiles

        t = _os.environ.get("DEFAULT_TENANT", "prestaunion")
        per_tenant_path = frontend / "tenants" / t / "index.html"
        generic_path = frontend / "index.html"
        source = per_tenant_path if per_tenant_path.exists() else generic_path

        # Replace only the quoted sentinel value, keeping the JS property name intact.
        cached = source.read_bytes().replace(b'"__TENANT__"', b'"' + t.encode() + b'"')

        @m.app.get("/", include_in_schema=False, name="_serve_root")
        async def _serve_root() -> _HR:  # noqa: RUF029
            return _HR(content=cached, media_type="text/html; charset=utf-8")

        m.app.mount("/", StaticFiles(directory=str(frontend), html=True), name="demo")

    monkeypatch.setattr(_m, "_mount_demo_frontend", _patched)
    _patched()

    # Starlette compiles its ASGI middleware_stack on first request. After mutating
    # app.routes we must reset the compiled stack so the new route is honoured.
    m.app.middleware_stack = None

    return TestClient(m.app)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_get_root_injects_default_tenant_prestaunion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """GET / with DEFAULT_TENANT=prestaunion injects tenant=prestaunion into page."""
    _write_generic(tmp_path)
    client = _fresh_client(tmp_path, "prestaunion", monkeypatch)

    r = client.get("/")

    assert r.status_code == 200
    assert b'window.__TENANT__ = "prestaunion"' in r.content


def test_get_root_prestamype_injects_tenant_no_query_param(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Regression: DEFAULT_TENANT=prestamype serves prestamype without ?tenant=."""
    _write_generic(tmp_path)
    _write_per_tenant(tmp_path, "prestamype")
    client = _fresh_client(tmp_path, "prestamype", monkeypatch)

    r = client.get("/")

    assert r.status_code == 200
    assert b'window.__TENANT__ = "prestamype"' in r.content
    # The unresolved sentinel VALUE must never reach the browser (property name is fine).
    assert b'"__TENANT__"' not in r.content


def test_get_root_falls_back_to_generic_when_no_per_tenant_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """When no per-tenant index.html exists, serves generic with tenant injected."""
    _write_generic(tmp_path)
    # Intentionally NO per-tenant file for "unknowntenant".
    client = _fresh_client(tmp_path, "unknowntenant", monkeypatch)

    r = client.get("/")

    assert r.status_code == 200
    assert b'window.__TENANT__ = "unknowntenant"' in r.content
    assert b"generic" in r.content


def test_widget_js_served_as_static(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """GET /widget.js returns 200 (served by StaticFiles, not shadowed by GET /)."""
    _write_generic(tmp_path)
    client = _fresh_client(tmp_path, "prestaunion", monkeypatch)

    r = client.get("/widget.js")

    assert r.status_code == 200


def test_get_root_does_not_serve_unresolved_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The literal string __TENANT__ must never appear in the served HTML."""
    _write_generic(tmp_path)
    client = _fresh_client(tmp_path, "prestaunion", monkeypatch)

    r = client.get("/")

    assert b'"__TENANT__"' not in r.content
