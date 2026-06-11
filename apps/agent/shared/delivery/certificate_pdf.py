"""Certificate-of-no-debt PDF generator (DEMO).

Renders a "Certificado de No Adeudo" PDF using reportlab. Pure presentation —
the eligibility decision (balance == 0) is made by the tool, not here. All data
is fictitious.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

# Vox brand palette (dark + gold) reused so the artifact matches the demo theme.
_GOLD = colors.HexColor("#f5c518")
_DARK = colors.HexColor("#18181b")
_MUTED = colors.HexColor("#71717a")
_TEXT = colors.HexColor("#1a1a1c")

_OUTPUT_DIR = Path("/tmp/prestaunion_certificates")
_MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _fecha_larga(d: date) -> str:
    return f"{d.day} de {_MESES_ES[d.month - 1]} de {d.year}"


def generate_certificate(
    *,
    folio: str,
    borrower_name: str,
    business_name: str,
    loan_number: str,
    company_name: str,
    cancelled_at: str | None = None,
) -> Path:
    """Render the certificate PDF and return its path.

    Args are pre-resolved server-side (borrower profile). ``company_name``
    must be supplied by the caller (resolved from tenant config → name).
    Returns the file path on disk; the caller turns it into a download link.
    """
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _OUTPUT_DIR / f"{folio}.pdf"

    c = canvas.Canvas(str(out_path), pagesize=A4)
    width, height = A4
    margin = 25 * mm

    # ── Header band (dark with gold wordmark) ──
    c.setFillColor(_DARK)
    c.rect(0, height - 38 * mm, width, 38 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(margin, height - 22 * mm, "Presta")
    presta_w = c.stringWidth("Presta", "Helvetica-Bold", 22)
    c.setFillColor(_GOLD)
    c.drawString(margin + presta_w, height - 22 * mm, "Union")
    c.setFillColor(colors.HexColor("#a1a1aa"))
    c.setFont("Helvetica", 9)
    c.drawString(margin, height - 30 * mm, "Soluciones de financiamiento para MYPEs")

    c.setFillColor(_GOLD)
    c.setFont("Helvetica-Bold", 9)
    c.drawRightString(width - margin, height - 22 * mm, f"FOLIO  {folio}")
    c.setFillColor(colors.HexColor("#a1a1aa"))
    c.setFont("Helvetica", 8)
    c.drawRightString(width - margin, height - 28 * mm, _fecha_larga(date.today()))

    # ── Title ──
    y = height - 58 * mm
    c.setFillColor(_TEXT)
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(width / 2, y, "CERTIFICADO DE NO ADEUDO")
    c.setStrokeColor(_GOLD)
    c.setLineWidth(2)
    c.line(width / 2 - 45 * mm, y - 4 * mm, width / 2 + 45 * mm, y - 4 * mm)

    # ── Body ──
    y -= 24 * mm
    c.setFillColor(_TEXT)
    c.setFont("Helvetica", 11)
    leading = 7 * mm

    cancel_txt = f" cancelado el {cancelled_at}" if cancelled_at else " cancelado en su totalidad"
    body_lines = [
        f"{company_name} deja constancia de que el/la titular:",
        "",
    ]
    for line in body_lines:
        c.drawString(margin, y, line)
        y -= leading

    # Highlighted borrower block
    c.setFillColor(colors.HexColor("#f7f8fa"))
    c.rect(margin, y - 26 * mm, width - 2 * margin, 28 * mm, fill=1, stroke=0)
    c.setFillColor(_TEXT)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin + 6 * mm, y - 4 * mm, borrower_name)
    c.setFont("Helvetica", 10)
    c.setFillColor(_MUTED)
    c.drawString(margin + 6 * mm, y - 11 * mm, f"Razón social:  {business_name}")
    c.drawString(margin + 6 * mm, y - 17 * mm, f"Préstamo:  {loan_number}")
    y -= 38 * mm

    c.setFillColor(_TEXT)
    c.setFont("Helvetica", 11)
    para = (
        f"no mantiene deuda pendiente con {company_name} respecto del préstamo "
        f"de la referencia, el cual ha sido{cancel_txt}. "
        "En consecuencia, a la fecha de emisión del presente documento, la cuenta "
        "registra saldo CERO y se encuentra en condición de NO ADEUDO."
    )
    # naive word-wrap
    words = para.split()
    line = ""
    max_w = width - 2 * margin
    for w in words:
        test = (line + " " + w).strip()
        if c.stringWidth(test, "Helvetica", 11) > max_w:
            c.drawString(margin, y, line)
            y -= leading
            line = w
        else:
            line = test
    if line:
        c.drawString(margin, y, line)
        y -= leading

    # ── Signature / footer ──
    y -= 18 * mm
    c.setStrokeColor(_MUTED)
    c.setLineWidth(0.6)
    c.line(margin, y, margin + 65 * mm, y)
    c.setFillColor(_MUTED)
    c.setFont("Helvetica", 9)
    c.drawString(margin, y - 5 * mm, f"{company_name} · Área de Cobranzas")

    c.setFillColor(_MUTED)
    c.setFont("Helvetica-Oblique", 7.5)
    c.drawCentredString(
        width / 2, 14 * mm,
        "Documento generado automáticamente para fines de demostración. Datos ficticios.",
    )
    c.drawCentredString(
        width / 2, 10 * mm,
        f"Verificable con el folio {folio}.",
    )

    c.showPage()
    c.save()
    return out_path
