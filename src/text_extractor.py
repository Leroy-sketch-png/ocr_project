"""Text-based PDF token extraction using PyMuPDF (fitz).

Uses fitz (PyMuPDF) for fast C-based text extraction, falling back to
pdfplumber only if fitz is unavailable. Coordinates are scaled to match
the Tesseract-300DPI pixel space so dpi_scale=1.0 works correctly.
"""
import logging
from typing import List

from .models import Token

logger = logging.getLogger(__name__)

# PDF points (1/72 inch) to Tesseract-300DPI pixels conversion.
# Tesseract renders at 300 DPI; PDF coordinates are in points (72 DPI).
_PDF_PT_TO_PX = 300.0 / 72.0  # ≈ 4.1667

_HAS_FITZ = False
try:
    import fitz  # type: ignore
    _HAS_FITZ = True
except ImportError:
    pass


def has_extractable_text(pdf_path: str) -> bool:
    """Check if the PDF has extractable text (vs scanned images)."""
    if _HAS_FITZ:
        try:
            doc = fitz.open(pdf_path)
            total = sum(len(page.get_text().strip()) for page in doc)
            doc.close()
            return total > 500
        except Exception:
            return False
    # Fallback to pdfplumber
    import pdfplumber
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[:5]:
                text = page.extract_text() or ""
                if len(text.strip()) > 200:
                    return True
            return False
    except Exception:
        return False


def _scale_bbox(x0: float, y0: float, x1: float, y1: float):
    """Scale PDF point coordinates to 300 DPI pixel space."""
    return (
        int(x0 * _PDF_PT_TO_PX),
        int(y0 * _PDF_PT_TO_PX),
        int(x1 * _PDF_PT_TO_PX),
        int(y1 * _PDF_PT_TO_PX),
    )


def extract_tokens_from_pdf(pdf_path: str) -> List[Token]:
    """Extract all tokens from a text-based PDF.

    Uses PyMuPDF (fitz) if available (fast), falls back to pdfplumber.

    Returns a flat list of Token objects with text, page, and bbox.
    Coordinates are scaled to match the Tesseract-300DPI pixel space.
    """
    if _HAS_FITZ:
        return _extract_with_fitz(pdf_path)

    logger.warning("PyMuPDF not installed, falling back to pdfplumber (slower)")
    return _extract_with_pdfplumber(pdf_path)


def _extract_with_fitz(pdf_path: str) -> List[Token]:
    """Fast text extraction using PyMuPDF."""
    tokens: List[Token] = []
    doc = fitz.open(pdf_path)
    try:
        for page_num in range(doc.page_count):
            page = doc[page_num]
            # get_text("words") returns list of tuples:
            # (x0, y0, x1, y1, word, block_no, line_no, word_no)
            words = page.get_text("words")
            for w in words:
                text = w[4].strip()
                if not text:
                    continue
                x0, y0, x1, y1 = _scale_bbox(w[0], w[1], w[2], w[3])
                tokens.append(
                    Token(
                        text=text,
                        page=page_num + 1,  # fitz is 0-indexed
                        bbox=(x0, y0, x1, y1),
                        confidence=100.0,
                    )
                )
    finally:
        doc.close()
    return tokens


def _extract_with_pdfplumber(pdf_path: str) -> List[Token]:
    """Fallback text extraction using pdfplumber."""
    import pdfplumber
    tokens: List[Token] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            words = page.extract_words() or []
            for w in words:
                text = w.get("text", "").strip()
                if not text:
                    continue
                x0, y0, x1, y1 = _scale_bbox(
                    w["x0"], w["top"], w["x1"], w["bottom"]
                )
                tokens.append(
                    Token(
                        text=text,
                        page=page_num,
                        bbox=(x0, y0, x1, y1),
                        confidence=100.0,
                    )
                )
    return tokens
