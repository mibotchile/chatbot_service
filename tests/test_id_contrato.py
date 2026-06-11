"""Phase 5 — IDC-01: ID-Contrato + DNI dual-factor identification tests.

Covers:
  (a) valid contrato_id + matching DNI (titular/SOLICITANTE) → profile returned
  (b) valid contrato_id + matching DNI (garante) → profile returned (authorized)
  (c) valid contrato_id + DNI NOT in {titular, garante} → None (fail-closed)
  (d) unknown contrato_id + any DNI → None (same message as mismatch, no reveal)
  (e) titular+garante rows → resolve_contrato returns exactly ONE profile (dedup)
  (f) max retries → asesor escalation
  (g) resolve_contrato returns None on DB exception without raising

Authorization rule:
  posicion_contractual ∈ {SOLICITANTE, GARANTE, FIADOR SOLIDARIO} → authorized
  TESTIGO DE IDENTIDAD is explicitly excluded.
  FIADOR SOLIDARIO included by default (solidary guarantor = obligated party)
  — pending Naomi confirmation.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from features.cobranza import doris_debt_source as dds

TENANT = "prestamype"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_db_rows(contrato_id: str, *, persons: list[dict]) -> list[dict]:
    """Build fake batch_asignacion_review_bronze rows (already DISTINCT per rn=1).

    Each person dict: {dni_ruc, posicion_contractual, nombre_completo, ...}
    """
    base = {
        "id_credito": contrato_id,
        "dni_ruc": "00000000",
        "posicion_contractual": "SOLICITANTE",
        "nombre_completo": "Test Borrower",
        "dias_mora": 0,
        "fecha_vencimiento": "2026-09-01",
        "moneda": "SOLES",
        "banco": "BCP",
        "codigo_de_cuenta_cci": "00219100200001",
        "inversionista": "Inversionista X",
        "capital": "5000",
        "correo_electronico": "",
        "telefono": "",
        "numero_de_cuenta": "12345",
    }
    rows = []
    for p in persons:
        row = dict(base)
        row.update(p)
        rows.append(row)
    return rows


def _mock_contrato_query(rows: list[dict]):
    """Return a patch context that makes _query_contrato_rows return rows."""
    return patch.object(dds, "_query_contrato_rows", return_value=rows)


# ── (a) valid contrato_id + titular DNI → profile ────────────────────────────

def test_resolve_contrato_titular_dni_returns_profile():
    rows = _make_db_rows(
        "CONT-001",
        persons=[{"dni_ruc": "12345678", "posicion_contractual": "SOLICITANTE"}],
    )
    with _mock_contrato_query(rows):
        result = dds.resolve_contrato("CONT-001", "12345678", TENANT)
    assert result is not None
    assert result.get("account_id") == "CONT-001"


# ── (b) valid contrato_id + garante DNI → profile (authorized) ───────────────

def test_resolve_contrato_garante_dni_authorized():
    rows = _make_db_rows(
        "CONT-002",
        persons=[
            {"dni_ruc": "11111111", "posicion_contractual": "SOLICITANTE"},
            {"dni_ruc": "22222222", "posicion_contractual": "GARANTE"},
        ],
    )
    with _mock_contrato_query(rows):
        result = dds.resolve_contrato("CONT-002", "22222222", TENANT)
    assert result is not None
    assert result.get("account_id") == "CONT-002"


# ── (c) valid contrato_id + unauthorized DNI → None ──────────────────────────

def test_resolve_contrato_unauthorized_dni_returns_none():
    """TESTIGO DE IDENTIDAD and any other uninvolved DNI must return None."""
    rows = _make_db_rows(
        "CONT-003",
        persons=[
            {"dni_ruc": "12345678", "posicion_contractual": "SOLICITANTE"},
            {"dni_ruc": "99999999", "posicion_contractual": "TESTIGO DE IDENTIDAD"},
        ],
    )
    with _mock_contrato_query(rows):
        # TESTIGO DE IDENTIDAD — excluded from authorized set
        result = dds.resolve_contrato("CONT-003", "99999999", TENANT)
    assert result is None


def test_resolve_contrato_unknown_dni_returns_none():
    """A DNI that doesn't appear in the contract at all → None."""
    rows = _make_db_rows(
        "CONT-003",
        persons=[{"dni_ruc": "12345678", "posicion_contractual": "SOLICITANTE"}],
    )
    with _mock_contrato_query(rows):
        result = dds.resolve_contrato("CONT-003", "00000001", TENANT)
    assert result is None


# ── (d) unknown contrato_id → None ───────────────────────────────────────────

def test_resolve_contrato_unknown_contract_returns_none():
    """Unknown contract: _query_contrato_rows returns empty → None."""
    with _mock_contrato_query([]):
        result = dds.resolve_contrato("UNKNOWN-999", "12345678", TENANT)
    assert result is None


# ── (e) titular + garante rows dedup → ONE profile ───────────────────────────

def test_resolve_contrato_deduplicates_to_single_profile():
    """Multiple person-rows for the same id_credito collapse to exactly one profile."""
    rows = _make_db_rows(
        "CONT-005",
        persons=[
            {"dni_ruc": "12345678", "posicion_contractual": "SOLICITANTE",
             "nombre_completo": "Ana Pérez"},
            {"dni_ruc": "87654321", "posicion_contractual": "GARANTE",
             "nombre_completo": "Luis Pérez"},
        ],
    )
    with _mock_contrato_query(rows):
        result = dds.resolve_contrato("CONT-005", "12345678", TENANT)
    # Must return exactly one dict (not a list)
    assert isinstance(result, dict)
    assert result.get("account_id") == "CONT-005"


# ── (f) max retries → asesor (responses engine integration) ──────────────────

def test_id_contrato_max_retries_produces_asesor(prestamype_spec):
    """After _IDENT_RETRY_MAX failed contrato attempts, route to asesor escalation."""
    from features.conversation import responses as eng

    session_state: dict = {}
    profile: dict = {}

    # Simulate max-1 failures already recorded
    session_state["id_contrato_retry_count"] = eng._ID_CONTRATO_RETRY_MAX - 1

    outcome = eng.handle_id_contrato_not_found(
        spec=prestamype_spec,
        profile=profile,
        session_state=session_state,
        source=eng.SOURCE_KEYWORD,
    )
    assert outcome is not None
    assert outcome.handled
    # At max retries the outcome should escalate to asesor
    assert (
        "asesor" in outcome.text.lower()
        or outcome.run_tool is not None
        or outcome.intent == "id_contrato_max_retries"
    )


# ── (g) DB exception → None without raising ──────────────────────────────────

def test_resolve_contrato_db_exception_returns_none_no_raise():
    """Any DB error during contrato lookup returns None; never propagates."""
    with patch.object(dds, "_query_contrato_rows", side_effect=Exception("connection refused")):
        result = dds.resolve_contrato("CONT-007", "12345678", TENANT)
    assert result is None


# ── FIADOR SOLIDARIO included ─────────────────────────────────────────────────

def test_resolve_contrato_fiador_solidario_authorized():
    """FIADOR SOLIDARIO is an obligated party — authorized by default.

    Pending Naomi confirmation (2026-06-11): included until told otherwise.
    """
    rows = _make_db_rows(
        "CONT-008",
        persons=[
            {"dni_ruc": "11111111", "posicion_contractual": "SOLICITANTE"},
            {"dni_ruc": "33333333", "posicion_contractual": "FIADOR SOLIDARIO"},
        ],
    )
    with _mock_contrato_query(rows):
        result = dds.resolve_contrato("CONT-008", "33333333", TENANT)
    assert result is not None
    assert result.get("account_id") == "CONT-008"


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def prestamype_spec():
    """Load the real prestamype ResponsesSpec from disk."""
    import json
    from pathlib import Path
    from tenancy.responses_spec import ResponsesSpec

    root = Path(__file__).resolve().parent.parent
    path = root / "tenants" / "prestamype" / "responses.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return ResponsesSpec(data, _tenant_id="prestamype")
