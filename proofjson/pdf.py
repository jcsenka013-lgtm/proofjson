"""Text-layer reading. Scanned pages with no text are left for a later OCR pass."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from proofjson.errors import ProofJsonError

_BLANK_LINES = re.compile(r"\n\s*\n")


@dataclass(frozen=True, slots=True)
class Page:
    number: int
    text: str


def read_pdf(path: str | Path) -> list[Page]:
    """Read one PDF into 1-based pages of extracted text."""
    pdf_path = Path(path)
    if not pdf_path.is_file():
        raise ProofJsonError(f"PDF not found: {pdf_path}")
    try:
        reader = PdfReader(str(pdf_path))
        if reader.is_encrypted:
            raise ProofJsonError("Encrypted PDFs are not supported in v0.1.")
        pages = [
            Page(number=index, text=page.extract_text() or "")
            for index, page in enumerate(reader.pages, start=1)
        ]
    except ProofJsonError:
        raise
    except PdfReadError as exc:
        raise ProofJsonError(f"Could not read PDF: {exc}") from exc
    except Exception as exc:
        raise ProofJsonError(f"Could not read PDF: {exc}") from exc
    if not pages:
        raise ProofJsonError(f"PDF has no pages: {pdf_path}")
    if not any(page.text.strip() for page in pages):
        raise ProofJsonError(
            "No text layer found in this PDF. Scanned pages need OCR, which is planned for v0.2."
        )
    return pages


def last_context(text: str, limit: int = 800) -> str:
    """Tail carried onto the next page: the last paragraph, or the last ``limit`` characters."""
    stripped = text.strip()
    if not stripped:
        return ""
    parts = [part.strip() for part in _BLANK_LINES.split(stripped) if part.strip()]
    snippet = parts[-1] if parts else stripped
    if len(snippet) > limit:
        snippet = snippet[-limit:]
    return snippet
