"""PDF text extraction plus an optional OCR fallback adapter."""

from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

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


class OCRFallbackExtractor:
    """Use OCR only when the normal first-page text layer is clearly weak."""

    def __init__(
        self,
        text_extractor: Optional[Any] = None,
        ocr_provider: Optional[Any] = None,
        *,
        minimum_text_characters: int = 20,
        minimum_text_words: int = 3,
    ) -> None:
        if minimum_text_characters < 0 or minimum_text_words < 0:
            raise ValueError("Grenzwerte für die Textebene dürfen nicht negativ sein.")
        self.text_extractor = text_extractor or PDFExtractor()
        if ocr_provider is None:
            from ocr import TesseractOCRProvider

            ocr_provider = TesseractOCRProvider()
        self.ocr_provider = ocr_provider
        self.minimum_text_characters = minimum_text_characters
        self.minimum_text_words = minimum_text_words

    def extract_page_data(self, pdf_path: Path) -> Optional[PageData]:
        page = self.text_extractor.extract_page_data(Path(pdf_path))
        if self.has_sufficient_text_layer(page):
            return page

        try:
            result = self.ocr_provider.recognize_first_page(
                Path(pdf_path),
                page_width=getattr(page, "width", None),
                page_height=getattr(page, "height", None),
            )
        except Exception as error:
            from ocr import OCRResult

            result = OCRResult.failed(
                f"OCR fehlgeschlagen: {str(error).strip() or error.__class__.__name__}"
            )

        if result.page_data is not None:
            ocr_page = result.page_data
            diagnostics = tuple(ocr_page.diagnostics)
            if not diagnostics and result.message:
                diagnostics = (result.message,)
            return replace(
                ocr_page,
                source=ExtractionSource.OCR,
                diagnostics=diagnostics,
                ocr_status=result.status.value,
            )

        diagnostic = result.message or result.status.value
        if page is None:
            return PageData(
                width=0.0,
                height=0.0,
                text="",
                diagnostics=(diagnostic,),
                ocr_status=result.status.value,
            )
        return replace(
            page,
            diagnostics=tuple(page.diagnostics) + (diagnostic,),
            ocr_status=result.status.value,
        )

    def has_sufficient_text_layer(self, page: Optional[PageData]) -> bool:
        if page is None:
            return False
        character_count = sum(character.isalnum() for character in page.text)
        word_count = max(len(page.words), len(page.text.split()))
        return (
            character_count >= self.minimum_text_characters
            and word_count >= self.minimum_text_words
        )

    def extract_first_page(self, pdf_path: Path) -> Optional[PageData]:
        return self.extract_page_data(pdf_path)

    def extract_text(self, pdf_path: Path) -> Optional[str]:
        page = self.extract_page_data(pdf_path)
        return page.text if page is not None and page.text else None


__all__ = ["OCRFallbackExtractor", "PDFExtractor"]
