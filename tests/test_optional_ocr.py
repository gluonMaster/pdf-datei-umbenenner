"""OCR reading tests; only the real-engine tests need a Tesseract install."""

from dataclasses import replace
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
from page_data import ExtractionSource, PageData, PageImage
from pdf_extractor import OCRFallbackExtractor, PDFExtractor
from processing_service import DocumentProcessingService, PlannedAction
from review_queue import ReviewStore
from student_registry import MatchStatus, Student, StudentMatch, StudentRegistry
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


def test_text_layer_is_kept_as_second_reading(tmp_path):
    # Replaces the former "text layer skips OCR" rule: scanner text layers
    # are often badly recognized, so OCR reads every page (decision 2026-10-08).
    page = PageData(
        595,
        842,
        "Sample Student Mathematik\nBewilligungszeitraum\nMonat Januar",
    )
    provider = FakeOCRProvider(OCRResult.success(_legacy_ocr_page()))
    extractor = OCRFallbackExtractor(StaticTextExtractor(page), provider)

    extracted = extractor.extract_page_data(tmp_path / "text.pdf")

    assert provider.calls == 1
    assert extracted.source == ExtractionSource.OCR
    assert extracted.ocr_status == OCRStatus.SUCCESS.value
    assert extracted.alternatives == (page,)


def test_ocr_failure_keeps_the_text_layer_for_review(tmp_path):
    page = PageData(595, 842, "Sample Student Mathematik\nBewilligungszeitraum")
    provider = FakeOCRProvider(OCRResult.failed("OCR fehlgeschlagen: Zeitlimit"))
    extractor = OCRFallbackExtractor(StaticTextExtractor(page), provider)

    extracted = extractor.extract_page_data(tmp_path / "text.pdf")

    assert extracted.source == ExtractionSource.PDF_TEXT
    assert extracted.text == page.text
    assert extracted.ocr_status == OCRStatus.FAILED.value
    assert extracted.diagnostics[0] == "OCR fehlgeschlagen: Zeitlimit"


def _tsv(*words):
    header = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext"
    )
    rows = [
        f"5\t1\t1\t1\t1\t{index}\t{index * 40}\t10\t30\t12\t91.5\t{word}"
        for index, word in enumerate(words, 1)
    ]
    return "\n".join([header, *rows])


def test_tsv_words_after_quotation_marks_are_not_swallowed():
    words = ['"Mit', "meiner", "„Anwesenheitsnachweis“", 'Schüler"', "Ende"]

    parsed = TesseractOCRProvider._parse_tsv(_tsv(*words))

    assert [word.text for word in parsed] == words
    assert parsed[-1].left == 200
    assert parsed[0].confidence == pytest.approx(0.915)


def test_parser_uses_text_layer_when_ocr_reading_has_no_structure():
    text_layer = PageData(
        595,
        842,
        "Sample Student Mathematik\nBewilligungszeitraum von 01.01.2026\n"
        "Monat Januar\nUnterrichtsart\nLehrer(in)",
    )
    ocr_reading = PageData(
        595,
        842,
        "unlesbar",
        source=ExtractionSource.OCR,
        alternatives=(text_layer,),
    )

    result = FormParser().parse(ocr_reading)

    assert result.form_type == FormType.LEGACY
    assert result.student_name == "Sample Student"
    assert any("Textebene" in reason for reason in result.reasons)


def _unstructured_ocr_page():
    return ocr_words_to_page_data(
        [OCRWord("unlesbar", 40, 40, 80, 20, 0.5)],
        image_width=600,
        image_height=800,
        page_width=600,
        page_height=800,
    )


class RotatingProvider:
    """Structure appears only after the configured rotation."""

    def __init__(self, upright_after=180, osd=None, image=None):
        self.upright_after = upright_after
        self.osd = osd
        self.image = image
        self.rotations = []

    def check_availability(self):
        return OCRAvailability(True, "fake provider")

    def recognize_first_page(self, _path, rotation=0, **_dimensions):
        self.rotations.append(rotation)
        page = (
            _legacy_ocr_page()
            if rotation == self.upright_after
            else _unstructured_ocr_page()
        )
        return OCRResult.success(replace(page, rotation=rotation, image=self.image))

    def detect_rotation(self, _image):
        return self.osd


def test_upside_down_page_is_recognized_once_more(tmp_path):
    provider = RotatingProvider(upright_after=180)
    extractor = OCRFallbackExtractor(StaticTextExtractor(PageData(600, 800, "")), provider)

    page = extractor.extract_page_data(tmp_path / "upside-down.pdf")

    assert provider.rotations == [0, 180]
    assert page.rotation == 180
    assert "180°" in page.diagnostics[-1]


def test_orientation_reported_by_osd_is_used(tmp_path):
    image = PageImage(b"png", 600, 800, 300)
    provider = RotatingProvider(upright_after=90, osd=90, image=image)
    extractor = OCRFallbackExtractor(StaticTextExtractor(PageData(600, 800, "")), provider)

    page = extractor.extract_page_data(tmp_path / "sideways.pdf")

    assert provider.rotations == [0, 90]
    assert page.rotation == 90


@pytest.mark.parametrize("upright_after,expected_calls", [(0, [0]), (None, [0, 180])])
def test_orientation_retry_is_bounded(tmp_path, upright_after, expected_calls):
    provider = RotatingProvider(upright_after=upright_after)
    extractor = OCRFallbackExtractor(StaticTextExtractor(PageData(600, 800, "")), provider)

    page = extractor.extract_page_data(tmp_path / "scan.pdf")

    assert provider.rotations == expected_calls
    assert page.rotation == 0


def test_rendered_image_is_released_after_parsing(tmp_path):
    source = tmp_path / "image-only.pdf"
    _image_only_pdf(source)
    page = replace(_legacy_ocr_page(), image=PageImage(b"png", 600, 800, 300))
    provider = FakeOCRProvider(OCRResult.success(page))
    extractor = OCRFallbackExtractor(PDFExtractor(), provider)

    result = _service(tmp_path, extractor).process_document(
        source, month=1, year=2035, dry_run=True
    )

    assert result.planned_action == PlannedAction.RENAME
    assert result.extraction.image is None


class NoOCRExtractor:
    def check_ocr_availability(self):
        return OCRAvailability(False, "OCR ist nicht verfügbar: Test-Tesseract fehlt.")

    def extract_page_data(self, _path):
        raise AssertionError("Without OCR no document may be processed.")


def test_cli_refuses_processing_without_ocr(monkeypatch, tmp_path, capsys):
    import main as main_module

    folder = tmp_path / "scans"
    folder.mkdir()
    (folder / "scan.pdf").write_bytes(b"%PDF synthetic")
    monkeypatch.setattr(main_module, "__file__", str(tmp_path / "main.py"))
    monkeypatch.setattr(main_module, "OCRFallbackExtractor", lambda *_a: NoOCRExtractor())

    code = main_module.main([str(folder), "--month", "1", "--year", "2030", "--dry-run"])

    output = capsys.readouterr().out
    assert code == main_module.EXIT_OCR_UNAVAILABLE
    assert "Test-Tesseract fehlt" in output and "setup.bat" in output
    assert sorted(item.name for item in folder.iterdir()) == ["scan.pdf"]


def test_gui_refuses_batch_without_ocr(monkeypatch):
    import gui_main

    gui = gui_main.PDFRenamerGUI.__new__(gui_main.PDFRenamerGUI)
    gui._student_task_running = False
    gui.processing = False
    gui.validate_input = lambda: True
    gui.extractor = NoOCRExtractor()
    logs, errors = [], []
    gui.log_message = logs.append
    monkeypatch.setattr(
        gui_main.messagebox,
        "showerror",
        lambda title, message: errors.append((title, message)),
    )
    monkeypatch.setattr(
        gui_main.threading,
        "Thread",
        lambda *_a, **_k: pytest.fail("Without OCR no batch may start."),
    )

    gui_main.PDFRenamerGUI.start_processing(gui)

    assert gui.processing is False
    assert errors and "setup.bat" in errors[0][1]
    assert logs and "Test-Tesseract fehlt" in logs[0]


def _provider_with_fake_executable(tmp_path):
    executable = tmp_path / "tesseract.exe"
    executable.write_bytes(b"fake")
    return TesseractOCRProvider(executable), executable


def test_successful_availability_is_reused_but_failure_is_not(monkeypatch, tmp_path):
    provider, executable = _provider_with_fake_executable(tmp_path)
    answers = [
        OCRAvailability(False, "fehlt"),
        OCRAvailability(True, "ok", executable, ("deu", "eng")),
    ]
    calls = []

    def query():
        calls.append(1)
        return answers[len(calls) - 1]

    monkeypatch.setattr(provider, "_query_availability", query)

    assert not provider.check_availability().available
    assert provider.check_availability().available
    assert provider.check_availability().available
    assert len(calls) == 2


@pytest.mark.parametrize("stdout,languages,expected", [
    (b"Orientation in degrees: 180\nRotate: 180\nOrientation confidence: 7.5\n",
     ("deu", "eng", "osd"), 180),
    (b"Rotate: 270\nOrientation confidence: 1.3\n", ("deu", "eng", "osd"), 90),
    (b"Rotate: 90\nOrientation confidence: 0.4\n", ("deu", "eng", "osd"), None),
    (b"Rotate: 180\nOrientation confidence: 7.5\n", ("deu", "eng"), None),
    (b"Too few characters. Skipping this page\n", ("deu", "eng", "osd"), None),
])
def test_osd_rotation_is_used_only_when_reliable(
    monkeypatch, tmp_path, stdout, languages, expected
):
    provider, executable = _provider_with_fake_executable(tmp_path)
    provider._available = OCRAvailability(True, "ok", executable, languages)
    monkeypatch.setattr(
        provider,
        "_run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, stdout=stdout, stderr=b""),
    )

    assert provider.detect_rotation(PageImage(b"png", 10, 10, 300)) == expected


def test_unavailable_ocr_is_diagnostic_manual_review(tmp_path):
    source = tmp_path / "unavailable.pdf"
    _image_only_pdf(source)
    provider = FakeOCRProvider(
        OCRResult.unavailable("OCR ist nicht verfügbar: Test-Tesseract fehlt.")
    )
    extractor = OCRFallbackExtractor(PDFExtractor(), provider)

    result = _service(tmp_path, extractor).process_document(
        source, month=2, year=2035
    )

    assert result.error is None
    assert result.planned_action == PlannedAction.MANUAL_REVIEW
    assert result.ocr_status == OCRStatus.UNAVAILABLE.value
    assert "OCR ist nicht verfügbar" in result.review_item.reason
    assert result.review_item.ocr_status == OCRStatus.UNAVAILABLE.value
    assert result.planned_path.exists()


def test_image_only_pdf_without_ocr_keeps_a_read_only_review_plan(tmp_path):
    source = tmp_path / "without-text-layer.pdf"
    _image_only_pdf(source)
    original = source.read_bytes()
    service = _service(tmp_path, PDFExtractor())

    result = service.process_document(source, month=8, year=2046, dry_run=True)

    assert result.error is None and result.requires_review
    assert result.extraction.diagnostics
    assert source.read_bytes() == original
    assert service.review_store.count() == 0
    assert not (tmp_path / "manual_review.json").exists()
    assert not (tmp_path / "manual_check").exists()


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
    assert availability.missing_languages == ("deu",)
    assert "Tesseract-Sprachdaten fehlen: deu" in availability.message


def _synthetic_legacy_image(extra_lines=()):
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
        *extra_lines,
    ]
    for index, line in enumerate(lines):
        draw.text((160, 180 + index * 180), line, fill="black", font=font)
    return image


def _real_provider_or_skip():
    provider = TesseractOCRProvider()
    availability = provider.check_availability()
    if not availability.available:
        pytest.skip(availability.message)
    return provider, availability


@pytest.mark.parametrize("turn", [90, 180])
def test_real_tesseract_recognizes_rotated_scan_when_available(tmp_path, turn):
    provider, availability = _real_provider_or_skip()
    if turn == 90 and "osd" not in availability.languages:
        pytest.skip("Tesseract-OSD-Daten fehlen; seitliche Seiten werden nicht erkannt.")
    # OSD needs enough text to decide the orientation reliably.
    filler = [f"Der Unterricht fand statt im Raum {index} am Vormittag" for index in range(8)]
    image = _synthetic_legacy_image(filler).rotate(turn, expand=True)
    source = tmp_path / "rotated.pdf"
    image.save(source, "PDF", resolution=300.0)

    page = OCRFallbackExtractor(PDFExtractor(), provider).extract_page_data(source)
    result = FormParser().parse(page)

    assert page.rotation in (90, 180, 270) and page.rotation != 0
    assert result.form_type == FormType.LEGACY
    assert result.student_name == "Sample Student"


def test_real_tesseract_image_only_pdf_when_available(tmp_path):
    provider, _availability = _real_provider_or_skip()
    image = _synthetic_legacy_image()
    source = tmp_path / "anonymous-image-only.pdf"
    image.save(source, "PDF", resolution=300.0)

    original = source.read_bytes()
    registry_path = tmp_path / "Schuler.csv"
    registry_path.write_text(
        "Nummer;Nachname;Vorname\n7;Sample;Student\n", encoding="utf-8"
    )
    service = _service(tmp_path, OCRFallbackExtractor(PDFExtractor(), provider))
    service.student_registry = StudentRegistry(registry_path)
    result = service.process_document(source, month=9, year=2046, dry_run=True)

    assert result.extraction.source == ExtractionSource.OCR
    assert result.form_type == FormType.LEGACY.value
    assert result.found_data.student_name == "Sample Student"
    assert result.canonical_subject == "Mathematik"
    assert result.succeeded, result.review_item or result.error
    assert result.planned_name == "Sample_Student_Mathematik_September_2046.pdf"
    assert source.read_bytes() == original
    assert not (tmp_path / "manual_review.json").exists()
