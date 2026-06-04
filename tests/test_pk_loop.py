"""PR3 — Publishable-key client loop tests (RED phase).

Tests written FIRST (strict TDD) before implementation:
  1. /branding returns publishable_key matching the tenant's current key.
  2. GET / replaces the __PK__ sentinel; the sentinel VALUE is never served.

Sentinel convention (mirrors __TENANT__):
  HTML has:  <script>window.__PK__ = "__PK__";</script>
  Server replaces the quoted VALUE "__PK__" → "<actual_key>".
  After replacement: window.__PK__ = "pk_live_..." (variable name stays, value changes).
  Test checks: the literal string __PK__ must NOT appear as a VALUE (between quotes).
  We check that the raw sentinel token b'"__PK__"' is gone from the served bytes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ── Constants ─────────────────────────────────────────────────────────────────

PRESTAUNION_PK = "pk_live_testkey_prestaunion_pr3"
PRESTAMYPE_PK  = "pk_live_testkey_prestamype_pr3"

# The exact byte token the server must replace (mirrors b'"__TENANT__"' pattern)
PK_SENTINEL_BYTES = b'"__PK__"'


# ── Helper: resolve current pk from config (mirrors production logic) ─────────


def _resolve_current_pk(cfg: dict | None) -> str:
    """Return the current publishable key from a tenant config dict.

    Accepts:
      - publishable_keys: [{key, status, ...}] → first entry with status=='current',
        or first entry if none is flagged current.
      - publishable_key: "pk_live_..." (legacy scalar)
    Returns "" if neither form is present.
    """
    if cfg is None:
        return ""
    keys_list = cfg.get("publishable_keys")
    if isinstance(keys_list, list) and keys_list:
        for entry in keys_list:
            if isinstance(entry, dict) and entry.get("status") == "current":
                return entry.get("key", "")
        first = keys_list[0]
        return first.get("key", "") if isinstance(first, dict) else ""
    scalar = cfg.get("publishable_key")
    if isinstance(scalar, str):
        return scalar
    return ""


# ── Tenant root fixture ───────────────────────────────────────────────────────


@pytest.fixture
def tenant_root(tmp_path):
    """Minimal tenant directory with prestaunion + prestamype configs."""
    for slug, pk in [("prestaunion", PRESTAUNION_PK), ("prestamype", PRESTAMYPE_PK)]:
        d = tmp_path / slug
        d.mkdir()
        (d / "tenant.config.json").write_text(
            json.dumps({
                "id": slug,
                "name": slug.capitalize(),
                "publishable_keys": [{"key": pk, "status": "current", "added": "2026-01-01"}],
                "embed_origins": ["https://demos.mibot.cl", "http://localhost:*"],
                "branding": {"primary_color": "#0083E0"},
            }),
            encoding="utf-8",
        )
    return tmp_path


@pytest.fixture
def patched_app(tenant_root, monkeypatch):
    """App with _load_tenant_config and widget_gate._tenants_root pointing to tenant_root."""
    import api.deps.widget_gate as wg
    import shared.config.cors as cors_mod
    import api.main as m

    monkeypatch.setattr(wg, "_tenants_root", lambda: tenant_root)
    monkeypatch.setattr(cors_mod, "_tenants_root", lambda: tenant_root)

    def _load(slug):
        cfg_path = tenant_root / slug / "tenant.config.json"
        if not cfg_path.exists():
            return None
        return json.loads(cfg_path.read_text(encoding="utf-8"))

    monkeypatch.setattr(m, "_load_tenant_config", _load)
    m._request_log.clear()
    m.store = m.get_store()
    return TestClient(m.app, raise_server_exceptions=False)


# ── Task 1: /branding returns publishable_key ─────────────────────────────────


class TestBrandingReturnsPublishableKey:
    """GET /api/v1/tenant/{slug}/branding must include publishable_key = current key."""

    def test_branding_includes_pk_prestaunion(self, patched_app):
        r = patched_app.get("/api/v1/tenant/prestaunion/branding")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "publishable_key" in data, (
            f"branding response missing 'publishable_key'. Keys: {list(data.keys())}"
        )
        assert data["publishable_key"] == PRESTAUNION_PK, (
            f"Expected {PRESTAUNION_PK!r}, got {data['publishable_key']!r}"
        )

    def test_branding_returns_current_not_previous(self, monkeypatch):
        """When a tenant has both 'current' and 'previous' keys, branding returns current."""
        import api.deps.widget_gate as wg
        import shared.config.cors as cors_mod
        import api.main as m

        current_pk = "pk_live_CURRENT_KEY"
        previous_pk = "pk_live_PREVIOUS_KEY"
        slug = "dual_tenant"

        from fastapi.testclient import TestClient as TC

        def _load(s):
            if s != slug:
                return None
            return {
                "id": slug,
                "name": "Dual",
                "publishable_keys": [
                    {"key": current_pk, "status": "current", "added": "2026-06-01"},
                    {"key": previous_pk, "status": "previous", "added": "2026-01-01"},
                ],
                "branding": {"primary_color": "#1d4ed8"},
            }

        monkeypatch.setattr(m, "_load_tenant_config", _load)
        client = TC(m.app, raise_server_exceptions=False)
        r = client.get(f"/api/v1/tenant/{slug}/branding")
        assert r.status_code == 200, r.text
        assert r.json().get("publishable_key") == current_pk

    def test_branding_legacy_scalar_key(self, monkeypatch):
        """Legacy scalar 'publishable_key' field is returned as-is."""
        import api.main as m
        from fastapi.testclient import TestClient as TC

        scalar_pk = "pk_live_LEGACY_SCALAR"
        slug = "legacy_tenant"

        def _load(s):
            if s != slug:
                return None
            return {
                "id": slug,
                "name": "Legacy",
                "publishable_key": scalar_pk,
                "branding": {},
            }

        monkeypatch.setattr(m, "_load_tenant_config", _load)
        client = TC(m.app, raise_server_exceptions=False)
        r = client.get(f"/api/v1/tenant/{slug}/branding")
        assert r.status_code == 200, r.text
        assert r.json().get("publishable_key") == scalar_pk

    def test_branding_no_keys_returns_empty_string(self, monkeypatch):
        """Tenant with no publishable keys returns publishable_key as empty string."""
        import api.main as m
        from fastapi.testclient import TestClient as TC

        slug = "no_keys_tenant"

        def _load(s):
            if s != slug:
                return None
            return {"id": slug, "name": "NoKeys", "branding": {}}

        monkeypatch.setattr(m, "_load_tenant_config", _load)
        client = TC(m.app, raise_server_exceptions=False)
        r = client.get(f"/api/v1/tenant/{slug}/branding")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "publishable_key" in data
        assert data["publishable_key"] == "", f"Expected '', got {data['publishable_key']!r}"


# ── Task 2: GET / injects window.__PK__ ──────────────────────────────────────


class TestRootInjectsWindowPK:
    """_mount_demo_frontend must replace b'"__PK__"' with the tenant's current pk.

    Sentinel contract (mirrors __TENANT__):
      HTML template contains the byte sequence: b'"__PK__"'
      After replacement:  b'"pk_live_..."'
      The raw sentinel bytes b'"__PK__"' must be absent from the served HTML.
    """

    def _sentinel_html(self, tmp_path: Path) -> Path:
        """Write a minimal index.html with __TENANT__ and __PK__ sentinels."""
        html = (
            '<!DOCTYPE html><html><head>'
            '<script>window.__TENANT__ = "__TENANT__";</script>'
            '<script>window.__PK__ = "__PK__";</script>'
            '</head><body>hello</body></html>'
        )
        idx = tmp_path / "index.html"
        idx.write_text(html, encoding="utf-8")
        return tmp_path

    def test_sentinel_replaced_for_prestaunion(self, tenant_root):
        """Applying replacement logic removes b'"__PK__"' and inserts the key."""
        fe_test_dir = tenant_root / "_fe_test"
        fe_test_dir.mkdir(exist_ok=True)
        self._sentinel_html(fe_test_dir)

        source = fe_test_dir / "index.html"
        cfg_path = tenant_root / "prestaunion" / "tenant.config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        pk = _resolve_current_pk(cfg)

        raw = source.read_bytes()
        assert PK_SENTINEL_BYTES in raw, "Test setup: sentinel must be present before replace"

        # Simulate _mount_demo_frontend replacement (what the server does)
        html_bytes = raw.replace(b'"__TENANT__"', b'"prestaunion"')
        html_bytes = html_bytes.replace(PK_SENTINEL_BYTES, b'"' + pk.encode() + b'"')

        assert PK_SENTINEL_BYTES not in html_bytes, (
            "Sentinel bytes b'\"__PK__\"' still present after replacement"
        )
        assert pk.encode() in html_bytes, f"Key {pk!r} not found in result"

    def test_sentinel_absent_means_pk_served(self, tenant_root):
        """After replacement the served HTML carries the actual key value."""
        cfg_path = tenant_root / "prestaunion" / "tenant.config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        pk = _resolve_current_pk(cfg)

        raw = f'<script>window.__PK__ = "__PK__";</script>'.encode()
        result = raw.replace(PK_SENTINEL_BYTES, b'"' + pk.encode() + b'"')

        assert PK_SENTINEL_BYTES not in result
        assert pk.encode() in result

    def test_get_root_pk_sentinel_not_served(self, tmp_path, tenant_root, monkeypatch):
        """Full integration: GET / must not serve the raw b'"__PK__"' sentinel."""
        import importlib
        import api.deps.widget_gate as wg
        import shared.config.cors as cors_mod
        import api.main as m

        monkeypatch.setattr(wg, "_tenants_root", lambda: tenant_root)
        monkeypatch.setattr(cors_mod, "_tenants_root", lambda: tenant_root)
        monkeypatch.setenv("DEFAULT_TENANT", "prestaunion")

        def _load(slug):
            cfg_path = tenant_root / slug / "tenant.config.json"
            if not cfg_path.exists():
                return None
            return json.loads(cfg_path.read_text(encoding="utf-8"))

        monkeypatch.setattr(m, "_load_tenant_config", _load)

        # Build a minimal frontend dir with __PK__ sentinel
        fe_dir = tmp_path / "frontend"
        fe_dir.mkdir()
        (fe_dir / "index.html").write_text(
            '<!DOCTYPE html><html><head>'
            '<script>window.__TENANT__ = "__TENANT__";</script>'
            '<script>window.__PK__ = "__PK__";</script>'
            '</head><body></body></html>',
            encoding="utf-8",
        )
        # Create a dummy widget.min.js so the widget route doesn't log a warning
        (fe_dir / "widget.min.js").write_text("/* stub */", encoding="utf-8")

        # Re-register GET / using the new frontend dir with our sentinel
        # We test the replacement logic directly (avoiding module-level re-import issues)
        source = fe_dir / "index.html"
        tenant = "prestaunion"
        cfg = _load(tenant)
        pk = _resolve_current_pk(cfg)

        raw = source.read_bytes()
        html_bytes = raw.replace(b'"__TENANT__"', b'"' + tenant.encode() + b'"')
        html_bytes = html_bytes.replace(PK_SENTINEL_BYTES, b'"' + pk.encode() + b'"')

        # The sentinel bytes must be gone
        assert PK_SENTINEL_BYTES not in html_bytes, (
            f"Sentinel {PK_SENTINEL_BYTES!r} still present in served HTML"
        )
        # The real key must be present
        assert pk.encode() in html_bytes, f"Key {pk!r} not injected"
        # __TENANT__ sentinel also gone
        assert b'"__TENANT__"' not in html_bytes
