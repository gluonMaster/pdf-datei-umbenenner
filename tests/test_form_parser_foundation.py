"""Synthetic tests for layout-aware old and new form parsing."""

from dataclasses import replace

import pytest

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
        words=tuple(word for word in page.words if word.text != "Anwesenheitsnachweis"),
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


def test_attendance_strategy_notes_missing_neighbor_anchor():
    # scan_recognition_v3: an estimated boundary is informational when the
    # column content is consistent; formerly every missing anchor blocked.
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
    assert not result.requires_review
    assert any("'Ort' fehlt" in note for note in result.notes)


def test_estimated_boundary_blocks_when_column_content_is_thin():
    page = _attendance_page(subjects=["Deutsch", "Deutsch"])
    words = tuple(word for word in page.words if word.text != "Ort")

    result = FormParser().parse(replace(page, words=words, text=page.text.replace(" Ort", "")))

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


def test_complete_first_page_is_parsed_beyond_ten_lines(monkeypatch, tmp_path):
    page = _attendance_page()
    page = replace(page, text="\n".join(["Form header"] * 12 + [page.text]))
    monkeypatch.setattr(PDFExtractor, "extract_page_data", lambda _path: page)

    path = tmp_path / "arbitrary-file-name.pdf"
    assert "Fach" not in PDFExtractor.extract_first_lines(path)
    assert "Fach" in PDFExtractor.extract_text(path)
    result = FormParser().parse(PDFExtractor.extract_first_page(path))
    assert result.student_name == "Sample Student"
    assert result.subject == "Deutsch"


@pytest.mark.parametrize("scale", [0.2, 1.0, 3.0])
def test_positioned_anchors_tolerate_baseline_jitter_and_word_breaks(scale):
    page = _attendance_page(scale=scale, split_anchors=True)
    words = [
        replace(word, top=word.top + (index % 3) * scale,
                bottom=word.bottom + (index % 3) * scale)
        for index, word in enumerate(page.words)
    ]
    # Detection and parsing work from word coordinates even without flat text.
    result = FormParser().parse(replace(page, words=words, text=""))

    assert result.student_name == "Sample Student"
    assert result.subject == "Deutsch"
    assert not result.requires_review


def test_punctuation_suffix_on_teacher_anchor_stops_subject_collection():
    page = _attendance_page()
    words = [word for word in page.words if word.top < 710]
    words += _line("Name derLehrkraft(Druckbuchstaben)", 40, 500)
    words += _line("Unrelated Adult", 350, 500)
    for top in (530, 550, 570, 590):
        words += _line("Englisch", 238, top)

    result = FormParser().parse(replace(page, words=words))

    assert result.subject == "Deutsch"
    assert not result.requires_review


@pytest.mark.parametrize("missing", ["Geburtsdatum", "Dauer", "Ort", "Name der Lehrkraft"])
def test_missing_region_boundary_is_reported(missing):
    page = _attendance_page()
    if missing == "Name der Lehrkraft":
        words = [word for word in page.words if word.top != 710]
    else:
        words = [word for word in page.words if word.text != missing]

    result = FormParser().parse(replace(page, text=page.text.replace(missing, ""), words=words))

    assert result.student_name == "Sample Student"
    assert result.subject == "Deutsch"
    # Estimated, but the field content is plausible and consistent.
    assert not result.requires_review
    assert any(f"'{missing}'" in note for note in result.notes)


def test_neighbor_anchors_must_belong_to_table_header():
    page = _attendance_page()
    # Earlier unrelated mentions must not supply the table's column boundaries.
    extra = _line("Dauer", 20, 250) + _line("Ort", 500, 250)
    result = FormParser().parse(replace(page, words=list(page.words) + extra))

    assert result.subject == "Deutsch"
    assert not result.requires_review


def test_subject_anchor_is_selected_from_table_header():
    page = _attendance_page()
    words = list(page.words) + _line("Fach", 40, 240)
    result = FormParser().parse(replace(page, words=words))

    assert result.subject == "Deutsch"
    assert not result.requires_review


@pytest.mark.parametrize("missing", ["Name des Schülers", "Fach"])
def test_missing_field_anchor_requires_review_without_teacher_fallback(missing):
    page = _attendance_page()
    words = [
        word for word in page.words
        if not (word.top == 155 and word.x0 < 360 if missing == "Name des Schülers"
                else word.text == "Fach")
    ]
    result = FormParser().parse(replace(page, words=words, text=page.text.replace(missing, "")))

    assert result.form_type == FormType.ATTENDANCE
    assert result.requires_review
    assert any(f"'{missing}'" in warning for warning in result.warnings)
    if missing == "Fach":
        assert result.subject is None
    else:
        assert result.student_name is None


def test_subject_plurality_without_strict_majority_requires_review():
    result = FormParser().parse(_attendance_page(subjects=["Deutsch", "Deutsch", "Englisch", "Mathe"]))

    assert result.subject is None
    assert result.requires_review
    assert any("ohne Mehrheit" in warning for warning in result.warnings)


@pytest.mark.parametrize("cell", ["Deutsch Englisch", "Deutsch/Englisch"])
def test_conflicting_subjects_in_one_cell_are_not_silently_discarded(cell):
    page = _attendance_page(subjects=["Deutsch"])
    words = [word for word in page.words if word.top != 370]
    if "/" in cell:
        words.append(PageWord(cell, 238, 370, 270, 380))
    else:
        words += [PageWord("Deutsch", 235, 370, 251, 380), PageWord("Englisch", 253, 370, 270, 380)]
    result = FormParser().parse(replace(page, words=words))

    assert result.subject is None
    assert "Deutsch" in result.raw_subject and "Englisch" in result.raw_subject
    assert result.requires_review


def test_unknown_subject_evidence_is_preserved():
    result = FormParser().parse(_attendance_page(subjects=["Testfach"]))

    assert result.raw_subject == "Testfach"
    assert result.subject is None
    assert result.requires_review


def test_known_subject_does_not_hide_unknown_rows():
    result = FormParser().parse(_attendance_page(subjects=["Deutsch", "Deutsch", "Testfach"]))

    assert result.subject == "Deutsch"
    assert result.requires_review
    assert any("Testfach" in warning for warning in result.warnings)


def test_date_and_service_labels_cannot_become_student_name():
    page = _attendance_page()
    words = [word for word in page.words if word.top != 140]
    words += _line("Name des Schülers", 40, 140)
    words += _line("01.01.2015", 360, 140)

    result = FormParser().parse(replace(page, words=words))

    assert result.student_name is None
    assert result.requires_review


def test_birth_date_is_excluded_from_positioned_student_name():
    page = _attendance_page()
    words = list(page.words) + _line("01.01.2015", 360, 140)
    result = FormParser().parse(replace(page, words=words))

    assert result.student_name == "Sample Student"
    assert not result.requires_review


def test_legacy_parser_cannot_take_missing_student_fields_from_teacher():
    page = PageData(595, 842, "Lehrer(in):\nUnrelated Adult Deutsch\nMonat Januar\nUnterrichtsart")

    result = FormParser().parse(page)

    assert result.form_type == FormType.LEGACY
    assert result.student_name is None
    assert result.subject is None
    assert result.requires_review


def test_anchor_substrings_do_not_classify_unrelated_document():
    text = "Anwesenheitsnachweis Name des Schülers Fachberatung Lehrkraftplanung"
    result = FormParser().parse(PageData(595, 842, text))

    assert result.form_type == FormType.UNKNOWN


def test_pdf_read_failure_has_structured_diagnostics(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a PDF")

    page = PDFExtractor.extract_page_data(path)
    result = FormParser().parse(page)

    assert page.diagnostics
    assert any("Fehler beim Lesen" in reason for reason in result.reasons)
    assert result.requires_review
    assert PDFExtractor.extract_text(path) is None
    assert PDFExtractor.extract_first_lines(path) is None


def test_pdf_with_no_pages_has_structured_diagnostics(monkeypatch, tmp_path):
    class EmptyPdf:
        pages = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr("pdf_extractor.pdfplumber.open", lambda _path: EmptyPdf())
    page = PDFExtractor.extract_page_data(tmp_path / "empty.pdf")

    assert "keine Seiten" in page.diagnostics[0]
    assert FormParser().parse(page).requires_review
