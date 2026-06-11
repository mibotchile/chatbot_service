"""Horario + feriados gating for cobranza flows (Phase 9, INF-08 / INF-09).

Reads the canonical holiday calendar from ``feriados_peru_2026.json`` (located in
the tenant directory) and the ``cobranza.horario`` block from ``tenant.config.json``.
Exposes two pure-ish functions (I/O only at load time, cached):

  is_feriado(d: date) -> bool
      True when ``d`` is listed in the JSON holidays array.

  is_business_hours(dt: datetime) -> bool
      True when ``dt`` (Lima local, naive or Lima-aware) falls within the
      configured business window AND is NOT on a weekend AND is NOT in the
      refrigerio break.

Timezone note: datetimes passed to ``is_business_hours`` are treated as Lima
local time (America/Lima). If the datetime is timezone-aware it is converted
to Lima time first; if it is naive it is treated directly as Lima local.

Tenant-dir resolution mirrors ``doris_debt_source._tenants_root()`` — Docker
path ``/app/tenants`` wins; otherwise repo-root ``tenants/`` is derived from
this file's location.

No hardcoded dates — all holiday and schedule data come from the JSON files.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo


# ── Tenant directory resolution (same pattern as doris_debt_source) ──────────

def _tenants_root() -> Path:
    """Locate the tenants/ directory in both Docker and local-dev layouts."""
    docker_path = Path("/app/tenants")
    if docker_path.exists():
        return docker_path
    # apps/agent/features/cobranza/ → repo root → tenants/
    return Path(__file__).resolve().parent.parent.parent.parent.parent / "tenants"


def _tenant_dir(tenant_id: str) -> Path:
    return _tenants_root() / tenant_id


# ── Cached data loaders ───────────────────────────────────────────────────────

@lru_cache(maxsize=8)
def _load_feriados(tenant_id: str) -> frozenset[date]:
    """Load and cache the set of holiday dates from ``feriados_peru_2026.json``."""
    tenant_cfg_path = _tenant_dir(tenant_id) / "tenant.config.json"
    feriados_filename = "feriados_peru_2026.json"

    # Read the feriados_source from tenant config when available.
    if tenant_cfg_path.exists():
        try:
            cfg = json.loads(tenant_cfg_path.read_text(encoding="utf-8"))
            feriados_filename = (
                cfg.get("cobranza", {})
                .get("horario", {})
                .get("feriados_source", feriados_filename)
            )
        except (json.JSONDecodeError, OSError):
            pass

    feriados_path = _tenant_dir(tenant_id) / feriados_filename
    if not feriados_path.exists():
        return frozenset()

    raw = json.loads(feriados_path.read_text(encoding="utf-8"))
    holidays: frozenset[date] = frozenset(
        date.fromisoformat(entry["date"])
        for entry in raw.get("holidays", [])
    )
    return holidays


@lru_cache(maxsize=8)
def _load_horario_config(tenant_id: str) -> dict:
    """Load and cache the ``cobranza.horario`` block from ``tenant.config.json``.

    Returns a normalized dict with keys:
      - ``weekday_set``: frozenset of Python weekday() ints (0=Mon … 6=Sun)
      - ``open``: datetime.time for the opening hour
      - ``close``: datetime.time for the closing hour
      - ``break_start``: datetime.time for refrigerio start
      - ``break_end``: datetime.time for refrigerio end
      - ``timezone``: ZoneInfo object for the configured timezone
    """
    tenant_cfg_path = _tenant_dir(tenant_id) / "tenant.config.json"

    # Sensible defaults (match confirmed Naomi values)
    _DAY_MAP = {
        "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
        "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6,
        # Also accept feriados_peru_2026 uppercase abbreviations just in case
        "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    }
    defaults: dict = {
        "weekday_set": frozenset({0, 1, 2, 3, 4}),
        "open": time(9, 0),
        "close": time(18, 30),
        "break_start": time(13, 0),
        "break_end": time(14, 0),
        "timezone": ZoneInfo("America/Lima"),
    }

    if not tenant_cfg_path.exists():
        return defaults

    try:
        cfg = json.loads(tenant_cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return defaults

    horario = cfg.get("cobranza", {}).get("horario", {})
    if not horario:
        return defaults

    # Parse working days
    dias_raw = horario.get("dias") or []
    weekday_set: frozenset[int] = frozenset(
        _DAY_MAP[d.lower().strip()]
        for d in dias_raw
        if d.lower().strip() in _DAY_MAP
    ) or defaults["weekday_set"]

    # Parse open / close times
    def _parse_time(val: str, fallback: time) -> time:
        try:
            h, m = val.strip().split(":")
            return time(int(h), int(m))
        except (AttributeError, ValueError):
            return fallback

    open_t = _parse_time(horario.get("hora_inicio", ""), defaults["open"])
    close_t = _parse_time(horario.get("hora_fin", ""), defaults["close"])

    # Parse refrigerio
    ref = horario.get("refrigerio") or {}
    break_start = _parse_time(ref.get("inicio", ""), defaults["break_start"])
    break_end = _parse_time(ref.get("fin", ""), defaults["break_end"])

    # Timezone: prefer cobranza.horario.timezone from tenant config, then
    # feriados_peru_2026.json business_hours.timezone as secondary source.
    # Falls back to "America/Lima" with a warning when neither key is present.
    tz_name: str | None = horario.get("timezone")
    if not tz_name:
        feriados_filename = horario.get("feriados_source", "feriados_peru_2026.json")
        feriados_path = _tenant_dir(tenant_id) / feriados_filename
        if feriados_path.exists():
            try:
                fraw = json.loads(feriados_path.read_text(encoding="utf-8"))
                tz_name = fraw.get("business_hours", {}).get("timezone")
            except (json.JSONDecodeError, OSError):
                pass
    if not tz_name:
        from loguru import logger as _log  # noqa: PLC0415
        _log.warning(
            "horario: cobranza.horario.timezone missing for tenant '{}'; "
            "falling back to 'America/Lima'",
            tenant_id,
        )
        tz_name = "America/Lima"

    return {
        "weekday_set": weekday_set,
        "open": open_t,
        "close": close_t,
        "break_start": break_start,
        "break_end": break_end,
        "timezone": ZoneInfo(tz_name),
    }


# ── Public API ────────────────────────────────────────────────────────────────

def is_feriado(d: date, *, tenant_id: str) -> bool:
    """Return True when ``d`` is a holiday for the given tenant.

    Data sourced exclusively from the tenant's feriados JSON file — no hardcoded dates.

    Args:
        d: the date to check.
        tenant_id: tenant whose config/feriados file to use (required).
    """
    return d in _load_feriados(tenant_id)


def is_business_hours(dt: datetime, *, tenant_id: str) -> bool:
    """Return True when ``dt`` falls within configured business hours.

    Returns False when ANY of these conditions hold:
      - The weekday is not in the configured working-days set (e.g. weekends)
      - The time is before ``hora_inicio`` (09:00)
      - The time is at or after ``hora_fin`` (18:30) — the endpoint is exclusive
      - The time falls within the refrigerio window [13:00, 14:00)

    Args:
        dt: a datetime to check. If timezone-aware it is converted to Lima time
            first; if naive it is treated directly as Lima local time.
        tenant_id: tenant whose config to use (required).
    """
    horario = _load_horario_config(tenant_id)
    tz: ZoneInfo = horario["timezone"]

    # Normalise to Lima local time
    if dt.tzinfo is not None:
        local_dt = dt.astimezone(tz)
    else:
        local_dt = dt  # treat naive as Lima local

    weekday = local_dt.weekday()  # 0=Mon, 6=Sun
    if weekday not in horario["weekday_set"]:
        return False

    t = local_dt.time()

    # Outside operating window
    if t < horario["open"] or t >= horario["close"]:
        return False

    # Refrigerio window [break_start, break_end)
    if horario["break_start"] <= t < horario["break_end"]:
        return False

    return True
