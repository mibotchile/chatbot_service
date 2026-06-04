"""TDD tests for DNI/RUC format validation in _identificar_cliente.

Spec: normalize (strip non-digits), then check len == 8 (DNI) or len == 11 (RUC).
Anything else → {identified: False, reason: "invalid_format"} WITHOUT calling
resolve_dni or the anti-enumeration attempt counter.

RED tests written first (task 2.1); GREEN after implementation (task 2.2).
"""

from __future__ import annotations

import pytest

from api.tool_registry import ToolRegistry


# ── Helpers ─────────────────────────────────────────────────────────────────

def _reg(*, attempt_called: list[str] | None = None, resolve_called: list[str] | None = None) -> ToolRegistry:
    """Build a registry wired to track side-effects.

    ``attempt_called`` accumulates DNIs passed to the attempt callback.
    ``resolve_called`` is a sentinel list — if resolve_dni is invoked, it
    appends the DNI (we monkeypatch via the module-level import).
    """
    def _attempt(dni: str):
        if attempt_called is not None:
            attempt_called.append(dni)
        return None  # allowed

    return ToolRegistry(
        identity_verified=False,
        on_identification_attempt=_attempt,
    )


# ── Invalid format — must reject, not call attempt counter ──────────────────

@pytest.mark.parametrize("bad_input,description", [
    ("hola",         "alphabetic string"),
    ("1234",         "too short (4 digits)"),
    ("123456789",    "9 digits — neither DNI(8) nor RUC(11)"),
    ("",             "empty string"),
    ("       ",      "whitespace only"),
    ("12345678901234", "too long (14 digits)"),
])
async def test_invalid_format_returns_invalid_format_reason(bad_input: str, description: str):
    """Garbage input → identified=False, reason=invalid_format."""
    attempts: list[str] = []
    reg = _reg(attempt_called=attempts)
    result = await reg.execute("identificar_cliente", {"dni": bad_input})

    assert result.get("identified") is False, f"[{description}] expected identified=False, got {result}"
    assert result.get("reason") == "invalid_format", (
        f"[{description}] expected reason='invalid_format', got {result.get('reason')!r}"
    )
    assert "message" in result, f"[{description}] expected a user-facing 'message' key"


async def test_invalid_format_does_not_increment_attempt_counter():
    """Garbage DNI must NOT call the anti-enumeration attempt counter.

    Garbage is not a probe — counting it would trip rate limiting for clumsy
    real users and leak the 8-digit rule via the rate-limited response.
    """
    attempts: list[str] = []
    reg = _reg(attempt_called=attempts)
    await reg.execute("identificar_cliente", {"dni": "hola"})
    assert attempts == [], f"Attempt counter was called with {attempts!r} — should not be called on invalid format"


async def test_invalid_format_9_digits_does_not_increment_attempt_counter():
    """9-digit input (not DNI, not RUC) must not trigger the attempt counter."""
    attempts: list[str] = []
    reg = _reg(attempt_called=attempts)
    await reg.execute("identificar_cliente", {"dni": "123456789"})
    assert attempts == [], "9-digit input should be rejected before attempt counter"


# ── Valid format — must NOT be rejected by the format gate ──────────────────

async def test_valid_8digit_dni_passes_format_gate(monkeypatch):
    """A clean 8-digit DNI must pass format validation and reach resolve_dni.

    Patch api.tool_registry.resolve_dni — the name bound in tool_registry's
    module namespace via `from features.cobranza.debt_source import resolve_dni`.
    """
    import api.tool_registry as tr
    resolve_calls: list[str] = []

    def _fake_resolve(dni: str, *, tenant_id: str) -> dict | None:
        resolve_calls.append(dni)
        return None  # not found is fine; we only care that the gate was passed

    monkeypatch.setattr(tr, "resolve_dni", _fake_resolve)

    reg = ToolRegistry(identity_verified=False)
    result = await reg.execute("identificar_cliente", {"dni": "12345678"})

    assert resolve_calls == ["12345678"], (
        f"Expected resolve_dni to be called with '12345678', got {resolve_calls}"
    )
    assert result.get("reason") != "invalid_format", (
        f"8-digit DNI should NOT be rejected by format gate, got reason={result.get('reason')!r}"
    )


async def test_valid_11digit_ruc_passes_format_gate(monkeypatch):
    """An 11-digit RUC must pass format validation and reach resolve_dni."""
    import api.tool_registry as tr
    resolve_calls: list[str] = []

    def _fake_resolve(dni: str, *, tenant_id: str) -> dict | None:
        resolve_calls.append(dni)
        return None

    monkeypatch.setattr(tr, "resolve_dni", _fake_resolve)

    reg = ToolRegistry(identity_verified=False)
    result = await reg.execute("identificar_cliente", {"dni": "12345678901"})

    assert resolve_calls == ["12345678901"], (
        f"Expected resolve_dni called with '12345678901', got {resolve_calls}"
    )
    assert result.get("reason") != "invalid_format"


async def test_formatted_dni_with_dots_normalized_and_accepted(monkeypatch):
    """'12.345.678' → strip non-digits → '12345678' (8 digits) → passes."""
    import api.tool_registry as tr
    resolve_calls: list[str] = []

    def _fake_resolve(dni: str, *, tenant_id: str) -> dict | None:
        resolve_calls.append(dni)
        return None

    monkeypatch.setattr(tr, "resolve_dni", _fake_resolve)

    reg = ToolRegistry(identity_verified=False)
    result = await reg.execute("identificar_cliente", {"dni": "12.345.678"})

    assert resolve_calls == ["12345678"], (
        f"Formatted DNI with dots should normalize to '12345678', got {resolve_calls}"
    )
    assert result.get("reason") != "invalid_format"


async def test_formatted_dni_with_spaces_normalized_and_accepted(monkeypatch):
    """'1234 5678' → strip non-digits → '12345678' (8 digits) → passes."""
    import api.tool_registry as tr
    resolve_calls: list[str] = []

    def _fake_resolve(dni: str, *, tenant_id: str) -> dict | None:
        resolve_calls.append(dni)
        return None

    monkeypatch.setattr(tr, "resolve_dni", _fake_resolve)

    reg = ToolRegistry(identity_verified=False)
    result = await reg.execute("identificar_cliente", {"dni": "1234 5678"})

    assert resolve_calls == ["12345678"]
    assert result.get("reason") != "invalid_format"
