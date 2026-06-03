"""Tests for the hardened rate limiter (core/rate_limit.py) + its wiring.

No Doris, no DB, no network. The clock is mocked (a mutable list cell) so every
window is driven deterministically; IPs are synthetic strings. We assert each
limit independently: DNI anti-enumeration (rate + distinct-DNI sweep→block),
chat/min, daily LLM-cost cap→429, and upload/hour.

The registry-integration test confirms the anti-enumeration hook short-circuits
``identificar_cliente`` WITHOUT resolving the DNI when the limiter denies.
"""

from __future__ import annotations

import pytest

from shared.rate_limit import RateLimitConfig, RateLimiter, from_settings


class FakeClock:
    """Mutable monotonic clock for deterministic window tests."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _limiter(clock: FakeClock, **overrides) -> RateLimiter:
    cfg = RateLimitConfig(**overrides)
    return RateLimiter(cfg, time_fn=clock)


# ── chat per-minute window ──────────────────────────────────────────────

def test_chat_per_min_allows_up_to_limit_then_429():
    clock = FakeClock()
    rl = _limiter(clock, chat_per_min=3)
    ip = "1.1.1.1"
    for _ in range(3):
        assert rl.check_chat_per_min(ip).allowed is True
    denied = rl.check_chat_per_min(ip)
    assert denied.allowed is False
    assert denied.reason == "chat_per_min"
    assert denied.retry_after >= 1


def test_chat_per_min_window_slides():
    clock = FakeClock()
    rl = _limiter(clock, chat_per_min=2)
    ip = "1.1.1.2"
    assert rl.check_chat_per_min(ip).allowed
    assert rl.check_chat_per_min(ip).allowed
    assert not rl.check_chat_per_min(ip).allowed
    # After the window passes, the budget resets.
    clock.advance(61)
    assert rl.check_chat_per_min(ip).allowed


def test_chat_per_min_is_per_ip():
    clock = FakeClock()
    rl = _limiter(clock, chat_per_min=1)
    assert rl.check_chat_per_min("a").allowed
    assert not rl.check_chat_per_min("a").allowed
    # Different IP has its own budget.
    assert rl.check_chat_per_min("b").allowed


def test_limit_zero_disables_check():
    clock = FakeClock()
    rl = _limiter(clock, chat_per_min=0)
    ip = "1.1.1.3"
    for _ in range(50):
        assert rl.check_chat_per_min(ip).allowed


# ── DNI anti-enumeration: rate ────────────────────────────────────────────

def test_identification_rate_per_hour():
    clock = FakeClock()
    # distinct disabled so we isolate the rate signal. The rate now counts NEW
    # identities, so we feed DISTINCT DNIs (repeats wouldn't count — see
    # test_repeated_same_dni_does_not_consume_ident_budget).
    rl = _limiter(clock, ident_per_hour=3, distinct_dni_per_hour=0)
    ip = "2.2.2.1"
    for n in range(3):
        assert rl.check_identification(ip, f"4178523{n}").allowed is True
    denied = rl.check_identification(ip, "41785239")
    assert denied.allowed is False
    assert denied.reason == "ident_per_hour"
    assert denied.retry_after >= 1


def test_identification_rate_window_slides():
    clock = FakeClock()
    rl = _limiter(clock, ident_per_hour=2, distinct_dni_per_hour=0)
    ip = "2.2.2.2"
    assert rl.check_identification(ip, "100").allowed
    assert rl.check_identification(ip, "200").allowed
    assert not rl.check_identification(ip, "300").allowed
    clock.advance(3601)
    assert rl.check_identification(ip, "400").allowed


# ── DNI anti-enumeration: diversity (sweep) → temporary block ─────────────

def test_distinct_dni_sweep_triggers_temp_block():
    clock = FakeClock()
    # high rate so the rate signal never trips first; isolate diversity.
    rl = _limiter(clock, ident_per_hour=100, distinct_dni_per_hour=5, block_minutes=15)
    ip = "3.3.3.1"
    # 5 distinct DNIs are allowed.
    for n in range(5):
        assert rl.check_identification(ip, f"dni-{n}").allowed is True
    # The 6th DISTINCT DNI exceeds the diversity threshold → block.
    denied = rl.check_identification(ip, "dni-5")
    assert denied.allowed is False
    assert denied.reason == "dni_sweep_block"
    # Block is ~15 minutes.
    assert 14 * 60 <= denied.retry_after <= 15 * 60


def test_temp_block_applies_to_all_checks_then_expires():
    clock = FakeClock()
    rl = _limiter(
        clock,
        ident_per_hour=100,
        distinct_dni_per_hour=2,
        block_minutes=15,
        chat_per_min=100,
        upload_per_hour=100,
    )
    ip = "3.3.3.2"
    assert rl.check_identification(ip, "a").allowed
    assert rl.check_identification(ip, "b").allowed
    assert not rl.check_identification(ip, "c").allowed  # 3rd distinct → block
    # While blocked, OTHER surfaces are denied too (the IP is hostile).
    assert not rl.check_chat_per_min(ip).allowed
    assert not rl.check_upload_per_hour(ip).allowed
    # After the block expires, normal service resumes.
    clock.advance(15 * 60 + 1)
    assert rl.check_chat_per_min(ip).allowed


def test_repeated_same_dni_is_not_a_sweep():
    clock = FakeClock()
    rl = _limiter(clock, ident_per_hour=100, distinct_dni_per_hour=3)
    ip = "3.3.3.3"
    # Hammering ONE DNI 10× is not diversity — no sweep block from this signal.
    for _ in range(10):
        d = rl.check_identification(ip, "same-dni")
        assert d.reason != "dni_sweep_block"


def test_repeated_same_dni_does_not_consume_ident_budget():
    """A legitimate borrower re-submitting the SAME DNI never trips ident_per_hour.

    Anti-enum counts NEW identities, not repeats. Hammering one verified DNI 20×
    (e.g. uploading many vouchers) stays allowed even with a tiny rate limit;
    only DISTINCT DNIs consume the budget.
    """
    clock = FakeClock()
    rl = _limiter(clock, ident_per_hour=2, distinct_dni_per_hour=10)
    ip = "2.2.2.9"
    for _ in range(20):
        d = rl.check_identification(ip, "44218903")
        assert d.allowed is True
        assert d.reason != "ident_per_hour"


def test_distinct_dnis_still_trip_ident_rate_after_repeats():
    """Repeats are free, but NEW DNIs still consume the rate budget.

    After hammering one DNI (free), introducing distinct DNIs beyond the rate
    limit must still 429 with ident_per_hour (distinct sweep disabled to isolate
    the rate signal).
    """
    clock = FakeClock()
    rl = _limiter(clock, ident_per_hour=2, distinct_dni_per_hour=0)
    ip = "2.2.2.10"
    # Repeats of the same DNI: free, never count.
    for _ in range(5):
        assert rl.check_identification(ip, "11111111").allowed
    # 2 NEW distinct DNIs: allowed (budget = 2). NOTE the first DNI already
    # consumed 1 (it was new the first time), so the budget is the # of distinct.
    assert rl.check_identification(ip, "22222222").allowed
    # 3rd distinct DNI exceeds ident_per_hour=2 → 429.
    denied = rl.check_identification(ip, "33333333")
    assert denied.allowed is False
    assert denied.reason == "ident_per_hour"


def test_empty_dni_counts_as_attempt_not_distinct():
    clock = FakeClock()
    rl = _limiter(clock, ident_per_hour=2, distinct_dni_per_hour=5)
    ip = "3.3.3.4"
    # Empty DNIs still consume the rate budget (enumeration probes).
    assert rl.check_identification(ip, "").allowed
    assert rl.check_identification(ip, "").allowed
    denied = rl.check_identification(ip, "")
    assert denied.allowed is False
    assert denied.reason == "ident_per_hour"


# ── daily LLM-spend cap ───────────────────────────────────────────────────

def test_daily_cost_cap_429_after_cap_exceeded():
    clock = FakeClock()
    rl = _limiter(clock, daily_cost_cap_usd=0.50)
    ip = "4.4.4.1"
    # Under the cap → allowed.
    assert rl.check_daily_cost(ip).allowed
    rl.add_cost(ip, 0.30)
    assert rl.check_daily_cost(ip).allowed
    # Cross the cap.
    rl.add_cost(ip, 0.25)  # total 0.55 > 0.50
    denied = rl.check_daily_cost(ip)
    assert denied.allowed is False
    assert denied.reason == "daily_cost_cap"
    assert denied.retry_after >= 1


def test_daily_cost_resets_next_day():
    clock = FakeClock()
    rl = _limiter(clock, daily_cost_cap_usd=0.10)
    ip = "4.4.4.2"
    rl.add_cost(ip, 0.20)
    assert not rl.check_daily_cost(ip).allowed
    # Advance past UTC midnight (>24h guarantees a new date bucket).
    clock.advance(86400 + 10)
    assert rl.check_daily_cost(ip).allowed


def test_daily_cost_is_per_ip():
    clock = FakeClock()
    rl = _limiter(clock, daily_cost_cap_usd=0.10)
    rl.add_cost("x", 0.50)
    assert not rl.check_daily_cost("x").allowed
    assert rl.check_daily_cost("y").allowed


def test_cost_cap_uses_real_pricing_table():
    """The cap accumulates the SAME cost_usd the analytics sink records."""
    from config.pricing import compute_cost_usd

    clock = FakeClock()
    rl = _limiter(clock, daily_cost_cap_usd=0.01)
    ip = "4.4.4.3"
    # Haiku 4.5: $1/MTok in, $5/MTok out. 5000 in + 2000 out = 0.005 + 0.010 = 0.015.
    cost = compute_cost_usd("claude-haiku-4-5", 5000, 2000)
    assert cost == pytest.approx(0.015)
    rl.add_cost(ip, cost)
    assert not rl.check_daily_cost(ip).allowed  # 0.015 > 0.01


# ── upload per-hour ───────────────────────────────────────────────────────

def test_upload_per_hour_then_429():
    clock = FakeClock()
    rl = _limiter(clock, upload_per_hour=2)
    ip = "5.5.5.1"
    assert rl.check_upload_per_hour(ip).allowed
    assert rl.check_upload_per_hour(ip).allowed
    denied = rl.check_upload_per_hour(ip)
    assert denied.allowed is False
    assert denied.reason == "upload_per_hour"
    assert denied.retry_after >= 1


def test_upload_window_slides():
    clock = FakeClock()
    rl = _limiter(clock, upload_per_hour=1)
    ip = "5.5.5.2"
    assert rl.check_upload_per_hour(ip).allowed
    assert not rl.check_upload_per_hour(ip).allowed
    clock.advance(3601)
    assert rl.check_upload_per_hour(ip).allowed


# ── settings wiring ───────────────────────────────────────────────────────

def test_from_settings_reads_env_defaults():
    from shared.config.settings import Settings

    rl = from_settings(Settings())
    cfg = rl.config
    assert cfg.ident_per_hour == 6
    assert cfg.distinct_dni_per_hour == 5
    assert cfg.block_minutes == 15
    assert cfg.chat_per_min == 12
    assert cfg.upload_per_hour == 8
    assert cfg.daily_cost_cap_usd == 0.50


def test_settings_overridable_by_env(monkeypatch):
    monkeypatch.setenv("COBRANZA_RL_IDENT_PER_HOUR", "3")
    monkeypatch.setenv("COBRANZA_RL_DISTINCT_DNI_PER_HOUR", "2")
    monkeypatch.setenv("COBRANZA_DAILY_COST_CAP_USD", "1.25")
    from shared.config.settings import Settings

    s = Settings()
    assert s.rl_ident_per_hour == 3
    assert s.rl_distinct_dni_per_hour == 2
    assert s.daily_cost_cap_usd == 1.25


# ── registry integration: anti-enumeration hook short-circuits resolution ──

async def test_registry_blocks_identification_without_resolving(monkeypatch):
    """A denied attempt returns rate_limited WITHOUT touching the data source."""
    from tools import ToolRegistry
    import tools as tools_pkg
    from shared.rate_limit import RateLimitDecision

    resolved = {"count": 0}

    def _spy_resolve(dni, tenant_id="prestaunion"):
        resolved["count"] += 1
        return {"account_id": "ACC", "borrower_name": "X"}

    monkeypatch.setattr(tools_pkg, "resolve_dni", _spy_resolve)

    reg = ToolRegistry(
        identity_verified=False,
        on_identification_attempt=lambda dni: RateLimitDecision(
            allowed=False, retry_after=900, reason="dni_sweep_block"
        ),
    )
    out = await reg.execute("identificar_cliente", {"dni": "41785236"})
    assert out["identified"] is False
    assert out["reason"] == "rate_limited"
    assert out["retry_after"] == 900
    # The data source was NEVER queried.
    assert resolved["count"] == 0


async def test_registry_allows_identification_when_under_limit(monkeypatch):
    from tools import ToolRegistry
    import tools as tools_pkg
    from shared.rate_limit import RateLimitDecision

    monkeypatch.setattr(
        tools_pkg,
        "resolve_dni",
        lambda dni, tenant_id="prestaunion": {
            "account_id": "ACC-1",
            "borrower_name": "Juan Pérez",
            "business_name": "Bodega",
            "status_label": "Al día",
        },
    )
    reg = ToolRegistry(
        identity_verified=False,
        on_identification_attempt=lambda dni: RateLimitDecision(allowed=True),
    )
    out = await reg.execute("identificar_cliente", {"dni": "41785236"})
    assert out["identified"] is True
    assert out["borrower_name"] == "Juan Pérez"
