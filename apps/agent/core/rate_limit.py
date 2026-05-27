"""Hardened per-IP rate limiting for the cobranza engine.

Extends the simple daily/minute IP limiter that lived inline in api/main.py with
targeted, attack-shaped limits:

  - **Anti-enumeration (DNI)**: the identity gate is DNI-only, so DNI brute-force
    / sweep is the highest-value vector. Two signals per IP per hour:
      · rate — too many identification attempts (``ident_per_hour``);
      · diversity — too many DISTINCT DNIs tried (``distinct_dni_per_hour``) →
        a sweep pattern → a temporary block (``block_minutes``).
  - **Short chat window**: cap chat messages/minute per IP (anti token-burn),
    on top of the existing daily cap.
  - **LLM spend cap**: accumulate ``cost_usd`` per IP per day (computed from the
    same pricing table the analytics sink uses) and cut over a USD cap.
  - **Upload**: cap comprobante uploads/hour per IP.

DESIGN — in-memory by default (fine for the single-container staging deploy).
A Redis URL (``COBRANZA_REDIS_URL``) is read by the caller and could back these
counters later; the limiter exposes a clean interface so swapping the storage is
mechanical. Nothing here imports Redis — keep it dependency-free + trivially
testable.

TESTABILITY — the clock is injectable (``time_fn``). Tests drive time forward
without sleeping and use synthetic IPs; nothing here touches Doris, the DB, or
the network.

CONTRACT — every public check returns a ``RateLimitDecision``. ``allowed`` gates
the request; ``retry_after`` seconds populate the HTTP ``Retry-After`` header;
``reason`` is an internal code (never leaked to the client verbatim).
"""

from __future__ import annotations

import threading
import time as _time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Deque


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of a rate-limit check.

    ``allowed`` False → the caller must reject with 429 and surface
    ``retry_after`` in the ``Retry-After`` header. ``reason`` is an internal
    code for logging — it must NOT be returned to the client verbatim.
    """

    allowed: bool
    retry_after: int = 0
    reason: str = ""


@dataclass
class RateLimitConfig:
    """All limits, env-driven (defaults match the task spec).

    Loaded once from ``settings`` via :func:`from_settings`. A limit of ``0``
    disables that specific check (escape hatch for staging evidence runs).
    """

    ident_per_hour: int = 6
    distinct_dni_per_hour: int = 5
    block_minutes: int = 15
    chat_per_min: int = 12
    daily_cost_cap_usd: float = 0.50
    upload_per_hour: int = 8


def _seconds_until_midnight_utc(now_ts: float) -> int:
    """Seconds from ``now_ts`` until the next UTC midnight (daily-cap reset)."""
    now = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    secs = 86400 - (now.hour * 3600 + now.minute * 60 + now.second)
    return max(secs, 1)


class RateLimiter:
    """In-memory, per-IP, thread-safe hardened rate limiter.

    One instance is shared by the app (module-level singleton in api.main).
    All state is bounded by pruning on access; idle IPs leave only small empty
    deques behind, acceptable for the staging single-container footprint.
    """

    def __init__(
        self,
        config: RateLimitConfig | None = None,
        *,
        time_fn: Callable[[], float] = _time.time,
    ) -> None:
        self._cfg = config or RateLimitConfig()
        self._time = time_fn
        self._lock = threading.Lock()

        # Sliding-window event timestamps per IP (seconds).
        self._chat_min: dict[str, Deque[float]] = defaultdict(deque)
        self._ident_hour: dict[str, Deque[float]] = defaultdict(deque)
        self._upload_hour: dict[str, Deque[float]] = defaultdict(deque)
        # Distinct DNIs seen per IP in the last hour: list of (ts, dni).
        self._dni_hour: dict[str, Deque[tuple[float, str]]] = defaultdict(deque)
        # Temporary blocks: ip -> unblock_ts.
        self._blocked_until: dict[str, float] = {}
        # Daily LLM spend per IP: ip -> (date_str, cost_usd).
        self._daily_cost: dict[str, tuple[str, float]] = {}

    @property
    def config(self) -> RateLimitConfig:
        return self._cfg

    def reset(self) -> None:
        """Drop all per-IP state (counters, blocks, daily cost).

        Used by the test suite to isolate the module-level singleton between
        cases. Not called in production.
        """
        with self._lock:
            self._chat_min.clear()
            self._ident_hour.clear()
            self._upload_hour.clear()
            self._dni_hour.clear()
            self._blocked_until.clear()
            self._daily_cost.clear()

    # ── helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _prune(window: Deque[float], cutoff: float) -> None:
        while window and window[0] <= cutoff:
            window.popleft()

    @staticmethod
    def _prune_pairs(window: Deque[tuple[float, str]], cutoff: float) -> None:
        while window and window[0][0] <= cutoff:
            window.popleft()

    def _today(self, now: float) -> str:
        return datetime.fromtimestamp(now, tz=timezone.utc).date().isoformat()

    def _is_blocked(self, ip: str, now: float) -> RateLimitDecision | None:
        """Return a deny decision if ``ip`` is in an active temporary block."""
        until = self._blocked_until.get(ip)
        if until is not None:
            if now < until:
                return RateLimitDecision(
                    allowed=False,
                    retry_after=max(int(until - now), 1),
                    reason="temp_block",
                )
            # Block expired — clear it.
            del self._blocked_until[ip]
        return None

    # ── public checks ───────────────────────────────────────────────────

    def check_chat_per_min(self, ip: str) -> RateLimitDecision:
        """Short-window chat cap: ``chat_per_min`` messages/min per IP."""
        limit = self._cfg.chat_per_min
        if limit <= 0:
            return RateLimitDecision(allowed=True)
        now = self._time()
        with self._lock:
            blocked = self._is_blocked(ip, now)
            if blocked:
                return blocked
            window = self._chat_min[ip]
            self._prune(window, now - 60)
            if len(window) >= limit:
                # Retry after the oldest event ages out of the window.
                retry = max(int(window[0] + 60 - now), 1)
                return RateLimitDecision(False, retry, "chat_per_min")
            window.append(now)
            return RateLimitDecision(allowed=True)

    def check_identification(self, ip: str, dni: str) -> RateLimitDecision:
        """Anti-enumeration check for one DNI identification attempt.

        Records the attempt, then enforces two signals over the trailing hour:
          1. distinct-DNI diversity → sweep → temporary block (checked FIRST so a
             scan trips the longer block rather than the soft per-hour rate);
          2. attempt rate (``ident_per_hour``).

        ``dni`` should be the normalized digits; an empty/garbage value still
        counts as an attempt (it's an enumeration probe) but adds no distinct
        DNI.
        """
        now = self._time()
        with self._lock:
            blocked = self._is_blocked(ip, now)
            if blocked:
                return blocked

            attempts = self._ident_hour[ip]
            dnis = self._dni_hour[ip]
            cutoff = now - 3600
            self._prune(attempts, cutoff)
            self._prune_pairs(dnis, cutoff)

            # Record this attempt.
            attempts.append(now)
            dni_norm = (dni or "").strip()
            if dni_norm:
                dnis.append((now, dni_norm))

            # 1) Diversity / sweep → temporary block.
            distinct_limit = self._cfg.distinct_dni_per_hour
            if distinct_limit > 0:
                distinct = {d for _, d in dnis}
                if len(distinct) > distinct_limit:
                    block_secs = max(self._cfg.block_minutes, 1) * 60
                    self._blocked_until[ip] = now + block_secs
                    return RateLimitDecision(False, block_secs, "dni_sweep_block")

            # 2) Attempt rate.
            rate_limit = self._cfg.ident_per_hour
            if rate_limit > 0 and len(attempts) > rate_limit:
                retry = max(int(attempts[0] + 3600 - now), 1)
                return RateLimitDecision(False, retry, "ident_per_hour")

            return RateLimitDecision(allowed=True)

    def check_upload_per_hour(self, ip: str) -> RateLimitDecision:
        """Upload cap: ``upload_per_hour`` comprobantes/hour per IP."""
        limit = self._cfg.upload_per_hour
        if limit <= 0:
            return RateLimitDecision(allowed=True)
        now = self._time()
        with self._lock:
            blocked = self._is_blocked(ip, now)
            if blocked:
                return blocked
            window = self._upload_hour[ip]
            self._prune(window, now - 3600)
            if len(window) >= limit:
                retry = max(int(window[0] + 3600 - now), 1)
                return RateLimitDecision(False, retry, "upload_per_hour")
            window.append(now)
            return RateLimitDecision(allowed=True)

    def check_daily_cost(self, ip: str) -> RateLimitDecision:
        """Daily LLM-spend cap: deny once accumulated cost > the USD cap.

        Read-only — call BEFORE spending tokens. Accumulation happens in
        :meth:`add_cost` after the turn completes (we know the real usage then).
        """
        cap = self._cfg.daily_cost_cap_usd
        if cap <= 0:
            return RateLimitDecision(allowed=True)
        now = self._time()
        with self._lock:
            blocked = self._is_blocked(ip, now)
            if blocked:
                return blocked
            entry = self._daily_cost.get(ip)
            if entry is None or entry[0] != self._today(now):
                return RateLimitDecision(allowed=True)
            if entry[1] > cap:
                return RateLimitDecision(
                    False, _seconds_until_midnight_utc(now), "daily_cost_cap"
                )
            return RateLimitDecision(allowed=True)

    def add_cost(self, ip: str, cost_usd: float) -> None:
        """Accumulate ``cost_usd`` for ``ip`` on today's (UTC) bucket."""
        if cost_usd <= 0:
            return
        now = self._time()
        with self._lock:
            entry = self._daily_cost.get(ip)
            today = self._today(now)
            if entry is None or entry[0] != today:
                self._daily_cost[ip] = (today, float(cost_usd))
            else:
                self._daily_cost[ip] = (today, entry[1] + float(cost_usd))


def from_settings(settings, *, time_fn: Callable[[], float] = _time.time) -> RateLimiter:
    """Build a :class:`RateLimiter` from the app settings object."""
    cfg = RateLimitConfig(
        ident_per_hour=settings.rl_ident_per_hour,
        distinct_dni_per_hour=settings.rl_distinct_dni_per_hour,
        block_minutes=settings.rl_block_minutes,
        chat_per_min=settings.rl_chat_per_min,
        daily_cost_cap_usd=settings.daily_cost_cap_usd,
        upload_per_hour=settings.rl_upload_per_hour,
    )
    return RateLimiter(cfg, time_fn=time_fn)
