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


def _get_cronograma_for_validation(account_id: str, tenant_id: str) -> list[dict]:
    """Thin wrapper around get_cronograma — exists as a module-level name so tests
    can monkeypatch it without touching the Doris source directly.

    Returns a list of {n_cuota, fecha_venc, monto, estado} dicts ordered by
    n_cuota, or [] on any error / unavailability.
    """
    try:
        from features.cobranza.doris_debt_source import get_cronograma  # noqa: PLC0415

        return get_cronograma(account_id, tenant_id)
    except Exception:  # noqa: BLE001
        return []


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


async def validar_comprobante(
    profile: dict,
    monto: float,
    nro_operacion: str,
    cuenta_destino: str | None = None,
    account_type: str = "cci",
    cci: str | None = None,  # backward-compat alias for cuenta_destino
    n_cuota: str | None = None,  # CPR-01: installment number (correlativo 1,2,3…)
    tenant_id: str = "",
) -> dict:
    """Register a payment voucher for the verified borrower (PrestamYpe).

    The destination account is NOT validated for pertenencia: it is stored
    as-is as a voucher attribute (the real bank reconciliation is done later by
    a human). We never reject by account number.

    The account the user paid INTO can be entered as either a full CCI (Código
    de Cuenta Interbancario, exactly 20 digits — inter-bank transfer) or a plain
    número de cuenta (shorter — same-bank transfer). ``account_type`` records
    which one it is; ``cuenta_destino`` is the number (digits only). The legacy
    ``cci`` kwarg is still accepted as an alias for ``cuenta_destino``.

    Logic (against the server-injected ``profile`` = verified credit):
      (a) classify ``tipo`` from ``monto`` vs the DNI's credit cuota / saldo
          (±2% tolerance): ≈ cuota → "pago"; < cuota → "abono"; ≈ saldo total
          → "cancelacion". The classification depends ONLY on the monto.
      (b) dedup ``nro_operacion`` against a local JSON store; if seen before →
          ``dedup_ok = False`` (duplicate flagged), no re-registration.

    Identity/credit ALWAYS come from the verified ``profile`` — only the voucher
    fields (account_type, cuenta_destino, monto, nro_operacion) come from the
    user. The result is queued for human reconciliation. ``cuenta_valida`` is
    always ``True`` (kept for the widget contract); the only soft failure is a
    duplicate ``nro_operacion``.
    """
    account_type = _normalize_account_type(account_type)
    cuenta_in = normalize_cci(cuenta_destino if cuenta_destino is not None else (cci or ""))
    credito = profile.get("account_id")

    # CPR-01: n_cuota correlativo validation — only when tenant_id is provided.
    # When tenant_id is absent the caller is a legacy/non-Prestamype path; skip
    # all n_cuota gates to preserve backward compatibility.
    #
    # (i)  tenant_id set + None → required-field error; do NOT proceed.
    # (ii) Non-positive-integer string → re-ask; do NOT proceed.
    # (iii) Cronograma available + n_cuota not in it → re-ask.
    # (iv)  Cronograma unavailable (empty) → accept best-effort; do NOT block.
    if tenant_id:
        if n_cuota is None:
            return {
                "ncuota_required": True,
                "n_cuota": None,
                "mensaje": "Por favor indicá el número de cuota (por ejemplo, 1, 2, 3…).",
            }

        # Validate that n_cuota is a positive integer string
        try:
            n_cuota_int = int(n_cuota)
            if n_cuota_int <= 0:
                raise ValueError("non-positive")
        except (ValueError, TypeError):
            return {
                "ncuota_reask": True,
                "n_cuota": n_cuota,
                "mensaje": (
                    "El número de cuota debe ser un número entero positivo "
                    "(por ejemplo, 1, 2, 3…). ¿Podés indicarme el número correcto?"
                ),
            }

        # Cross-validate against cronograma when available
        cronograma = _get_cronograma_for_validation(credito or "", tenant_id)
        if cronograma:  # non-empty → we can validate
            valid_correlativos = {
                row["n_cuota"] for row in cronograma if row.get("n_cuota") is not None
            }
            if n_cuota_int not in valid_correlativos:
                valid_range = (
                    f"1 a {max(valid_correlativos)}" if valid_correlativos else "tu cronograma"
                )
                return {
                    "ncuota_reask": True,
                    "n_cuota": n_cuota,
                    "mensaje": (
                        f"El número de cuota {n_cuota} no coincide con tu cronograma "
                        f"(cuotas disponibles: {valid_range}). "
                        f"¿Podés confirmar o ingresar el número correcto?"
                    ),
                }
        # cronograma empty → Doris unavailable; accept best-effort (no blocking)

    # (a) tipo de operación — contra el crédito del DNI (NO depende de la CCI)
    cuota = float(profile.get("cuota_esperada") or profile.get("next_installment_amount") or 0.0)
    saldo = float(profile.get("saldo_por_cancelar") or profile.get("balance") or 0.0)
    tipo = classify_tipo(monto, cuota, saldo)
    tipo_label = _TIPO_LABELS.get(tipo, tipo.upper())

    # (b) dedup por nº de operación (por crédito)
    nro = (nro_operacion or "").strip()
    items = _load_comprobantes()
    duplicate = any(
        r.get("nro_operacion") == nro and r.get("credito") == credito
        for r in items
    )
    dedup_ok = not duplicate

    if duplicate:
        mensaje = (
            f"Este comprobante (operación {nro}) ya lo recibimos antes para tu "
            f"crédito {credito}. No lo registré de nuevo para evitar duplicados."
        )
    else:
        # Register for human reconciliation. We store BOTH the account type
        # (cci | cuenta) and the number; ``cci`` is kept for backward compat
        # with any reader that still expects that key.
        items.append({
            "credito": credito,
            "dni": profile.get("dni"),
            "account_type": account_type,
            "cuenta_destino": cuenta_in,
            "cci": cuenta_in,  # legacy alias (same value as cuenta_destino)
            "monto": float(monto or 0.0),
            "nro_operacion": nro,
            "n_cuota": n_cuota,  # CPR-01: installment correlativo (1, 2, 3…)
            "tipo": tipo,
            "estado": "en_revision",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        _save_comprobantes(items)
        cuenta_label = _ACCOUNT_TYPE_LABELS.get(account_type, "cuenta")
        mensaje = (
            f"Recibimos tu comprobante de pago. Lo registramos como {tipo_label} "
            f"sobre tu crédito {credito}, {cuenta_label} ···{cuenta_in[-4:]}. "
            f"Será validado y, de estar conforme, se aplicará a tu cuenta."
        )

    return {
        "cuenta_valida": True,
        "credito": credito,
        "tipo": tipo,
        "account_type": account_type,
        "cuenta_destino": cuenta_in,
        "n_cuota": n_cuota,  # CPR-01: passed through for ChatHub/asesor context
        "dedup_ok": dedup_ok,
        "mensaje": mensaje,
    }
