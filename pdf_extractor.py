"""PDF text and layout extraction."""

from pathlib import Path
from typing import Optional

import pdfplumber

from page_data import ExtractionSource, PageCharacter, PageData, PageWord


class PDFExtractor:
    """Extract text and positioned elements from PDF files."""

    @staticmethod
    def extract_page_data(pdf_path: Path) -> Optional[PageData]:
        """Extract complete text and positioned elements from the first page.

        Coordinates use pdfplumber's top-origin page coordinate system. Returning
        a structured page keeps layout-sensitive parsing independent of PDF file
        names and of a fixed line limit.
        """
        try:
            with pdfplumber.open(pdf_path) as pdf:
                if not pdf.pages:
                    return None

                page = pdf.pages[0]
                text = page.extract_text() or ""
                words = tuple(
                    PageWord(
                        text=str(item.get("text", "")),
                        x0=float(item["x0"]),
                        top=float(item["top"]),
                        x1=float(item["x1"]),
                        bottom=float(item["bottom"]),
                    )
                    for item in page.extract_words(
                        x_tolerance=2,
                        y_tolerance=3,
                        keep_blank_chars=False,
                    )
                )
                characters = tuple(
                    PageCharacter(
                        text=str(item.get("text", "")),
                        x0=float(item["x0"]),
                        top=float(item["top"]),
                        x1=float(item["x1"]),
                        bottom=float(item["bottom"]),
                    )
                    for item in page.chars
                )

                return PageData(
                    width=float(page.width),
                    height=float(page.height),
                    text=text,
                    words=words,
                    characters=characters,
                    source=ExtractionSource.PDF_TEXT,
                )
        except Exception as error:
            print(f"Fehler beim Lesen der Datei {pdf_path.name}: {error}")
            return None

    @classmethod
    def extract_text(cls, pdf_path: Path) -> Optional[str]:
        """Return the complete text of the first page."""

        page_data = cls.extract_page_data(pdf_path)
        if page_data is None or not page_data.text:
            return None
        return page_data.text

    @classmethod
    def extract_first_page(cls, pdf_path: Path) -> Optional[PageData]:
        """Compatibility alias for the structured first-page API."""

        return cls.extract_page_data(pdf_path)

    @classmethod
    def extract_first_lines(
        cls,
        pdf_path: Path,
        num_lines: int = 10,
    ) -> Optional[str]:
        """Return the first N lines for callers using the legacy text API."""

        text = cls.extract_text(pdf_path)
        if not text:
            return None
        return "\n".join(text.splitlines()[:num_lines])

    @staticmethod
    def is_valid_pdf(pdf_path: Path) -> bool:
        """Return whether a path points to a readable, non-empty PDF."""

        if not pdf_path.exists() or not pdf_path.is_file():
            return False
        if pdf_path.suffix.lower() != ".pdf":
            return False

        try:
            with pdfplumber.open(pdf_path) as pdf:
                return len(pdf.pages) > 0
        except Exception:
            return False
