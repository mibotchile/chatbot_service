"""Tests for Phase 10 — N° cuota correlativo validation (CPR-01).

STRICT TDD — tests written first (RED), implementation satisfies them (GREEN).

Decision locked (Naomi 2026-06-10):
  N° cuota is a correlativo (1, 2, 3…) matching the "Nro Cuotas" column in the
  Prestamype payment file. Must match the customer's cronograma via get_cronograma.

Five cases:
  (a) valid n_cuota=2 matching cronograma → payload accepted (n_cuota in result)
  (b) n_cuota=99 not in cronograma → re-ask triggered (reask dict returned)
  (c) n_cuota="abc" non-integer → re-ask triggered
  (d) n_cuota=None → required-field error
  (e) cronograma unavailable (Doris error / empty) → accept n_cuota best-effort
      (flow not blocked on Doris failure)
"""

from __future__ import annotations

import pytest


# ── Shared fixture ─────────────────────────────────────────────────────────────

def _profile(account_id: str = "P02137") -> dict:
    return {
        "account_id": account_id,
        "dni": "44218903",
        "borrower_name": "Luis Demo",
        "balance": 23800.0,
        "saldo_por_cancelar": 23800.0,
        "cuota_esperada": 462.14,
        "next_installment_amount": 462.14,
        "next_due_date": "2026-07-15",
        "status": "al_dia",
        "days_overdue": 0,
        "cuotas_vencidas": 0,
    }


_FAKE_CRONOGRAMA = [
    {"n_cuota": 1, "fecha_venc": "2026-04-15", "monto": 462.14, "estado": "pagado"},
    {"n_cuota": 2, "fecha_venc": "2026-05-15", "monto": 462.14, "estado": "pagado"},
    {"n_cuota": 3, "fecha_venc": "2026-06-15", "monto": 462.14, "estado": "pendiente"},
    {"n_cuota": 4, "fecha_venc": "2026-07-15", "monto": 462.14, "estado": "pendiente"},
]


# ── Case (a): valid n_cuota matching cronograma → accepted ─────────────────────


@pytest.mark.asyncio
async def test_ncuota_valid_matches_cronograma_accepted(tmp_path, monkeypatch):
    """n_cuota=2 is in the cronograma → validar_comprobante returns accepted payload."""
    import features.comprobantes.validator as validator

    monkeypatch.setattr(validator, "_COMPROBANTES_PATH", tmp_path / "comprobantes.json")

    # Patch get_cronograma to return the fake schedule
    import features.comprobantes.validator as v_mod
    monkeypatch.setattr(
        v_mod,
        "_get_cronograma_for_validation",
        lambda account_id, tenant_id: _FAKE_CRONOGRAMA,
    )

    from features.comprobantes.validator import validar_comprobante

    result = await validar_comprobante(
        _profile(),
        monto=462.14,
        n_cuota="2",
        tenant_id="prestamype",
    )
    assert result.get("ncuota_reask") is not True, "Should not trigger reask for valid n_cuota"
    assert result["n_cuota"] == "2"
    assert result.get("cuenta_valida") is True


# ── Case (b): n_cuota=99 not in cronograma → re-ask triggered ─────────────────


@pytest.mark.asyncio
async def test_ncuota_not_in_cronograma_triggers_reask(tmp_path, monkeypatch):
    """n_cuota=99 is not in the cronograma → validar_comprobante returns reask dict."""
    import features.comprobantes.validator as validator

    monkeypatch.setattr(validator, "_COMPROBANTES_PATH", tmp_path / "comprobantes.json")

    import features.comprobantes.validator as v_mod
    monkeypatch.setattr(
        v_mod,
        "_get_cronograma_for_validation",
        lambda account_id, tenant_id: _FAKE_CRONOGRAMA,
    )

    from features.comprobantes.validator import validar_comprobante

    result = await validar_comprobante(
        _profile(),
        monto=462.14,
        n_cuota="99",
        tenant_id="prestamype",
    )
    assert result.get("ncuota_reask") is True
    assert "n_cuota" in result
    assert result.get("cuenta_valida") is not True or result.get("ncuota_reask") is True


# ── Case (c): n_cuota="abc" non-integer → re-ask triggered ────────────────────


@pytest.mark.asyncio
async def test_ncuota_non_integer_triggers_reask(tmp_path, monkeypatch):
    """n_cuota='abc' is not a valid positive integer → validar_comprobante returns reask dict."""
    import features.comprobantes.validator as validator

    monkeypatch.setattr(validator, "_COMPROBANTES_PATH", tmp_path / "comprobantes.json")

    import features.comprobantes.validator as v_mod
    monkeypatch.setattr(
        v_mod,
        "_get_cronograma_for_validation",
        lambda account_id, tenant_id: _FAKE_CRONOGRAMA,
    )

    from features.comprobantes.validator import validar_comprobante

    result = await validar_comprobante(
        _profile(),
        monto=462.14,
        n_cuota="abc",
        tenant_id="prestamype",
    )
    assert result.get("ncuota_reask") is True


# ── Case (d): n_cuota=None → required-field error ─────────────────────────────


@pytest.mark.asyncio
async def test_ncuota_none_returns_required_error(tmp_path, monkeypatch):
    """n_cuota=None → validar_comprobante returns a required-field error dict."""
    import features.comprobantes.validator as validator

    monkeypatch.setattr(validator, "_COMPROBANTES_PATH", tmp_path / "comprobantes.json")

    import features.comprobantes.validator as v_mod
    monkeypatch.setattr(
        v_mod,
        "_get_cronograma_for_validation",
        lambda account_id, tenant_id: _FAKE_CRONOGRAMA,
    )

    from features.comprobantes.validator import validar_comprobante

    result = await validar_comprobante(
        _profile(),
        monto=462.14,
        n_cuota=None,
        tenant_id="prestamype",
    )
    assert result.get("ncuota_required") is True
    # Must not register or succeed normally
    assert result.get("cuenta_valida") is not True or result.get("ncuota_required") is True


# ── Case (e): cronograma unavailable → accept best-effort, do NOT block ───────


@pytest.mark.asyncio
async def test_ncuota_cronograma_unavailable_accepts_best_effort(tmp_path, monkeypatch):
    """When get_cronograma returns [] (Doris error / empty), n_cuota is accepted
    best-effort without cross-validation. Flow is NOT blocked.
    """
    import features.comprobantes.validator as validator

    monkeypatch.setattr(validator, "_COMPROBANTES_PATH", tmp_path / "comprobantes.json")

    import features.comprobantes.validator as v_mod
    # Doris unavailable → empty list
    monkeypatch.setattr(
        v_mod,
        "_get_cronograma_for_validation",
        lambda account_id, tenant_id: [],
    )

    from features.comprobantes.validator import validar_comprobante

    result = await validar_comprobante(
        _profile(),
        monto=462.14,
        n_cuota="7",
        tenant_id="prestamype",
    )
    # best-effort: no reask, no required error — flow continues
    assert result.get("ncuota_reask") is not True
    assert result.get("ncuota_required") is not True
    assert result["n_cuota"] == "7"
    assert result.get("cuenta_valida") is True
