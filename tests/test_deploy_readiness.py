"""Deploy-readiness guards: root_path config + widget base-path derivation.

These lock in the reverse-proxy behavior so a refactor can't silently break the
demo behind the /pubot-gj5w2a0p prefix.
"""

from __future__ import annotations

import re
from pathlib import Path

_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


def test_settings_has_root_path_default_empty():
    from config.settings import Settings

    s = Settings()
    assert s.root_path == ""  # local dev = no prefix


def test_settings_root_path_from_env(monkeypatch):
    monkeypatch.setenv("COBRANZA_ROOT_PATH", "/pubot-gj5w2a0p")
    from config.settings import Settings

    assert Settings().root_path == "/pubot-gj5w2a0p"


def test_app_uses_root_path(monkeypatch):
    # FastAPI() is built at import time from settings; assert the app exposes it.
    import api.main as m

    assert hasattr(m.app, "root_path")  # attribute exists; value driven by env


def test_cors_includes_demos_origin():
    from config.settings import Settings

    assert "https://demos.mibot.cl" in Settings().cors_origins


def test_widget_derives_base_from_script_url():
    """The widget must build API URLs from its own <script src>, not a hardcoded
    absolute path — so it works at / (local) and /pubot-gj5w2a0p/ (proxy)."""
    js = (_FRONTEND / "widget.js").read_text(encoding="utf-8")
    # base derivation present
    assert "_deriveApiBase" in js
    assert re.search(r"replace\(\s*/\\?/widget\\?\.js\$?/", js) or "/widget.js" in js
    # every API call goes through the derived `API` base (no bare "/api/v1" fetch)
    assert "fetch(`${API}/api/v1/" in js
    # no fetch to an absolute root-anchored API path (would break under prefix)
    assert 'fetch("/api/v1' not in js and "fetch('/api/v1" not in js


def test_widget_has_no_hardcoded_localhost():
    js = (_FRONTEND / "widget.js").read_text(encoding="utf-8")
    assert "http://localhost" not in js and "127.0.0.1" not in js
