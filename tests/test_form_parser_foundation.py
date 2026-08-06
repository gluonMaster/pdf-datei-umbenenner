"""Synthetic tests for layout-aware old and new form parsing."""

from form_parser import FormDetector, FormParser, FormType
from page_data import ExtractionSource, PageCharacter, PageData, PageWord
from pdf_extractor import PDFExtractor


def _word(text, x0, top, scale=1.0):
    width = max(8.0, len(text) * 5.0)
    return PageWord(
        text=text,
        x0=x0 * scale,
        top=top * scale,
        x1=(x0 + width) * scale,
        bottom=(top + 10.0) * scale,
    )


def _line(text, x0, top, scale=1.0, gap=4.0):
    words = []
    cursor = x0
    for token in text.split():
        word = _word(token, cursor, top, scale)
        words.append(word)
        cursor += max(8.0, len(token) * 5.0) + gap
    return words


def _attendance_page(scale=1.0, split_anchors=False, subjects=None, teacher="Teacher Example"):
    subjects = subjects or ["Deutsch", "Deutsch", "Deutsch"]
    words = []
    words += _line("Formular Anwesenheitsnachweis", 40, 70, scale)
    words += _line("Sample Student", 40, 140, scale)
    words += _line("Name des Schülers", 40, 155, scale)
    words += _line("Geburtsdatum", 360, 155, scale)
    words += _line("Dauer", 180, 335, scale)
    words += _line("Fach", 242, 340, scale)
    words += _line("Ort", 300, 340, scale)
    for index, subject in enumerate(subjects):
        words += _line(subject, 238, 370 + index * 24, scale, gap=0)
    teacher_label = "Name de r Lehrkraft" if split_anchors else "Name der Lehrkraft"
    words += _line(teacher_label, 40, 710, scale, gap=1)
    words += _line(teacher, 180, 710, scale)
    text = "\n".join(
        [
            "Formular Anwesenheitsnachweis",
            "Sample Student 01.01.2015",
            "Name des Schülers Geburtsdatum",
            "Dauer Fach Ort",
            *subjects,
            f"{teacher_label} {teacher}",
        ]
    )
    return PageData(
        width=595 * scale,
        height=842 * scale,
        text=text,
        words=tuple(words),
        source=ExtractionSource.PDF_TEXT,
    )


def test_page_data_contains_full_text_coordinates_and_source(monkeypatch, tmp_path):
    class FakePage:
        width = 200
        height = 300
        chars = [{"text": "A", "x0": 10, "top": 20, "x1": 15, "bottom": 30}]

        def extract_text(self):
            return "first\nsecond\nthird"

        def extract_words(self, **_kwargs):
            return [{"text": "first", "x0": 10, "top": 20, "x1": 35, "bottom": 30}]

    class FakePdf:
        pages = [FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr("pdf_extractor.pdfplumber.open", lambda _path: FakePdf())
    path = tmp_path / "synthetic.pdf"

    page = PDFExtractor.extract_page_data(path)

    assert page is not None
    assert page.text == "first\nsecond\nthird"
    assert page.width == 200
    assert page.words[0].text == "first"
    assert page.characters == (PageCharacter("A", 10, 20, 15, 30),)
    assert page.source == ExtractionSource.PDF_TEXT
    assert PDFExtractor.extract_first_lines(path, 2) == "first\nsecond"


def test_detector_tolerates_punctuation_and_ocr_word_breaks():
    page = _attendance_page(split_anchors=True)
    page = PageData(
        width=page.width,
        height=page.height,
        text=page.text.replace("Anwesenheitsnachweis", "Anwesenheits-nachweis"),
        words=page.words,
    )

    detection = FormDetector().detect(page)

    assert detection.form_type == FormType.ATTENDANCE
    assert detection.confidence >= 0.95


def test_detector_accepts_attendance_form_with_one_missing_anchor():
    page = _attendance_page()
    page = PageData(
        width=page.width,
        height=page.height,
        text=page.text.replace("Anwesenheitsnachweis", "Nachweis"),
        words=page.words,
    )

    detection = FormDetector().detect(page)

    assert detection.form_type == FormType.ATTENDANCE
    assert 0.75 <= detection.confidence < 0.95


def test_legacy_strategy_preserves_name_and_subject_normalization():
    text = (
        "Example Student Mathe\n"
        "Bewilligungszeitraum von 01.01.2026 bis 31.01.2026\n"
        "Monat: Januar\nUnterrichtsart:"
    )
    page = PageData(width=595, height=842, text=text)

    result = FormParser().parse(page)

    assert result.form_type == FormType.LEGACY
    assert result.raw_student_name == "Example Student"
    assert result.student_name == "Example Student"
    assert result.raw_subject == "Mathe"
    assert result.subject == "Mathematik"
    assert not result.requires_review


def test_attendance_strategy_uses_relative_regions_at_different_scale():
    parser = FormParser()

    normal = parser.parse(_attendance_page(scale=1.0, teacher="Wrong Person"))
    scaled = parser.parse(_attendance_page(scale=1.7, teacher="Wrong Person"))

    for result in (normal, scaled):
        assert result.form_type == FormType.ATTENDANCE
        assert result.student_name == "Sample Student"
        assert result.subject == "Deutsch"
        assert result.raw_subject == "Deutsch"
        assert "Wrong Person" != result.student_name
        assert not result.requires_review


def test_attendance_strategy_joins_split_subject_words():
    result = FormParser().parse(
        _attendance_page(subjects=["Deu tsch", "Deu tsch", "Deutsch"])
    )

    assert result.subject == "Deutsch"
    assert result.raw_subject in {"Deu tsch", "Deutsch"}
    assert not result.warnings


def test_attendance_strategy_handles_missing_neighbor_anchor_with_warning():
    page = _attendance_page()
    words = tuple(word for word in page.words if word.text != "Ort")
    page = PageData(
        width=page.width,
        height=page.height,
        text=page.text.replace("Dauer Fach Ort", "Dauer Fach"),
        words=words,
    )

    result = FormParser().parse(page)

    assert result.form_type == FormType.ATTENDANCE
    assert result.student_name == "Sample Student"
    assert result.subject == "Deutsch"
    assert result.requires_review
    assert any("'Ort' fehlt" in warning for warning in result.warnings)


def test_conflicting_subjects_without_majority_require_review():
    result = FormParser().parse(
        _attendance_page(subjects=["Deutsch", "Englisch"])
    )

    assert result.form_type == FormType.ATTENDANCE
    assert result.subject is None
    assert result.raw_subject == "Deutsch, Englisch"
    assert result.requires_review
    assert any("ohne Mehrheit" in warning for warning in result.warnings)


def test_conflicting_subjects_with_majority_are_reported():
    result = FormParser().parse(
        _attendance_page(subjects=["Englisch", "Deutsch", "Deutsch"])
    )

    assert result.subject == "Deutsch"
    assert result.requires_review
    assert any("Abweichende Fachwerte" in warning for warning in result.warnings)


def test_unknown_form_has_diagnostics_for_manual_review():
    result = FormParser().parse(PageData(595, 842, "Unrelated document"))

    assert result.form_type == FormType.UNKNOWN
    assert result.student_name is None
    assert result.subject is None
    assert result.requires_review
    assert result.reasons
    assert result.warnings
