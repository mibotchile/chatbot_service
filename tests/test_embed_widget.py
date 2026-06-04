"""Tests for the embeddable widget feature.

Covers the backend-testable parts:
  · build_cors_origin_regex — global ∪ tenant embed_origins, port wildcards,
    no-`*`-with-credentials, malformed specs rejected.
  · CORS behavior end-to-end via TestClient — an allowed tenant origin gets the
    Access-Control-Allow-Origin echo; a disallowed one does not.
  · embed.js is served by the static mount (200, JS content-type).
  · /widget.js → 302 redirect to the current versioned path.
  · /widget/<version>/widget.min.js → 200, immutable Cache-Control.
  · /widget/<unknown>/widget.min.js → 404.

The Shadow DOM / FAB rendering is visual and verified in the browser, not here.

widget.min.js for tests:
  The real minified file is produced by the esbuild Docker build stage (node:20-slim
  + esbuild). For local test runs, frontend/widget.min.js is a committed stub that
  contains the public API strings ("PubotWidget", "attachShadow") so route tests
  can verify the response body contract without running Node/esbuild.
  The stub is intentionally tiny — it is NOT the production artifact.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from shared.config.cors import build_cors_origin_regex


# ── Origin regex builder (unit) ─────────────────────────────────────────────

def test_regex_matches_exact_global_origin():
    rx = re.compile(build_cors_origin_regex(["https://demos.mibot.cl"]))
    assert rx.match("https://demos.mibot.cl")
    assert not rx.match("https://evil.mibot.cl")
    # No partial / suffix matches (anchored).
    assert not rx.match("https://demos.mibot.cl.evil.com")


def test_regex_port_wildcard_localhost():
    rx = re.compile(build_cors_origin_regex(["http://localhost:*"]))
    assert rx.match("http://localhost:3000")
    assert rx.match("http://localhost:8080")
    assert rx.match("http://localhost")          # :* also covers no-port
    assert not rx.match("https://localhost:3000")  # scheme is exact
    assert not rx.match("http://localhost.evil")   # host is exact


def test_regex_unions_tenant_embed_origins(monkeypatch):
    # The prestamype tenant declares embed_origins in its config; the union must
    # include them on top of the global list.
    rx = re.compile(build_cors_origin_regex(["https://demos.mibot.cl"]))
    assert rx.match("https://demos.mibot.cl")
    assert rx.match("http://localhost:5173")     # from tenants/prestamype config
    assert rx.match("http://127.0.0.1:5500")


def test_regex_rejects_unknown_origin():
    rx = re.compile(build_cors_origin_regex(["https://demos.mibot.cl"]))
    assert not rx.match("https://prestamype.com")  # not configured yet (prod TODO)
    assert not rx.match("https://attacker.example")


def test_regex_never_matches_everything_when_empty(monkeypatch):
    # Degenerate input (no globals AND no tenant origins) must match NOTHING,
    # never collapse to a permissive regex.
    import shared.config.cors as cors

    monkeypatch.setattr(cors, "collect_embed_origins", lambda: [])
    rx = re.compile(cors.build_cors_origin_regex([]))
    assert not rx.match("https://anything.example")
    assert not rx.match("https://demos.mibot.cl")


def test_regex_does_not_let_a_tenant_inject_regex(tmp_path, monkeypatch):
    # A tenant origin containing regex metacharacters is escaped, so it can only
    # match itself — it cannot become a wildcard.
    import shared.config.cors as cors

    monkeypatch.setattr(cors, "collect_embed_origins", lambda: ["https://.*"])
    rx = re.compile(cors.build_cors_origin_regex(["https://demos.mibot.cl"]))
    assert not rx.match("https://anything")        # ".*" was escaped
    assert rx.match("https://.*")                  # only the literal matches


# ── CORS behavior end-to-end ────────────────────────────────────────────────

@pytest.fixture
def client():
    import api.main as m

    m._request_log.clear()
    m.store = m.get_store()
    return TestClient(m.app)


def test_cors_allows_tenant_localhost_origin(client):
    # A localhost dev origin (declared in the prestamype embed_origins) is echoed
    # back on a simple request → the browser would allow the cross-origin call.
    origin = "http://localhost:5173"
    r = client.get("/api/v1/tenant/prestamype/branding", headers={"Origin": origin})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == origin
    assert r.headers.get("access-control-allow-credentials") == "true"


def test_cors_allows_demos_origin(client):
    origin = "https://demos.mibot.cl"
    r = client.get("/api/v1/tenant/prestamype/branding", headers={"Origin": origin})
    assert r.headers.get("access-control-allow-origin") == origin


def test_cors_rejects_unconfigured_origin(client):
    # A random external origin is NOT echoed → the browser blocks the call.
    r = client.get(
        "/api/v1/tenant/prestamype/branding",
        headers={"Origin": "https://attacker.example"},
    )
    # The request itself still returns 200 (CORS is a browser-enforced header
    # contract), but the allow-origin header must be absent.
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_cors_preflight_allowed_origin(client):
    origin = "http://localhost:3000"
    r = client.options(
        "/api/v1/chat",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == origin


def test_cors_preflight_rejected_origin(client):
    r = client.options(
        "/api/v1/chat",
        headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    # Starlette returns 400 for a disallowed preflight origin.
    assert r.status_code == 400


# ── Static assets served (loader + widget) ──────────────────────────────────

def test_embed_js_is_served(client):
    r = client.get("/embed.js")
    assert r.status_code == 200
    assert "javascript" in r.headers.get("content-type", "")
    # The loader's public contract: it calls PubotWidget.mount with a shadowRoot.
    assert "PubotWidget" in r.text
    assert "attachShadow" in r.text


def test_widget_js_redirects_to_versioned_url(client):
    # /widget.js is the legacy alias — it must 302-redirect to the current
    # versioned path so existing embed snippets keep working.
    # TestClient follows redirects by default; disable to inspect the 302.
    r = client.get("/widget.js", follow_redirects=False)
    assert r.status_code == 302
    location = r.headers.get("location", "")
    assert re.match(r"^/widget/.+/widget\.min\.js$", location), (
        f"Expected Location matching /widget/<version>/widget.min.js, got: {location!r}"
    )


def test_versioned_widget_served(client):
    # GET /widget/<current-version>/widget.min.js → 200, immutable cache,
    # application/javascript, body contains the public API surface.
    import os
    version = os.environ.get("WIDGET_VERSION", "dev")
    r = client.get(f"/widget/{version}/widget.min.js")
    assert r.status_code == 200
    assert "javascript" in r.headers.get("content-type", "")
    cache_control = r.headers.get("cache-control", "")
    assert "immutable" in cache_control, f"Cache-Control missing 'immutable': {cache_control!r}"
    assert "max-age=31536000" in cache_control, f"Cache-Control missing max-age: {cache_control!r}"
    # Public API contract: minified file preserves PubotWidget global + shadow usage.
    assert "PubotWidget" in r.text
    assert "attachShadow" in r.text


def test_unknown_widget_version_returns_404(client):
    # A version string that does not match the deployed version → 404.
    r = client.get("/widget/9.9.9-unknown/widget.min.js")
    assert r.status_code == 404


def test_embed_demo_page_served(client):
    r = client.get("/embed-demo.html")
    assert r.status_code == 200
    assert "embed.js" in r.text
    assert "data-tenant" in r.text
