"""Optional OCR fallback tests; the normal suite needs no Tesseract install."""

from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFont
import pytest

from file_renamer import FileRenamer
from form_parser import FormParser, FormType
from ocr import (
    OCRAvailability,
    OCRResult,
    OCRStatus,
    OCRWord,
    TesseractOCRProvider,
    ocr_words_to_page_data,
)
from page_data import ExtractionSource, PageData
from pdf_extractor import OCRFallbackExtractor, PDFExtractor
from processing_service import DocumentProcessingService, PlannedAction
from review_queue import ReviewStore
from student_registry import MatchStatus, Student, StudentMatch
from validator import Validator


class FakeOCRProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def check_availability(self):
        return OCRAvailability(self.error is None, "fake provider")

    def recognize_first_page(self, _path, **_dimensions):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class StaticTextExtractor:
    def __init__(self, page):
        self.page = page

    def extract_page_data(self, _path):
        return self.page


class ExactRegistry:
    def match(self, _raw_name):
        return StudentMatch(
            MatchStatus.EXACT,
            Student(7, "Sample", "Student"),
            (),
            "exact",
        )


def _image_only_pdf(path: Path) -> None:
    image = Image.new("RGB", (600, 800), "white")
    image.save(path, "PDF", resolution=72.0)


def _legacy_ocr_page(field_confidence=0.96):
    lines = [
        "Sample Student Mathematik",
        "Bewilligungszeitraum von 01.01.2026 bis 31.01.2026",
        "Monat Januar",
        "Unterrichtsart",
        "Lehrer(in)",
    ]
    words = []
    for line_number, line in enumerate(lines, 1):
        left = 40
        for token in line.split():
            confidence = (
                field_confidence
                if token in {"Sample", "Student", "Mathematik"}
                else 0.97
            )
            width = max(20, len(token) * 10)
            words.append(
                OCRWord(
                    token,
                    left,
                    50 + line_number * 45,
                    width,
                    24,
                    confidence,
                    (1, 1, 1, line_number),
                )
            )
            left += width + 12
    return ocr_words_to_page_data(
        words,
        image_width=600,
        image_height=800,
        page_width=600,
        page_height=800,
    )


def _service(tmp_path, extractor):
    return DocumentProcessingService(
        extractor=extractor,
        parser=FormParser(),
        student_registry=ExactRegistry(),
        validator=Validator(),
        review_store=ReviewStore(tmp_path / "manual_review.json"),
        renamer=FileRenamer(),
        manual_review_folder=tmp_path / "manual_check",
    )


def test_fake_ocr_success_reuses_existing_parser_pipeline(tmp_path):
    source = tmp_path / "image-only.pdf"
    _image_only_pdf(source)
    provider = FakeOCRProvider(OCRResult.success(_legacy_ocr_page()))
    extractor = OCRFallbackExtractor(PDFExtractor(), provider)

    result = _service(tmp_path, extractor).process_document(
        source, month=1, year=2035, dry_run=True
    )

    assert provider.calls == 1
    assert result.error is None
    assert result.extraction.source == ExtractionSource.OCR
    assert result.form_type == FormType.LEGACY.value
    assert result.found_data.student_name == "Sample Student"
    assert result.canonical_subject == "Mathematik"
    assert result.planned_action == PlannedAction.RENAME
    assert result.ocr_status == OCRStatus.SUCCESS.value


def test_text_layer_does_not_call_ocr(tmp_path):
    page = PageData(
        595,
        842,
        "Sample Student Mathematik\nBewilligungszeitraum\nMonat Januar",
    )
    provider = FakeOCRProvider(OCRResult.success(_legacy_ocr_page()))
    extractor = OCRFallbackExtractor(StaticTextExtractor(page), provider)

    extracted = extractor.extract_page_data(tmp_path / "text.pdf")

    assert extracted is page
    assert provider.calls == 0


def test_unavailable_ocr_is_diagnostic_manual_review(tmp_path):
    source = tmp_path / "unavailable.pdf"
    _image_only_pdf(source)
    provider = FakeOCRProvider(
        OCRResult.unavailable("OCR unavailable: fake Tesseract is missing.")
    )
    extractor = OCRFallbackExtractor(PDFExtractor(), provider)

    result = _service(tmp_path, extractor).process_document(
        source, month=2, year=2035
    )

    assert result.error is None
    assert result.planned_action == PlannedAction.MANUAL_REVIEW
    assert result.ocr_status == OCRStatus.UNAVAILABLE.value
    assert "OCR unavailable" in result.review_item.reason
    assert result.review_item.ocr_status == OCRStatus.UNAVAILABLE.value
    assert result.planned_path.exists()


def test_ocr_provider_exception_is_controlled(tmp_path):
    source = tmp_path / "exception.pdf"
    _image_only_pdf(source)
    provider = FakeOCRProvider(error=RuntimeError("synthetic OCR crash"))
    extractor = OCRFallbackExtractor(PDFExtractor(), provider)

    page = extractor.extract_page_data(source)

    assert page is not None
    assert page.ocr_status == OCRStatus.FAILED.value
    assert "synthetic OCR crash" in page.diagnostics[0]


def test_low_field_confidence_prevents_automatic_rename(tmp_path):
    source = tmp_path / "low-confidence.pdf"
    _image_only_pdf(source)
    provider = FakeOCRProvider(OCRResult.success(_legacy_ocr_page(0.41)))
    extractor = OCRFallbackExtractor(PDFExtractor(), provider)

    result = _service(tmp_path, extractor).process_document(
        source, month=3, year=2035, dry_run=True
    )

    assert result.error is None
    assert result.planned_action == PlannedAction.MANUAL_REVIEW
    assert result.found_data.name_confidence == pytest.approx(0.41)
    assert result.found_data.subject_confidence == pytest.approx(0.41)
    assert "Niedrige OCR-Konfidenz" in result.review_item.reason


@pytest.mark.parametrize(
    ("image_size", "word_box"),
    [
        ((600, 800), (100, 200, 50, 20)),
        ((1200, 1600), (200, 400, 100, 40)),
    ],
)
def test_ocr_coordinates_scale_independently_of_render_resolution(
    image_size, word_box
):
    left, top, width, height = word_box
    page = ocr_words_to_page_data(
        [OCRWord("Test", left, top, width, height, 0.9)],
        image_width=image_size[0],
        image_height=image_size[1],
        page_width=600,
        page_height=800,
    )

    assert page.words[0].x0 == pytest.approx(100)
    assert page.words[0].top == pytest.approx(200)
    assert page.words[0].x1 == pytest.approx(150)
    assert page.words[0].bottom == pytest.approx(220)
    assert page.words[0].confidence == pytest.approx(0.9)


def test_tesseract_reports_missing_language_data(monkeypatch, tmp_path):
    executable = tmp_path / "tesseract.exe"
    executable.write_bytes(b"fake")
    provider = TesseractOCRProvider(executable)
    monkeypatch.setattr(provider, "_load_pymupdf", lambda: object())
    monkeypatch.setattr(
        provider,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, stdout=b"List of available languages (1):\neng\n", stderr=b""
        ),
    )

    availability = provider.check_availability()

    assert not availability.available
    assert "missing Tesseract language data: deu" in availability.message


def test_real_tesseract_image_only_pdf_when_available(tmp_path):
    provider = TesseractOCRProvider()
    availability = provider.check_availability()
    if not availability.available:
        pytest.skip(availability.message)

    image = Image.new("RGB", (2480, 3508), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 70)
    except OSError:
        font = ImageFont.load_default()
    lines = [
        "Sample Student Mathematik",
        "Bewilligungszeitraum von 01.01.2026 bis 31.01.2026",
        "Monat Januar",
        "Unterrichtsart",
        "Lehrer(in)",
    ]
    for index, line in enumerate(lines):
        draw.text((160, 180 + index * 180), line, fill="black", font=font)
    source = tmp_path / "anonymous-image-only.pdf"
    image.save(source, "PDF", resolution=300.0)

    page = OCRFallbackExtractor(PDFExtractor(), provider).extract_page_data(source)
    parsed = FormParser().parse(page)

    assert page.source == ExtractionSource.OCR
    assert parsed.form_type == FormType.LEGACY
    assert parsed.student_name == "Sample Student"
    assert parsed.subject == "Mathematik"
