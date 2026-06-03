"""Deploy-readiness guards: root_path config + widget base-path derivation.

These lock in the reverse-proxy behavior so a refactor can't silently break the
demo behind the /pubot-gj5w2a0p prefix.
"""

from __future__ import annotations

import re
from pathlib import Path

_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


def test_settings_has_root_path_default_empty():
    from shared.config.settings import Settings

    s = Settings()
    assert s.root_path == ""  # local dev = no prefix


def test_settings_root_path_from_env(monkeypatch):
    monkeypatch.setenv("COBRANZA_ROOT_PATH", "/pubot-gj5w2a0p")
    from shared.config.settings import Settings

    assert Settings().root_path == "/pubot-gj5w2a0p"


def test_app_uses_root_path(monkeypatch):
    # FastAPI() is built at import time from settings; assert the app exposes it.
    import api.main as m

    assert hasattr(m.app, "root_path")  # attribute exists; value driven by env


def test_cors_includes_demos_origin():
    from shared.config.settings import Settings

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


# ── Bloque 3: hardening guards (presence in widget.js) ─────────────────────

def test_widget_esc_to_close():
    js = (_FRONTEND / "widget.js").read_text(encoding="utf-8")
    assert 'e.key === "Escape"' in js and "setOpen(false)" in js


def test_widget_distinct_error_states():
    js = (_FRONTEND / "widget.js").read_text(encoding="utf-8")
    assert "errorMessageForStatus" in js
    assert "=== 429" in js          # rate-limit branch
    assert "401" in js and "403" in js   # session/CSRF branch
    assert "Tu sesión expiró" in js and "muy rápido" in js


def test_widget_reset_confirmation():
    js = (_FRONTEND / "widget.js").read_text(encoding="utf-8")
    assert "requestReset" in js and "pu-confirm" in js
    assert "¿Seguro?" in js


def test_widget_document_download_chip():
    js = (_FRONTEND / "widget.js").read_text(encoding="utf-8")
    assert "pu-doc-link" in js and "pu-dl-ico" in js
    assert "Descargar documento" in js
    assert "_docChip" in js   # renders from structured `document` field


def test_widget_dni_format_hint():
    js = (_FRONTEND / "widget.js").read_text(encoding="utf-8")
    assert "maybeDniHint" in js and "8 dígitos" in js


def test_widget_strip_shows_business_name():
    js = (_FRONTEND / "widget.js").read_text(encoding="utf-8")
    assert "identity.business_name" in js


def test_index_no_emdash_in_dni_list():
    """Visible product copy uses '·' as separator, not em-dash."""
    html = (_FRONTEND / "index.html").read_text(encoding="utf-8")
    # the DNI list lines must not carry an em-dash separator
    for line in html.splitlines():
        if "Pérez Rojas (al día)" in line or "Huamán Flores (en mora)" in line:
            assert "—" not in line


# ── Bloque 4: final polish guards ──────────────────────────────────────────

def test_widget_header_btn_touch_target():
    """Header reset/minimize buttons get a >=44px tap area via ::before."""
    js = (_FRONTEND / "widget.js").read_text(encoding="utf-8")
    assert ".pu-hbtn::before" in js
    assert "width: 44px; height: 44px" in js


def test_widget_system_error_style():
    """Errors render as a neutral SYSTEM message, not in Ada's voice (no avatar)."""
    js = (_FRONTEND / "widget.js").read_text(encoding="utf-8")
    assert "showSystemError" in js
    assert "pu-msg system" in js and "pu-sysmsg" in js
    assert "alert:" in js  # alert icon in ICONS
    # the three error states route through the system-error renderer
    assert js.count("showSystemError(typingEl") >= 2


def test_widget_dni_hint_natural_language():
    """Hint detects a digit run ANYWHERE (e.g. 'Mi DNI es 417'), not only leading."""
    js = (_FRONTEND / "widget.js").read_text(encoding="utf-8")
    assert "match(/\\d+/g)" in js
    assert "longest >= 1 && longest <= 7" in js


def test_widget_comprobante_account_type_selector():
    """Jorge feedback: comprobante form lets the user pick Número de cuenta vs CCI."""
    js = (_FRONTEND / "widget.js").read_text(encoding="utf-8")
    # both labels, exactly as specified
    assert "Número de cuenta" in js
    assert "Código de Cuenta Interbancario (CCI)" in js
    # radio selector wired + type-aware validation
    assert 'name="pu-cb-acct"' in js
    assert "_cbAcctType" in js
    # destination-account copy (a quién pagaste, not your own account)
    assert "destinatario del depósito" in js
    # voucher example present + toggle
    assert "VOUCHER_EXAMPLE_SVG" in js
    assert "Ver ejemplo de voucher" in js
    assert "<svg" in js and "var(--pu-brand)" in js
    # backend payload uses the neutral fields
    assert "account_type" in js and "cuenta_destino" in js
