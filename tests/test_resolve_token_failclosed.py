"""TDD tests for resolve_token fail-closed fix (W2 — PR2 pre-flight).

Spec (mirroring resolve_dni contract):
  - Token resolves a DNI; Doris OK + that DNI NOT in Doris (zero rows) + flag=False
      → resolve_token returns None (fixture NOT returned)
  - Token resolves a DNI; Doris OK + that DNI NOT in Doris (zero rows) + flag=True
      → resolve_token returns fixture_profile (demo fallback preserved)
  - Token resolves a DNI; Doris OK + DNI IN Doris → returns the Doris credit (unchanged)
  - Token NOT in fixture (fixture_profile is None) → None (unchanged)

Security assertion: a bogus/demo token whose DNI is absent from Doris with
flag=False CANNOT return the fixture profile — the identity gate stays closed.

lru_cache handling: each test calls _allow_fixture_fallback.cache_clear() before
and after to prevent state bleed.
"""

from __future__ import annotations

import pytest

from features.cobranza import doris_debt_source as dds


# ── Fixtures / helpers ────────────────────────────────────────────────────────

_FIXTURE_PROFILE = {
    "account_id": "DEMO-001",
    "dni": "12345678",
    "borrower_name": "DEMO USER",
}

_DORIS_PROFILE = {
    "account_id": "DORIS-001",
    "dni": "12345678",
    "borrower_name": "LIVE USER",
}

_DORIS_PROFILE_ALT = {
    "account_id": "DORIS-002",
    "dni": "12345678",
    "borrower_name": "LIVE USER 2",
}


def _patch_fixture_token(monkeypatch, profile: dict | None):
    """Stub mock_debt_source.resolve_token to return a fixture profile."""
    monkeypatch.setattr(dds.mock_debt_source, "resolve_token", lambda tok, *, tenant_id: profile)


def _patch_resolve_dni_credits(monkeypatch, credits: list[dict]):
    """Stub _resolve_dni_credits to return a pre-built list (bypass Doris entirely)."""
    monkeypatch.setattr(dds, "_resolve_dni_credits", lambda dni, tenant_id: credits)


# ── W2a: Doris OK + DNI not found + flag=False → None (KEY SECURITY TEST) ────

def test_resolve_token_doris_up_dni_absent_flag_false_returns_none(monkeypatch):
    """SECURITY: Doris OK + DNI absent from Doris + flag=False → None.

    A demo token whose DNI is not in Doris MUST NOT resolve to the fixture
    profile when allow_fixture_fallback is False (prod / prestamype config).
    The identity gate must stay closed.
    """
    _patch_fixture_token(monkeypatch, _FIXTURE_PROFILE)
    # Doris up but returns zero rows → _resolve_dni_credits returns []
    _patch_resolve_dni_credits(monkeypatch, credits=[])

    dds._allow_fixture_fallback.cache_clear()
    # prestamype has allow_fixture_fallback=False
    result = dds.resolve_token("DEMO_TOKEN", "prestamype")
    dds._allow_fixture_fallback.cache_clear()

    assert result is None, (
        f"SECURITY VIOLATION: resolve_token returned {result!r} instead of None. "
        "A demo token with DNI absent from Doris (flag=False) must NOT return the fixture profile."
    )


# ── W2b: Doris OK + DNI not found + flag=True → fixture returned ──────────────

def test_resolve_token_doris_up_dni_absent_flag_true_returns_fixture(monkeypatch):
    """Doris OK + DNI absent from Doris + flag=True → fixture_profile returned.

    Demo tenants (prestaunion) preserve the affordance: if Doris has no row
    for this DNI (even though Doris is up), fall back to fixture.
    """
    _patch_fixture_token(monkeypatch, _FIXTURE_PROFILE)
    _patch_resolve_dni_credits(monkeypatch, credits=[])

    dds._allow_fixture_fallback.cache_clear()
    # prestaunion has allow_fixture_fallback=True
    result = dds.resolve_token("DEMO_TOKEN", "prestaunion")
    dds._allow_fixture_fallback.cache_clear()

    assert result == _FIXTURE_PROFILE, (
        f"Expected fixture_profile for flag=True + empty Doris result, got {result!r}"
    )


# ── W2c: Doris OK + DNI IN Doris → Doris credit returned (unchanged) ──────────

def test_resolve_token_doris_up_dni_present_returns_doris_credit(monkeypatch):
    """Doris OK + DNI in Doris → returns the matching Doris credit (unchanged behavior)."""
    _patch_fixture_token(monkeypatch, _FIXTURE_PROFILE)
    _patch_resolve_dni_credits(monkeypatch, credits=[_DORIS_PROFILE])

    dds._allow_fixture_fallback.cache_clear()
    result = dds.resolve_token("DEMO_TOKEN", "prestamype")
    dds._allow_fixture_fallback.cache_clear()

    assert result == _DORIS_PROFILE, (
        f"Expected Doris credit when DNI is present, got {result!r}"
    )


# ── W2d: Doris OK + multiple credits → prefers credit matching account_id ─────

def test_resolve_token_prefers_credit_matching_account_id(monkeypatch):
    """When Doris returns multiple credits, the one matching fixture account_id wins."""
    fixture = {**_FIXTURE_PROFILE, "account_id": "DORIS-002"}
    _patch_fixture_token(monkeypatch, fixture)
    _patch_resolve_dni_credits(monkeypatch, credits=[_DORIS_PROFILE, _DORIS_PROFILE_ALT])

    dds._allow_fixture_fallback.cache_clear()
    result = dds.resolve_token("DEMO_TOKEN", "prestamype")
    dds._allow_fixture_fallback.cache_clear()

    assert result == _DORIS_PROFILE_ALT, (
        f"Expected credit matching account_id='DORIS-002', got {result!r}"
    )


# ── W2e: Token not in fixture → None (unchanged) ──────────────────────────────

def test_resolve_token_not_in_fixture_returns_none(monkeypatch):
    """Token not found in fixture → None (unchanged early-exit behavior)."""
    _patch_fixture_token(monkeypatch, None)
    # _resolve_dni_credits should never be called — no need to patch

    dds._allow_fixture_fallback.cache_clear()
    result = dds.resolve_token("UNKNOWN_TOKEN", "prestamype")
    dds._allow_fixture_fallback.cache_clear()

    assert result is None, (
        f"Expected None when token not in fixture, got {result!r}"
    )


# ── W2f: prestaunion zero-behavior — flag=True path unchanged ─────────────────

def test_resolve_token_prestaunion_flag_true_doris_up_empty_returns_fixture(monkeypatch):
    """Zero-behavior: prestaunion (flag=True) with empty Doris still returns fixture.

    This is the existing demo-tenant contract — must not regress.
    """
    _patch_fixture_token(monkeypatch, _FIXTURE_PROFILE)
    _patch_resolve_dni_credits(monkeypatch, credits=[])

    dds._allow_fixture_fallback.cache_clear()
    result = dds.resolve_token("ANY_TOKEN", "prestaunion")
    dds._allow_fixture_fallback.cache_clear()

    assert result == _FIXTURE_PROFILE, (
        f"prestaunion flag=True + Doris empty should return fixture, got {result!r}"
    )
