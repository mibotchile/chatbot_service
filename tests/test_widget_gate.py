"""Unit tests for the publishable-key gate (Slice A, tasks A-1 + A-2).

TDD: written RED-first before widget_gate.py exists.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# A-2: resolve_tenant_by_pk
# ---------------------------------------------------------------------------

class TestResolveTenantByPk:
    """resolve_tenant_by_pk scans all tenant configs and returns the slug."""

    def test_known_key_single_entry(self, monkeypatch):
        from api.deps.widget_gate import resolve_tenant_by_pk
        import shared.config.cors as cors_mod

        # Inject a fake tenants-root so we don't hit the real FS.
        import tempfile, json
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "acme"
            t.mkdir()
            (t / "tenant.config.json").write_text(
                json.dumps({
                    "id": "acme",
                    "publishable_keys": [
                        {"key": "pk_live_abc123", "status": "current", "added": "2026-01-01"}
                    ],
                    "embed_origins": ["https://acme.example.com"],
                }),
                encoding="utf-8",
            )
            monkeypatch.setattr(cors_mod, "_tenants_root", lambda: Path(tmp))
            from api.deps import widget_gate
            monkeypatch.setattr(widget_gate, "_tenants_root", lambda: Path(tmp))

            result = resolve_tenant_by_pk("pk_live_abc123")
            assert result == "acme"

    def test_unknown_key_returns_none(self, monkeypatch):
        from api.deps.widget_gate import resolve_tenant_by_pk
        import shared.config.cors as cors_mod

        import tempfile, json
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "acme"
            t.mkdir()
            (t / "tenant.config.json").write_text(
                json.dumps({
                    "id": "acme",
                    "publishable_keys": [
                        {"key": "pk_live_abc123", "status": "current", "added": "2026-01-01"}
                    ],
                }),
                encoding="utf-8",
            )
            from api.deps import widget_gate
            monkeypatch.setattr(widget_gate, "_tenants_root", lambda: Path(tmp))

            result = resolve_tenant_by_pk("pk_live_UNKNOWN")
            assert result is None

    def test_dual_key_both_accepted(self, monkeypatch):
        """current + previous keys must both resolve to the same tenant."""
        from api.deps.widget_gate import resolve_tenant_by_pk

        import tempfile, json
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "acme"
            t.mkdir()
            (t / "tenant.config.json").write_text(
                json.dumps({
                    "id": "acme",
                    "publishable_keys": [
                        {"key": "pk_live_newkey", "status": "current", "added": "2026-06-01"},
                        {"key": "pk_live_oldkey", "status": "previous", "added": "2026-01-01"},
                    ],
                }),
                encoding="utf-8",
            )
            from api.deps import widget_gate
            monkeypatch.setattr(widget_gate, "_tenants_root", lambda: Path(tmp))

            assert resolve_tenant_by_pk("pk_live_newkey") == "acme"
            assert resolve_tenant_by_pk("pk_live_oldkey") == "acme"

    def test_legacy_scalar_key_accepted(self, monkeypatch):
        """Legacy configs with publishable_key (scalar, not list) still work."""
        from api.deps.widget_gate import resolve_tenant_by_pk

        import tempfile, json
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "legacy"
            t.mkdir()
            (t / "tenant.config.json").write_text(
                json.dumps({
                    "id": "legacy",
                    "publishable_key": "pk_live_legacyscalar",
                }),
                encoding="utf-8",
            )
            from api.deps import widget_gate
            monkeypatch.setattr(widget_gate, "_tenants_root", lambda: Path(tmp))

            result = resolve_tenant_by_pk("pk_live_legacyscalar")
            assert result == "legacy"


# ---------------------------------------------------------------------------
# A-1: require_publishable_key dependency
# ---------------------------------------------------------------------------

def _make_scope(tenant_configs: list[dict]) -> tuple:
    """Build a temp tenants dir from a list of tenant config dicts."""
    import tempfile, json
    from pathlib import Path

    tmp = tempfile.mkdtemp()
    for cfg in tenant_configs:
        slug = cfg["id"]
        d = Path(tmp) / slug
        d.mkdir(exist_ok=True)
        (d / "tenant.config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return tmp


class TestRequirePublishableKey:
    """require_publishable_key factory — FastAPI dependency logic."""

    @pytest.fixture(autouse=True)
    def _patch_tenants(self, monkeypatch, tmp_path):
        """Patch _tenants_root in widget_gate to use a controlled tmp dir."""
        import json
        from pathlib import Path
        from api.deps import widget_gate

        t = tmp_path / "acme"
        t.mkdir()
        (t / "tenant.config.json").write_text(
            json.dumps({
                "id": "acme",
                "publishable_keys": [
                    {"key": "pk_live_valid", "status": "current", "added": "2026-01-01"}
                ],
                "embed_origins": ["https://acme.example.com", "http://localhost:*"],
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(widget_gate, "_tenants_root", lambda: tmp_path)

    def _run_dep(self, *, pk: str | None, origin: str | None, allow_no_key: bool = False):
        """Call the dependency directly (not via HTTP) and return the result or raise."""
        import asyncio
        from types import SimpleNamespace
        from api.deps.widget_gate import require_publishable_key

        class _Headers(dict):
            """dict subclass that supports .get() override."""
            pass

        headers = _Headers()
        if pk is not None:
            headers["X-Publishable-Key"] = pk
        if origin is not None:
            headers["Origin"] = origin

        request = SimpleNamespace(
            headers=headers,
            state=SimpleNamespace(),
            url=SimpleNamespace(path="/api/v1/test"),
        )

        dep_fn = require_publishable_key(allow_no_key=allow_no_key)
        return asyncio.run(dep_fn(request))

    def test_valid_key_and_allowlisted_origin_passes(self):
        # Should complete without raising HTTPException
        result = self._run_dep(pk="pk_live_valid", origin="https://acme.example.com")
        assert result == "acme"

    def test_valid_key_localhost_wildcard_origin_passes(self):
        result = self._run_dep(pk="pk_live_valid", origin="http://localhost:3000")
        assert result == "acme"

    def test_missing_key_raises_403(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._run_dep(pk=None, origin="https://acme.example.com")
        assert exc_info.value.status_code == 403

    def test_invalid_key_raises_403(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._run_dep(pk="pk_live_BOGUS", origin="https://acme.example.com")
        assert exc_info.value.status_code == 403

    def test_non_allowlisted_origin_raises_403(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._run_dep(pk="pk_live_valid", origin="https://attacker.example.com")
        assert exc_info.value.status_code == 403

    def test_allow_no_key_missing_key_passes(self):
        """allow_no_key=True: missing header is allowed."""
        # Should not raise; origin check not triggered without a pk
        result = self._run_dep(pk=None, origin="https://acme.example.com", allow_no_key=True)
        assert result is None  # no tenant resolved

    def test_allow_no_key_present_but_bad_key_still_403(self):
        """allow_no_key only relaxes MISSING header; a present-but-invalid key must 403."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._run_dep(pk="pk_live_BOGUS", origin="https://acme.example.com", allow_no_key=True)
        assert exc_info.value.status_code == 403
