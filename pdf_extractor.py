"""PDF text-layer extraction and the OCR-first reading of scanned pages."""

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Optional

import pdfplumber

from page_data import ExtractionSource, PageCharacter, PageData, PageWord


class PDFExtractor:
    """Extract text and positioned elements from PDF files."""

    @staticmethod
    def extract_page_data(pdf_path: Path) -> PageData:
        """Extract complete text and positioned elements from the first page.

        Coordinates use pdfplumber's top-origin page coordinate system. Returning
        a structured page keeps layout-sensitive parsing independent of PDF file
        names and of a fixed line limit. Unreadable/empty PDFs return diagnostic
        pages so callers can pass the failure to manual review.
        """
        try:
            with pdfplumber.open(pdf_path) as pdf:
                if not pdf.pages:
                    return PageData(0.0, 0.0, "", diagnostics=("PDF enthält keine Seiten.",))

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
                    diagnostics=() if text.strip() else (
                        "Keine lesbare Textebene auf der ersten PDF-Seite.",
                    ),
                )
        except Exception as error:
            return PageData(
                0.0, 0.0, "",
                diagnostics=(f"Fehler beim Lesen der PDF ({error.__class__.__name__}): {error}",),
            )

    @classmethod
    def extract_text(cls, pdf_path: Path) -> Optional[str]:
        """Return the complete text of the first page."""

        page_data = cls.extract_page_data(pdf_path)
        if page_data is None or not page_data.text:
            return None
        return page_data.text

    @classmethod
    def extract_first_page(cls, pdf_path: Path) -> PageData:
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
    """Read every first page with OCR; keep the PDF text layer as a second reading.

    The scanner's own text layer is often badly recognized, so it is no longer
    the primary source. It stays available as ``alternatives`` of the OCR
    reading and as the only reading when OCR fails for a document. If neither
    reading shows a known form structure, the page is recognized once more in
    the orientation reported by OSD, or upside down when OSD is unavailable.
    """

    def __init__(
        self,
        text_extractor: Optional[Any] = None,
        ocr_provider: Optional[Any] = None,
        *,
        minimum_text_characters: int = 20,
        minimum_text_words: int = 3,
        structure_check: Optional[Callable[[PageData], bool]] = None,
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
        self.structure_check = structure_check or _has_known_form_structure

    def check_ocr_availability(self):
        """Availability of the OCR engine, never raising."""

        try:
            return self.ocr_provider.check_availability()
        except Exception as error:
            from ocr import OCRAvailability

            return OCRAvailability(
                False,
                f"OCR ist nicht verfügbar: {str(error).strip() or error.__class__.__name__}",
            )

    def extract_page_data(self, pdf_path: Path) -> Optional[PageData]:
        pdf_path = Path(pdf_path)
        page = self.text_extractor.extract_page_data(pdf_path)
        result = self._recognize(pdf_path, page)

        if result.page_data is not None:
            text_readings = (page,) if self.has_sufficient_text_layer(page) else ()
            ocr_page = self._as_ocr_reading(result, text_readings)
            if not any(self.structure_check(item) for item in ocr_page.readings):
                ocr_page = self._retry_orientation(pdf_path, page, ocr_page, text_readings)
            return ocr_page

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
            diagnostics=(diagnostic,) + tuple(page.diagnostics),
            ocr_status=result.status.value,
        )

    def has_sufficient_text_layer(self, page: Optional[PageData]) -> bool:
        """Whether the PDF text layer is worth keeping as a second reading."""

        if page is None:
            return False
        character_count = sum(character.isalnum() for character in page.text)
        word_count = max(len(page.words), len(page.text.split()))
        return (
            character_count >= self.minimum_text_characters
            and word_count >= self.minimum_text_words
        )

    def _recognize(self, pdf_path: Path, page: Optional[PageData], rotation: int = 0):
        from ocr import OCRResult

        options = {
            "page_width": getattr(page, "width", None),
            "page_height": getattr(page, "height", None),
        }
        if rotation:
            options["rotation"] = rotation
        try:
            return self.ocr_provider.recognize_first_page(pdf_path, **options)
        except Exception as error:
            return OCRResult.failed(
                f"OCR fehlgeschlagen: {str(error).strip() or error.__class__.__name__}"
            )

    @staticmethod
    def _as_ocr_reading(result, text_readings) -> PageData:
        ocr_page = result.page_data
        diagnostics = tuple(ocr_page.diagnostics)
        if not diagnostics and result.message:
            diagnostics = (result.message,)
        return replace(
            ocr_page,
            source=ExtractionSource.OCR,
            diagnostics=diagnostics,
            ocr_status=result.status.value,
            alternatives=tuple(text_readings),
        )

    def _retry_orientation(
        self,
        pdf_path: Path,
        text_page: Optional[PageData],
        ocr_page: PageData,
        text_readings,
    ) -> PageData:
        """One bounded retry for pages scanned sideways or upside down."""

        rotation = None
        detect = getattr(self.ocr_provider, "detect_rotation", None)
        if ocr_page.image is not None and callable(detect):
            try:
                rotation = detect(ocr_page.image)
            except Exception:
                rotation = None
        if rotation is None:
            rotation = 180
        if rotation == 0:
            return ocr_page
        result = self._recognize(pdf_path, text_page, rotation)
        if result.page_data is None:
            return ocr_page
        rotated = self._as_ocr_reading(result, text_readings)
        if not self.structure_check(rotated):
            return ocr_page
        return replace(
            rotated,
            diagnostics=rotated.diagnostics + (
                f"Seite war gedreht; für die Erkennung um {rotation}° gedreht.",
            ),
        )

    def extract_first_page(self, pdf_path: Path) -> Optional[PageData]:
        return self.extract_page_data(pdf_path)

    def extract_text(self, pdf_path: Path) -> Optional[str]:
        page = self.extract_page_data(pdf_path)
        return page.text if page is not None and page.text else None


def _has_known_form_structure(page: PageData) -> bool:
    from form_parser import FormDetector, FormType

    return FormDetector().detect(page).form_type != FormType.UNKNOWN


OCR_SETUP_HINT = (
    "Die Verarbeitung benötigt Tesseract mit deutschen Sprachdaten (deu). "
    "Bitte setup.bat ausführen oder Tesseract installieren."
)


def ocr_unavailable_reason(extractor: Any) -> Optional[str]:
    """Message explaining why processing cannot start, or ``None`` if it can.

    Injected extractors without an OCR check (test doubles) are accepted.
    """

    check = getattr(extractor, "check_ocr_availability", None)
    if not callable(check):
        return None
    availability = check()
    if getattr(availability, "available", False):
        return None
    return str(getattr(availability, "message", "") or "OCR ist nicht verfügbar.")


__all__ = [
    "OCR_SETUP_HINT",
    "OCRFallbackExtractor",
    "PDFExtractor",
    "ocr_unavailable_reason",
]
