"""Comprobante de pago validator for PrestamYpe.

Carved from tools/cobranza.py (lines 708-870). Handles voucher registration,
dedup, and classification. Identity always comes from the verified profile
injected server-side — never from LLM or user input.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from shared.config.settings import settings
from shared.debt_math import classify_tipo, normalize_cci

# Local dedup store: a comprobante's nº de operación seen once is flagged on
# any repeat. JSON list of records so the demo can show the audit trail. Lives
# under COBRANZA_COMPROBANTE_DIR (mounted volume), NOT /tmp, so it persists with
# the uploaded images. Tests override _COMPROBANTES_PATH via monkeypatch.
_COMPROBANTES_PATH = Path(settings.comprobante_dir) / "comprobantes.json"


def _load_comprobantes() -> list[dict]:
    if not _COMPROBANTES_PATH.exists():
        return []
    try:
        return json.loads(_COMPROBANTES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_comprobantes(items: list[dict]) -> None:
    _COMPROBANTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _COMPROBANTES_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


_TIPO_LABELS = {"pago": "Pago", "abono": "Abono", "cancelacion": "Cancelación"}

# Account-type labels for the destination account the user paid into. In Perú a
# CCI (Código de Cuenta Interbancario) has exactly 20 digits and is used for
# inter-bank transfers; a plain número de cuenta is shorter and used for
# same-bank transfers. We store the type so the human reconciler knows which.
_ACCOUNT_TYPE_LABELS = {"cci": "CCI", "cuenta": "Número de cuenta"}


def _normalize_account_type(account_type: str | None) -> str:
    """Coerce the destination-account type to 'cci' | 'cuenta' (default 'cci')."""
    t = (account_type or "").strip().lower()
    return t if t in _ACCOUNT_TYPE_LABELS else "cci"


def registrar_comprobante_foto(profile: dict, media_url: str) -> dict:
    """Register a voucher PHOTO (no OCR) for the verified borrower (PrestamYpe).

    Camino B del flujo "subir pago por WhatsApp": el deudor manda la FOTO del
    voucher en lugar de tipear los datos. NO hay OCR, así que NO clasificamos
    monto ni tipo: solo dejamos constancia de la imagen recibida, asociada al
    crédito verificado, para conciliación MANUAL posterior. ``estado`` queda en
    ``en_revision`` igual que el camino tipeado.

    Identity/credit ALWAYS come from the verified ``profile``. The ONLY input is
    the ``media_url`` (the chathub-hosted image). Dedup por media_url + crédito
    para no duplicar si el deudor reenvía la misma foto. Never raises.
    """
    credito = profile.get("account_id")
    url = (media_url or "").strip()
    items = _load_comprobantes()
    duplicate = any(
        r.get("media_url") == url and r.get("credito") == credito and url
        for r in items
    )
    if not duplicate:
        items.append({
            "credito": credito,
            "dni": profile.get("dni"),
            "media_url": url,
            "source": "foto",
            "monto": None,
            "nro_operacion": None,
            "tipo": None,
            "estado": "en_revision",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        _save_comprobantes(items)
    return {
        "registered": not duplicate,
        "duplicate": duplicate,
        "credito": credito,
        "media_url": url,
        "estado": "en_revision",
    }


def _classify_payment(monto: float, cuota: float, saldo: float) -> str:
    """Classify a payment amount against the credit's cuota and saldo.

    Uses classify_tipo from shared.debt_math (±2% tolerance) and maps to the
    spec's three-category taxonomy:
      pago_cuota   — monto ≈ cuota (installment payment)
      abono        — monto > 0 but below cuota or between cuota and saldo
      cancelacion  — monto ≈ saldo (full cancellation)
    """
    raw = classify_tipo(float(monto), float(cuota), float(saldo))
    # classify_tipo returns "pago" for cuota match — map to spec label.
    return "pago_cuota" if raw == "pago" else raw


async def validar_comprobante(
    profile: dict,
    monto: float,
    *,
    inversionista: str | None = None,
    id_credito: str | None = None,
) -> dict:
    """Register a payment voucher for the verified borrower (PrestamYpe).

    Lighter flow (Slice C): only monto is required from the user.
    CCI is resolved server-side from the verified profile — never from user input.
    inversionista is compared against the credit's known inversionista:
      - match     → inversionista_match=True
      - mismatch  → inversionista_match=False, WARN (not reject), estado=en_revision
      - not given → inversionista_match=None (not checked)

    Anti-dup: keyed by (credito, monto) within the dedup store. Same credito+monto
    in the same session → dedup_ok=False, no second record.

    CCI is stored from the verified profile for human bank reconciliation.
    The audit record captures inversionista (user-provided value).

    Returns:
      cuenta_valida        bool  — always True when profile resolves
      credito              str   — account_id from profile
      tipo                 str   — pago_cuota | abono | cancelacion
      cuota_esperada       float
      saldo_por_cancelar   float
      inversionista        str   — from profile (authoritative)
      inversionista_match  bool|None
      dedup_ok             bool
      estado               str   — always "en_revision"
      mensaje              str
    """
    credito = profile.get("account_id")

    # (a) classification against the verified credit — independent of user input
    cuota = float(profile.get("cuota_esperada") or profile.get("next_installment_amount") or 0.0)
    saldo = float(profile.get("saldo_por_cancelar") or profile.get("balance") or 0.0)
    tipo = _classify_payment(float(monto or 0.0), cuota, saldo)
    tipo_label = _TIPO_LABELS.get(tipo, tipo.upper())

    # (b) inversionista server-side match check (non-blocking)
    profile_inv = (profile.get("inversionista") or "").strip()
    user_inv = (inversionista or "").strip() if inversionista is not None else None
    if user_inv is None:
        inv_match: bool | None = None
    else:
        inv_match = user_inv.upper() == profile_inv.upper()

    # (c) CCI from verified profile — never from user input
    profile_cci = normalize_cci(profile.get("cci") or "")

    # (d) anti-dup by (credito, monto) — same amount to same credit = duplicate
    monto_f = float(monto or 0.0)
    items = _load_comprobantes()
    duplicate = any(
        r.get("credito") == credito and float(r.get("monto") or 0.0) == monto_f
        for r in items
    )
    dedup_ok = not duplicate

    if duplicate:
        mensaje = (
            f"Ya registramos un comprobante por ese monto (S/ {monto_f:,.2f}) "
            f"para tu crédito {credito}. No lo registré de nuevo para evitar duplicados."
        )
    else:
        record: dict = {
            "credito": credito,
            "dni": profile.get("dni"),
            "cci": profile_cci,  # server-side, from profile
            "monto": monto_f,
            "tipo": tipo,
            "inversionista": user_inv,          # user-provided (may differ from profile)
            "inversionista_profile": profile_inv,  # authoritative (for reconciliation)
            "inversionista_match": inv_match,
            "id_credito_user": id_credito,      # user-provided optional reference
            "estado": "en_revision",
            "source": "typed",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        if inv_match is False:
            record["alerta"] = "inversionista_mismatch"
        items.append(record)
        _save_comprobantes(items)
        if inv_match is False:
            mensaje = (
                f"Recibimos tu comprobante de {tipo_label} (S/ {monto_f:,.2f}) "
                f"sobre el crédito {credito}. Quedó EN REVISIÓN. "
                f"Notamos que el inversionista que indicaste ({user_inv!r}) no coincide "
                f"con el registrado — nuestro equipo lo revisará."
            )
        else:
            mensaje = (
                f"Recibimos tu comprobante de {tipo_label} (S/ {monto_f:,.2f}) "
                f"sobre el crédito {credito}. Quedó EN REVISIÓN: "
                f"un asesor lo concilia contra el banco y, de estar conforme, "
                f"se aplica a tu cuenta."
            )

    return {
        "cuenta_valida": True,
        "credito": credito,
        "tipo": tipo,
        "cuota_esperada": cuota,
        "saldo_por_cancelar": saldo,
        "inversionista": profile_inv or None,
        "inversionista_match": inv_match,
        "dedup_ok": dedup_ok,
        "estado": "en_revision",
        "mensaje": mensaje,
    }
