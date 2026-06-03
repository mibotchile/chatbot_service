"""Pure math/string utilities for debt domain calculations.

Extracted here so both features/cobranza and features/comprobantes can import
them without creating cross-feature dependencies. No external dependencies.
"""

from __future__ import annotations

import re


def normalize_cci(cci: str) -> str:
    """Strip everything but digits from a CCI (Doris stores some with spaces)."""
    return re.sub(r"\D", "", cci or "")


def classify_tipo(monto: float, cuota: float, saldo: float, tol: float = 0.02) -> str:
    """Classify a payment amount as pago / abono / cancelacion.

    - ``monto`` ≈ ``saldo`` (±tol) → ``cancelacion`` (pays the whole balance)
    - ``monto`` ≈ ``cuota`` (±tol) → ``pago`` (regular installment)
    - ``monto`` < ``cuota``        → ``abono`` (partial)
    - otherwise (monto > cuota but not full saldo) → ``abono`` (extra partial)

    Cancelación is checked first so a single-installment loan (cuota == saldo)
    is reported as a full cancelation.
    """

    def _to_float(v) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    monto = _to_float(monto)
    if saldo > 0 and abs(monto - saldo) <= tol * saldo:
        return "cancelacion"
    if cuota > 0 and abs(monto - cuota) <= tol * cuota:
        return "pago"
    if cuota > 0 and monto < cuota:
        return "abono"
    return "abono"
