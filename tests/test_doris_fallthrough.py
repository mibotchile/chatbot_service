"""TDD tests for Doris fall-through fix and per-tenant fixture fallback flag.

Tasks 2.3 (RED), 2.4 (GREEN) — fall-through fix in _resolve_dni_credits
Tasks 2.5 (RED), 2.6 (GREEN) — _allow_fixture_fallback flag reader

Spec:
  - Doris reachable + zero rows → return []  (fixture NOT called)
  - Doris reachable + rows → return mapped profiles (fixture NOT called)
  - Doris raises Exception + allow_fixture_fallback=True  → fixture consulted
  - Doris raises Exception + allow_fixture_fallback=False → return [] (fail-closed)
  - _allow_fixture_fallback: prestamype → False, prestaunion → True, unknown → False

lru_cache handling: each test calls _allow_fixture_fallback.cache_clear() before
and after to prevent state bleed between tests that toggle the flag.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from features.cobranza import doris_debt_source as dds


# ── Helpers ──────────────────────────────────────────────────────────────────

# Minimal Doris row — enough for _row_to_profile to work.
_SAMPLE_ROW = {
    "account_id": "P99999",
    "loan_number": "P99999",
    "borrower_name": "JUAN DEMO",
    "dni": "12345678",
    "email": "juan@demo.com",
    "phone": "999000111",
    "principal_original": 5000.0,
    "days_overdue": 10,
    "next_due_date": "2026-07-01",
    "currency": "SOLES",
    "banco": "BCP",
    "cci": "00200100001234567890",
    "inversionista": "INV DEMO",
    "cuota_esperada": 500.0,
    "next_installment_amount": 500.0,
    "saldo_por_cancelar": 4500.0,
    "balance": 4500.0,
}

TENANT = "prestamype"


class _FakeConn:
    """Fake Doris connection returning a configurable row list."""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)

    def close(self):
        pass


class _FakeCursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        pass

    def fetchall(self):
        return self._rows


def _patch_connect(monkeypatch, rows: list[dict]):
    """Make _connect return a fake connection yielding ``rows``."""
    monkeypatch.setattr(dds, "_connect", lambda db: _FakeConn(rows))


def _patch_connect_raise(monkeypatch, exc: Exception):
    """Make _connect raise ``exc`` (simulates Doris down)."""
    def _boom(db):
        raise exc
    monkeypatch.setattr(dds, "_connect", _boom)


# ── 2a. Doris OK + zero rows → [] (fixture NOT consulted) ────────────────────

def test_doris_ok_empty_returns_empty_list(monkeypatch):
    """Doris responds but finds no rows for this DNI → return [], no fixture."""
    fixture_calls: list[str] = []

    def _fake_mock_resolve(dni, *, tenant_id):
        fixture_calls.append(dni)
        return {"account_id": "FIXTURE"}

    monkeypatch.setattr(dds.mock_debt_source, "resolve_dni", _fake_mock_resolve)
    dds._load_schema.cache_clear()
    _patch_connect(monkeypatch, rows=[])  # Doris returns empty

    result = dds._resolve_dni_credits("12345678", TENANT)

    assert result == [], f"Expected [] when Doris returns no rows, got {result!r}"
    assert fixture_calls == [], (
        f"Fixture must NOT be called when Doris is reachable but returns no rows. "
        f"Got calls: {fixture_calls}"
    )


# ── 2b. Doris OK + rows → mapped profiles (fixture NOT consulted) ─────────────

def test_doris_ok_with_rows_returns_mapped_profiles(monkeypatch):
    """Doris returns a row → mapped profile returned, fixture NOT consulted."""
    fixture_calls: list[str] = []

    def _fake_mock_resolve(dni, *, tenant_id):
        fixture_calls.append(dni)
        return {"account_id": "FIXTURE"}

    monkeypatch.setattr(dds.mock_debt_source, "resolve_dni", _fake_mock_resolve)
    dds._load_schema.cache_clear()
    _patch_connect(monkeypatch, rows=[_SAMPLE_ROW])

    result = dds._resolve_dni_credits("12345678", TENANT)

    assert len(result) == 1, f"Expected 1 profile, got {result!r}"
    assert result[0]["account_id"] == "P99999"
    assert fixture_calls == [], (
        f"Fixture must NOT be called when Doris returns rows. Got: {fixture_calls}"
    )


# ── 2c. Doris raises Exception + flag=True → fixture consulted ────────────────

def test_doris_exception_with_flag_true_falls_back_to_fixture(monkeypatch):
    """Doris raises + allow_fixture_fallback=True → fixture profile returned."""
    fixture_calls: list[str] = []

    def _fake_mock_resolve(dni, *, tenant_id):
        fixture_calls.append(dni)
        return {"account_id": "FIXTURE", "dni": dni}

    monkeypatch.setattr(dds.mock_debt_source, "resolve_dni", _fake_mock_resolve)
    dds._load_schema.cache_clear()
    _patch_connect_raise(monkeypatch, ConnectionError("Doris down"))

    # prestaunion has allow_fixture_fallback=true
    dds._allow_fixture_fallback.cache_clear()
    result = dds._resolve_dni_credits("12345678", "prestaunion")
    dds._allow_fixture_fallback.cache_clear()

    assert fixture_calls != [], "Fixture must be consulted when Doris raises + flag=True"
    assert len(result) == 1
    assert result[0]["account_id"] == "FIXTURE"


# ── 2d. Doris raises Exception + flag=False → [] (fail-closed) ───────────────

def test_doris_exception_with_flag_false_returns_empty(monkeypatch):
    """Doris raises + allow_fixture_fallback=False → [], fixture NOT consulted."""
    fixture_calls: list[str] = []

    def _fake_mock_resolve(dni, *, tenant_id):
        fixture_calls.append(dni)
        return {"account_id": "FIXTURE"}

    monkeypatch.setattr(dds.mock_debt_source, "resolve_dni", _fake_mock_resolve)
    dds._load_schema.cache_clear()
    _patch_connect_raise(monkeypatch, ConnectionError("Doris down"))

    # prestamype has allow_fixture_fallback=false
    dds._allow_fixture_fallback.cache_clear()
    result = dds._resolve_dni_credits("12345678", "prestamype")
    dds._allow_fixture_fallback.cache_clear()

    assert result == [], (
        f"Expected [] when Doris raises + flag=False (fail-closed), got {result!r}"
    )
    assert fixture_calls == [], (
        f"Fixture must NOT be consulted when flag=False. Got: {fixture_calls}"
    )


# ── Per-tenant flag reader (_allow_fixture_fallback) ──────────────────────────

def test_allow_fixture_fallback_prestamype_is_false():
    """prestamype.allow_fixture_fallback must be False (prod = fail-closed)."""
    dds._allow_fixture_fallback.cache_clear()
    result = dds._allow_fixture_fallback("prestamype")
    dds._allow_fixture_fallback.cache_clear()
    assert result is False, f"prestamype should have allow_fixture_fallback=False, got {result!r}"


def test_allow_fixture_fallback_prestaunion_is_true():
    """prestaunion.allow_fixture_fallback must be True (demo = fixture allowed)."""
    dds._allow_fixture_fallback.cache_clear()
    result = dds._allow_fixture_fallback("prestaunion")
    dds._allow_fixture_fallback.cache_clear()
    assert result is True, f"prestaunion should have allow_fixture_fallback=True, got {result!r}"


def test_allow_fixture_fallback_unknown_tenant_defaults_false(tmp_path, monkeypatch):
    """Unknown tenant (no key in config) → False (default, fail-closed)."""
    # Create a tenant config WITHOUT the allow_fixture_fallback key.
    tenant_dir = tmp_path / "unknown_tenant"
    tenant_dir.mkdir()
    (tenant_dir / "tenant.config.json").write_text(
        json.dumps({"id": "unknown_tenant", "data_source": "doris",
                    "doris_schema": {"db": "x", "debt_table": "t", "pagos_table": "p",
                                     "join": {"debt_key": "id", "pagos_key": "id"},
                                     "dni_column": "dni", "column_map": {"borrower_name": {"source": "debt", "column": "name"}}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(dds, "_tenants_root", lambda: tmp_path)
    dds._allow_fixture_fallback.cache_clear()
    result = dds._allow_fixture_fallback("unknown_tenant")
    dds._allow_fixture_fallback.cache_clear()
    assert result is False, f"Missing key should default to False, got {result!r}"


def test_allow_fixture_fallback_lru_cache_does_not_bleed_between_tests():
    """Verify cache_clear() between calls isolates state — no cross-test bleed."""
    dds._allow_fixture_fallback.cache_clear()
    v1 = dds._allow_fixture_fallback("prestamype")
    dds._allow_fixture_fallback.cache_clear()
    v2 = dds._allow_fixture_fallback("prestamype")
    dds._allow_fixture_fallback.cache_clear()
    # Both calls must return the same canonical value (from config).
    assert v1 == v2 == False  # noqa: E712


# ── 3.2 Safe-degradation message when Doris down + flag=False (task 3.2) ─────

@pytest.mark.anyio
async def test_identificar_cliente_doris_down_flag_false_returns_safe_message(monkeypatch):
    """Doris down + allow_fixture_fallback=False → identified=False, neutral message.

    The response must NOT reveal that Doris is down (no technical detail).
    The user-facing message must be non-empty and not expose internal state.
    Reason must be 'dni_not_found' (the tool cannot distinguish down vs. absent
    without a separate reason code — acceptable; the gate stays closed either way).
    """
    from api.tool_registry import ToolRegistry

    # Patch _connect to simulate Doris down.
    dds._load_schema.cache_clear()
    dds._allow_fixture_fallback.cache_clear()
    monkeypatch.setattr(dds, "_connect", lambda db: (_ for _ in ()).throw(ConnectionError("Doris down")))

    # prestamype has allow_fixture_fallback=false — fail-closed.
    reg = ToolRegistry(identity_verified=False, tenant_id="prestamype")
    result = await reg.execute("identificar_cliente", {"dni": "12345678"})

    dds._allow_fixture_fallback.cache_clear()

    assert result.get("identified") is False, f"Expected identified=False, got {result}"
    msg = result.get("message", "")
    assert msg, "Expected a non-empty user-facing message"
    # Message must be neutral — no stack traces, no 'Doris', no 'Connection'.
    assert "Doris" not in msg
    assert "Connection" not in msg
    assert "Exception" not in msg
