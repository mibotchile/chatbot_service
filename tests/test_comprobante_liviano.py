"""SLICE C — RED tests: Comprobante Liviano (server-side validation, lighter flow).

Spec: cobranza-comprobante
  - validate_comprobante(profile, monto, *, image_sha256=None, inversionista=None, id_credito=None)
  - CCI resolved server-side from profile, never from user input
  - Classification: pago_cuota / abono / cancelacion by monto vs cuota/saldo
  - Inversionista mismatch → inversionista_match=False, estado=en_revision (WARN, not reject)
  - Anti-dup by image_sha256 (within same credito) — dedup_ok=False on same image re-upload
  - Different images with same (credito, monto) → BOTH accepted (no false-positive dedup)
  - Audit captures inversionista and image_sha256
  - schema: monto required; inversionista+id_credito optional; CCI+nro_operacion NOT required
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.config.tools_schema import TOOL_DEFINITIONS


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _prestamype_profile(*, inversionista="FONDO A", cci="00382100123456789012"):
    return {
        "account_id": "P04197",
        "loan_number": "P04197",
        "borrower_name": "PRUEBA CLIENTE CUATRO",
        "dni": "12345678",
        "currency_symbol": "S/",
        "balance": 81510.15,
        "saldo_por_cancelar": 81510.15,
        "cuota_esperada": 7031.91,
        "next_installment_amount": 7031.91,
        "cci": cci,
        "cuenta_bancaria": "20300001234",
        "inversionista": inversionista,
        "banco": "BCP",
        "days_overdue": 94,
        "status": "en_mora",
    }


# ── C.1: Tool schema contract ─────────────────────────────────────────────────

def _validar_schema() -> dict:
    for t in TOOL_DEFINITIONS:
        if t["name"] == "validar_comprobante":
            return t
    raise AssertionError("validar_comprobante not found in TOOL_DEFINITIONS")


def test_validar_comprobante_schema_monto_required():
    """monto must be in required list."""
    schema = _validar_schema()
    required = schema["parameters"].get("required", [])
    assert "monto" in required, f"monto must be required; got required={required}"


def test_validar_comprobante_schema_inversionista_optional():
    """inversionista must be a defined (optional) parameter, NOT in required."""
    schema = _validar_schema()
    props = schema["parameters"].get("properties", {})
    required = schema["parameters"].get("required", [])
    assert "inversionista" in props, "inversionista must be in schema properties"
    assert "inversionista" not in required, "inversionista must NOT be required"


def test_validar_comprobante_schema_id_credito_optional():
    """id_credito must be a defined (optional) parameter, NOT in required."""
    schema = _validar_schema()
    props = schema["parameters"].get("properties", {})
    required = schema["parameters"].get("required", [])
    assert "id_credito" in props, "id_credito must be in schema properties"
    assert "id_credito" not in required, "id_credito must NOT be required"


def test_validar_comprobante_schema_cci_not_required():
    """cci / cuenta_destino must NOT be in the required list."""
    schema = _validar_schema()
    required = schema["parameters"].get("required", [])
    assert "cci" not in required, "cci must not be required (server-side resolved)"
    assert "cuenta_destino" not in required, "cuenta_destino must not be required"
    assert "nro_operacion" not in required, "nro_operacion must not be required"
    assert "account_type" not in required, "account_type must not be required"


# ── C.2: validate_comprobante function interface ──────────────────────────────

@pytest.fixture
def tmp_comprobantes(tmp_path, monkeypatch):
    """Isolate the comprobante store to a temp path for each test."""
    from features.comprobantes import validator as v
    tmp_file = tmp_path / "comprobantes.json"
    monkeypatch.setattr(v, "_COMPROBANTES_PATH", tmp_file)
    return tmp_file


async def test_validate_comprobante_happy_path_pago_cuota(tmp_comprobantes):
    """monto=7031.91 (≈ cuota) → tipo=pago_cuota, estado=en_revision, audit has inversionista."""
    from features.comprobantes.validator import validar_comprobante
    profile = _prestamype_profile()
    result = await validar_comprobante(profile, monto=7031.91, inversionista="FONDO A")
    assert result["cuenta_valida"] is True
    assert result["tipo"] == "pago_cuota", f"expected pago_cuota, got {result['tipo']}"
    assert result["estado"] == "en_revision"
    assert result["dedup_ok"] is True
    # Audit must capture inversionista
    records = json.loads(tmp_comprobantes.read_text())
    assert len(records) == 1
    assert records[0]["inversionista"] == "FONDO A"


async def test_validate_comprobante_cancelacion(tmp_comprobantes):
    """monto=81510.15 (≈ saldo) → tipo=cancelacion."""
    from features.comprobantes.validator import validar_comprobante
    profile = _prestamype_profile()
    result = await validar_comprobante(profile, monto=81510.15, inversionista="FONDO A")
    assert result["tipo"] == "cancelacion"
    assert result["estado"] == "en_revision"


async def test_validate_comprobante_abono(tmp_comprobantes):
    """monto=50000 (between cuota and saldo) → tipo=abono."""
    from features.comprobantes.validator import validar_comprobante
    profile = _prestamype_profile()
    result = await validar_comprobante(profile, monto=50000.00, inversionista="FONDO A")
    assert result["tipo"] == "abono"
    assert result["estado"] == "en_revision"


# ── C.3: inversionista mismatch → WARN, NOT reject ───────────────────────────

async def test_validate_comprobante_inversionista_mismatch_warns_not_rejects(tmp_comprobantes):
    """Inversionista mismatch must set inversionista_match=False but NOT reject."""
    from features.comprobantes.validator import validar_comprobante
    profile = _prestamype_profile(inversionista="FONDO A")
    # User says they paid to FONDO B (mismatch)
    result = await validar_comprobante(profile, monto=7031.91, inversionista="FONDO B")
    assert result["cuenta_valida"] is True, "Must not reject on inversionista mismatch"
    assert result["inversionista_match"] is False, "inversionista_match must be False"
    assert result["estado"] == "en_revision", "Must stay en_revision (human conciliates)"
    # Audit must still record the submitted inversionista
    records = json.loads(tmp_comprobantes.read_text())
    assert records[0]["inversionista"] == "FONDO B"


async def test_validate_comprobante_inversionista_match_is_true_when_correct(tmp_comprobantes):
    """Correct inversionista → inversionista_match=True."""
    from features.comprobantes.validator import validar_comprobante
    profile = _prestamype_profile(inversionista="FONDO A")
    result = await validar_comprobante(profile, monto=7031.91, inversionista="FONDO A")
    assert result["inversionista_match"] is True


async def test_validate_comprobante_inversionista_none_match_is_none(tmp_comprobantes):
    """When inversionista not provided (None) → inversionista_match=None (not checked)."""
    from features.comprobantes.validator import validar_comprobante
    profile = _prestamype_profile(inversionista="FONDO A")
    result = await validar_comprobante(profile, monto=7031.91, inversionista=None)
    assert result["inversionista_match"] is None


# ── C.4: anti-dup by image_sha256 (not by monto) ─────────────────────────────

_SHA_A = "a" * 64  # fake sha256 hex digest — image A
_SHA_B = "b" * 64  # fake sha256 hex digest — image B (different file, same amount)


async def test_validate_comprobante_same_image_same_credito_is_dup(tmp_comprobantes):
    """Same image (same sha256) uploaded twice for same credito → second dedup_ok=False."""
    from features.comprobantes.validator import validar_comprobante
    profile = _prestamype_profile()
    first = await validar_comprobante(profile, monto=7031.91, image_sha256=_SHA_A)
    assert first["dedup_ok"] is True
    second = await validar_comprobante(profile, monto=7031.91, image_sha256=_SHA_A)
    assert second["dedup_ok"] is False, "Same image re-upload must be flagged as duplicate"


async def test_validate_comprobante_different_images_same_amount_both_accepted(tmp_comprobantes):
    """Two DIFFERENT images with the SAME (credito, monto) must BOTH be accepted.

    This is the core regression test for the old (credito, monto) dedup bug:
    previously the second would be flagged as duplicate even though it's a
    distinct payment voucher (different image file → different sha256).
    """
    from features.comprobantes.validator import validar_comprobante
    profile = _prestamype_profile()
    first = await validar_comprobante(profile, monto=7031.91, image_sha256=_SHA_A)
    assert first["dedup_ok"] is True, "First upload must be accepted"
    second = await validar_comprobante(profile, monto=7031.91, image_sha256=_SHA_B)
    assert second["dedup_ok"] is True, (
        "Different image (different sha256) with same monto must NOT be flagged as duplicate"
    )


async def test_validate_comprobante_same_image_different_credito_not_dup(tmp_comprobantes):
    """Same image sha256 on a DIFFERENT credito is NOT a duplicate (dedup is per-credito)."""
    from features.comprobantes.validator import validar_comprobante
    profile_a = _prestamype_profile()
    profile_b = {**_prestamype_profile(), "account_id": "P99999", "loan_number": "P99999"}
    first = await validar_comprobante(profile_a, monto=7031.91, image_sha256=_SHA_A)
    assert first["dedup_ok"] is True
    second = await validar_comprobante(profile_b, monto=7031.91, image_sha256=_SHA_A)
    assert second["dedup_ok"] is True, "Same sha256 on different credito must not be flagged"


async def test_validate_comprobante_sha256_stored_in_audit(tmp_comprobantes):
    """image_sha256 must be persisted in the audit record."""
    from features.comprobantes.validator import validar_comprobante
    profile = _prestamype_profile()
    await validar_comprobante(profile, monto=7031.91, image_sha256=_SHA_A)
    records = json.loads(tmp_comprobantes.read_text())
    assert records[0].get("image_sha256") == _SHA_A, "image_sha256 must be in the audit record"


async def test_validate_comprobante_no_sha256_still_works(tmp_comprobantes):
    """When image_sha256 is not provided (None), the call still succeeds without dedup check."""
    from features.comprobantes.validator import validar_comprobante
    profile = _prestamype_profile()
    result = await validar_comprobante(profile, monto=7031.91, image_sha256=None)
    assert result["cuenta_valida"] is True
    assert result["dedup_ok"] is True, "No sha256 → no dedup check → dedup_ok=True"


# ── C.5: CCI resolved server-side (not from user) ─────────────────────────────

async def test_validate_comprobante_cci_from_profile_not_user(tmp_comprobantes):
    """CCI in audit record must come from profile, never from user-supplied argument."""
    from features.comprobantes.validator import validar_comprobante
    profile = _prestamype_profile(cci="00382100123456789012")
    # User does NOT supply CCI — it's resolved from profile
    result = await validar_comprobante(profile, monto=7031.91, inversionista="FONDO A")
    assert result["cuenta_valida"] is True
    records = json.loads(tmp_comprobantes.read_text())
    # CCI in audit must come from profile, not any user input
    assert records[0].get("cci") == "00382100123456789012"


# ── C.6: tool_registry _validar_comprobante new signature ────────────────────

async def test_tool_registry_validar_comprobante_new_signature(tmp_comprobantes):
    """ToolRegistry._validar_comprobante must accept (monto, *, inversionista=None, id_credito=None).

    Does NOT require nro_operacion, cuenta_destino, account_type, or cci.
    """
    from api.tool_registry import ToolRegistry
    profile = _prestamype_profile()
    reg = ToolRegistry(
        identity_verified=True,
        debt_context=profile,
        tenant_id="prestamype",
    )
    # Must succeed with only monto
    result = await reg.execute("validar_comprobante", {"monto": 7031.91})
    assert result.get("blocked") != "identity_required"
    assert "cuenta_valida" in result or "tipo" in result, (
        f"Expected comprobante result, got: {result}"
    )
