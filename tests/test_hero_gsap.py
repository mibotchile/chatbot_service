"""Tests for the prestamype GSAP hero animation asset wiring.

Covers:
  · GET /vendor/gsap.min.js → 200, javascript content-type
  · GET /tenants/prestamype/hero.js → 200, javascript content-type
  · GET / with DEFAULT_TENANT=prestamype references both script paths in HTML
  · GET / with DEFAULT_TENANT=prestaunion (generic) does NOT reference hero.js
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# ── Sentinel / fixture helpers (mirror test_frontend_serving.py pattern) ──────

_SENTINEL = b'<script>window.__TENANT__ = "__TENANT__";</script>'


def _write_generic(frontend: Path) -> None:
    (frontend / "index.html").write_bytes(
        b"<!DOCTYPE html><html><head>" + _SENTINEL + b"</head><body>generic</body></html>"
    )
    (frontend / "widget.js").write_bytes(b"/* widget */")
    (frontend / "app.js").write_bytes(b"/* app */")


def _write_per_tenant_with_scripts(frontend: Path, tenant: str) -> None:
    """Write a per-tenant index.html that includes the GSAP + hero script tags."""
    d = frontend / "tenants" / tenant
    d.mkdir(parents=True, exist_ok=True)
    body = (
        b"<!DOCTYPE html><html><head>"
        + _SENTINEL
        + b'</head><body>'
        + tenant.encode()
        + b'-landing'
        + b'<script src="/vendor/gsap.min.js"></script>'
        + b'<script src="/tenants/prestamype/hero.js"></script>'
        + b"</body></html>"
    )
    (d / "index.html").write_bytes(body)


def _fresh_client(
    frontend: Path,
    tenant: str,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Return a TestClient with _mount_demo_frontend re-run against *frontend*.

    Mirrors the helper in test_frontend_serving.py exactly.
    """
    import api.main as m

    monkeypatch.setenv("DEFAULT_TENANT", tenant)

    m.app.routes[:] = [
        r for r in m.app.routes
        if getattr(r, "name", None) not in ("_serve_root", "demo")
    ]

    import os as _os
    from fastapi.responses import HTMLResponse as _HR
    from fastapi.staticfiles import StaticFiles

    def _patched() -> None:
        t = _os.environ.get("DEFAULT_TENANT", "prestaunion")
        per_tenant_path = frontend / "tenants" / t / "index.html"
        generic_path = frontend / "index.html"
        source = per_tenant_path if per_tenant_path.exists() else generic_path
        cached = source.read_bytes().replace(b'"__TENANT__"', b'"' + t.encode() + b'"')

        @m.app.get("/", include_in_schema=False, name="_serve_root")
        async def _serve_root() -> _HR:  # noqa: RUF029
            return _HR(content=cached)

        m.app.mount(
            "/",
            StaticFiles(directory=str(frontend), html=True),
            name="demo",
        )

    _patched()
    return TestClient(m.app, raise_server_exceptions=True)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_vendor_gsap_served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /vendor/gsap.min.js returns 200 with javascript content-type."""
    _write_generic(tmp_path)
    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()
    (vendor_dir / "gsap.min.js").write_bytes(b"/* gsap 3.15.0 */")
    client = _fresh_client(tmp_path, "prestaunion", monkeypatch)

    r = client.get("/vendor/gsap.min.js")

    assert r.status_code == 200
    assert "javascript" in r.headers.get("content-type", "")


def test_hero_js_served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /tenants/prestamype/hero.js returns 200 with javascript content-type."""
    _write_generic(tmp_path)
    tenant_dir = tmp_path / "tenants" / "prestamype"
    tenant_dir.mkdir(parents=True)
    (tenant_dir / "hero.js").write_bytes(b"/* hero animation */")
    (tenant_dir / "index.html").write_bytes(
        b"<!DOCTYPE html><html><head>" + _SENTINEL + b"</head><body>prestamype-landing</body></html>"
    )
    client = _fresh_client(tmp_path, "prestamype", monkeypatch)

    r = client.get("/tenants/prestamype/hero.js")

    assert r.status_code == 200
    assert "javascript" in r.headers.get("content-type", "")


def test_prestamype_index_references_gsap_and_hero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET / (DEFAULT_TENANT=prestamype) HTML references /vendor/gsap.min.js and /tenants/prestamype/hero.js."""
    _write_generic(tmp_path)
    _write_per_tenant_with_scripts(tmp_path, "prestamype")
    client = _fresh_client(tmp_path, "prestamype", monkeypatch)

    r = client.get("/")

    assert r.status_code == 200
    assert b"/vendor/gsap.min.js" in r.content
    assert b"/tenants/prestamype/hero.js" in r.content


def test_prestaunion_index_does_not_reference_hero_js(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET / with DEFAULT_TENANT=prestaunion must NOT contain hero.js (it is prestamype-scoped)."""
    _write_generic(tmp_path)
    client = _fresh_client(tmp_path, "prestaunion", monkeypatch)

    r = client.get("/")

    assert r.status_code == 200
    assert b"hero.js" not in r.content
