"""Robust form reading: tolerant labels, fields, birth date and subject column."""

from dataclasses import replace
from datetime import date

import pytest

from alias_manager import AliasManager
from form_parser import (
    SOURCE_OCR_FIELD,
    SOURCE_OCR_PAGE,
    SOURCE_TEXT_LAYER,
    AttendanceFormParser,
    FormDetector,
    FormParser,
    FormType,
    SubjectCatalog,
    _find_anchors,
    _group_rows,
    parse_birth_date,
)
from ocr import OCRWord
from page_data import ExtractionSource, PageData, PageImage, PageWord
from review_queue import ReviewItem
from text_parser import TextParser


def _line(text, x0, top, gap=4.0, confidence=None):
    words, cursor = [], x0
    for token in text.split():
        width = max(8.0, len(token) * 5.0)
        words.append(PageWord(token, cursor, top, cursor + width, top + 10.0, confidence))
        cursor += width + gap
    return words


def _page(
    *,
    title="Formular Anwesenheitsnachweis",
    name="Sample Student",
    name_label="Name des Schülers",
    birth="01.01.2015",
    birth_label="Geburtsdatum",
    header=("Dauer", "GR/EZ", "Fach", "Ort"),
    rows=("Deutsch",) * 4,
    gr_ez=("EZ",) * 4,
    teacher_label="Name der Lehrkraft",
    extra=(),
    source=ExtractionSource.PDF_TEXT,
    confidence=None,
):
    columns = {"Dauer": 150, "GR/EZ": 205, "Fach": 245, "Ort": 300}
    words = []
    words += _line(title, 40, 70)
    if name:
        words += _line(name, 40, 140, confidence=confidence)
    words += _line(name_label, 40, 155)
    if birth:
        words += _line(birth, 360, 140)
    words += _line(birth_label, 360, 155)
    for label in header:
        words += _line(label, columns[label], 340 if label != "GR/EZ" else 334)
    for index, subject in enumerate(rows):
        top = 370 + index * 24
        words += _line(subject, 240, top, gap=0, confidence=confidence)
        if index < len(gr_ez) and "GR/EZ" in header:
            # Handwritten GR/EZ values may spill towards the Fach column.
            words += _line(gr_ez[index], 222, top)
    if teacher_label:
        words += _line(teacher_label, 40, 710, gap=1)
        words += _line("Teacher Example", 180, 710)
    words += list(extra)
    return PageData(595, 842, "", words=tuple(words), source=source)


def test_misread_labels_are_still_located():
    page = _page(title="Formular Anwesenheitsnachwels", name_label="Name des Schüters*")
    misread = {"Dauer": "Daucr", "GR/EZ": "GRIEZ"}
    page = replace(page, words=tuple(
        replace(word, text=misread.get(word.text, word.text)) for word in page.words
    ))

    result = FormParser().parse(page)

    assert result.form_type == FormType.ATTENDANCE
    assert result.student_name == "Sample Student"
    assert result.subject == "Deutsch"
    assert not result.requires_review


def _header_page(labels):
    words = []
    for index, label in enumerate(labels):
        words += _line(label, 40 + index * 120, 100)
    return PageData(595, 842, "", words=tuple(words))


def test_short_labels_must_match_exactly():
    assert not _find_anchors(_header_page(["Ont"]), "Ort")
    assert _find_anchors(_header_page(["Ort"]), "Ort")
    assert _find_anchors(_header_page(["Bach"]), "Fach")  # one error in four letters


def test_label_errors_are_bounded():
    assert _find_anchors(_header_page(["Anwosenheitsnochweis"]), "Anwesenheitsnachweis")
    assert not _find_anchors(_header_page(["Abwesenheitsgrund"]), "Anwesenheitsnachweis")
    unrelated = PageData(595, 842, "Anwesenheitsnachweis Name des Schülers Fachberatung")
    assert FormDetector().detect(unrelated).form_type == FormType.UNKNOWN


def test_cyrillic_lookalikes_in_text_layer_are_normalized():
    page = _page(title="Formular Аnwesenheitsnachweis", name="Іngrid Мusterfrau")

    result = FormParser().parse(page)

    assert result.form_type == FormType.ATTENDANCE
    assert result.student_name == "Ingrid Musterfrau"


def test_split_capital_and_signature_fragments_are_cleaned():
    extra = _line("~ Af/", 160, 141)
    page = _page(name="Muster I van", extra=extra)

    result = FormParser().parse(page)

    assert result.student_name == "Muster Ivan"


def test_skewed_line_is_grouped_into_one_row():
    words = [
        PageWord("Name", 40, 742, 60, 752),
        PageWord("der", 64, 736, 78, 746),
        PageWord("Lehrkraft", 82, 741, 120, 751),
    ]

    rows = _group_rows(words, 854)

    assert [[word.text for word in row] for row in rows] == [["Name", "der", "Lehrkraft"]]


@pytest.mark.parametrize("text,expected", [
    ("21.06.2013", date(2013, 6, 21)),
    ("2106 2013", date(2013, 6, 21)),
    ("21.O6.2O13", date(2013, 6, 21)),
    ("Geb. 3.4.2015", date(2015, 4, 3)),
])
def test_birth_date_variants(text, expected):
    assert parse_birth_date(text, today=date(2026, 10, 8))[0] == expected


@pytest.mark.parametrize("text", ["31.02.2012", "15.10.1950", "15.10.2026", "Schule"])
def test_invalid_or_implausible_birth_dates_are_rejected(text):
    assert parse_birth_date(text, today=date(2026, 10, 8)) is None


def test_birth_date_is_read_above_its_label():
    result = FormParser().parse(_page(birth="12.11.2016"))

    assert result.birth_date == date(2016, 11, 12)
    birth = next(item for item in result.evidence if item.field == "birth_date")
    assert birth.canonical == "2016-11-12"
    assert birth.bbox is not None


def test_missing_birth_date_is_not_guessed():
    result = FormParser().parse(_page(birth=None))

    assert result.birth_date is None
    assert not result.requires_review  # the date is evidence, not a required field


def test_gr_ez_values_never_become_subjects():
    page = _page(gr_ez=("EZ", "E2", "&z", "/EZ"))

    result = FormParser().parse(page)

    assert result.subject == "Deutsch"
    assert not result.warnings


@pytest.mark.parametrize("value,canonical", [
    ("Sachunt.", "Sachunterricht"),
    ("Informatik", "Informatik"),
    ("Englsch", "Englisch"),
    ("Blologle", "Biologie"),
    ("Sachkunde", "Sachkunde"),
    ("Mathe", "Mathematik"),
])
def test_subject_catalog_tolerates_abbreviations_and_ocr_errors(value, canonical):
    assert SubjectCatalog.load().match(value) == canonical


def test_ambiguous_fuzzy_subject_is_not_guessed():
    catalog = SubjectCatalog({"abcdef": "Eins", "abcxyz": "Zwei", "abcdxy": "Drei"})

    assert catalog.match("abcdxf") is None


def test_confirmed_alias_reaches_the_table_parser(monkeypatch, tmp_path):
    manager = AliasManager(tmp_path / "subjects_aliases.json", create_if_missing=True)
    manager.add_and_save("Phy.", "Physik")
    monkeypatch.setattr(TextParser, "_alias_manager", manager)

    result = FormParser().parse(_page(rows=("Phy.",) * 3))

    assert result.subject == "Physik"


def test_consistent_unknown_subject_is_proposed():
    result = FormParser().parse(_page(rows=("Robotik",) * 3))

    assert result.subject is None
    assert result.subject_proposal == "Robotik"
    assert result.requires_review


def test_misread_copy_of_a_well_supported_subject_does_not_block():
    result = FormParser().parse(_page(rows=("Deutsch",) * 5 + ("Oevisch",)))

    assert result.subject == "Deutsch"
    assert not result.requires_review
    subject = next(item for item in result.evidence if item.field == "subject")
    assert any("Oevisch" in item for item in subject.uncertainties)


def test_short_uncertain_ocr_fragments_do_not_block_but_words_do():
    rows = ("Deutsch",) * 4 + ("age",)
    fragment = _page(rows=rows, source=ExtractionSource.OCR, confidence=0.95)
    words = [
        replace(word, confidence=0.5) if word.text == "age" else word
        for word in fragment.words
    ]
    fragment = replace(fragment, words=tuple(words))

    assert FormParser().parse(fragment).subject == "Deutsch"
    assert not FormParser().parse(fragment).warnings
    assert FormParser().parse(_page(rows=("Deutsch",) * 4 + ("Robotik",))).requires_review


def test_text_below_the_table_is_ignored_without_teacher_label():
    footer = _line("Unterschrift bestätige Anwesenheitsnachweises", 200, 520)
    page = _page(teacher_label=None, extra=footer)

    result = FormParser().parse(page)

    assert result.subject == "Deutsch"
    assert not result.requires_review
    assert any("Lehrkraft" in note for note in result.notes)


class _FieldReader:
    def __init__(self, text):
        self.text = text
        self.boxes = []

    def __call__(self, image, box):
        self.boxes.append(box)
        left, top = box[0] + 10, box[1] + 5
        return tuple(
            OCRWord(token, left + index * 60, top, 50, 20, 0.93)
            for index, token in enumerate(self.text.split())
        )


def test_field_reading_and_text_layer_are_kept_as_evidence():
    reader = _FieldReader("Sample Student")
    text_layer = _page(name="Sample Studnet")
    ocr_page = replace(
        _page(source=ExtractionSource.OCR),
        image=PageImage(b"png", 1190, 1684, 144, reader=reader),
        alternatives=(text_layer,),
    )

    result = FormParser().parse(ocr_page)

    name = next(item for item in result.evidence if item.field == "student_name")
    assert [(reading.source, reading.value) for reading in name.readings] == [
        (SOURCE_OCR_PAGE, "Sample Student"),
        (SOURCE_OCR_FIELD, "Sample Student"),
        (SOURCE_TEXT_LAYER, "Sample Studnet"),
    ]
    assert reader.boxes  # pixel boxes of the field crops
    assert result.student_name == "Sample Student"


def test_name_from_field_reading_fills_a_missing_page_reading():
    reader = _FieldReader("Sample Student")
    page = replace(
        _page(name="~~ ##", source=ExtractionSource.OCR),
        image=PageImage(b"png", 1190, 1684, 144, reader=reader),
    )

    result = FormParser().parse(page)

    assert result.student_name == "Sample Student"
    assert any("zusätzlichen Lesung" in reason for reason in result.reasons)


def test_legacy_header_stops_at_misread_period_label():
    text = (
        "Example Student Deutsch\n"
        "Bewiligungszeitraum von 01.08.2026 bis 31.01.2027\n"
        "Monat: September\nUnterrichtsart:"
    )

    result = FormParser().parse(PageData(595, 842, text))

    assert result.student_name == "Example Student"
    assert result.subject == "Deutsch"
    assert not result.requires_review


def test_review_item_keeps_evidence_and_reads_old_entries():
    item = ReviewItem(
        "scan.pdf", "Sample Student", "Deutsch", "Prüfung", [], "",
        birth_date="2015-01-01",
        subject_proposal=None,
        field_evidence=[{"field": "student_name", "value": "Sample Student"}],
    )

    restored = ReviewItem.from_dict(item.to_dict())
    old = ReviewItem.from_dict({
        "file_path": "old.pdf", "raw_name": None, "raw_subject": None,
        "reason": "alt", "suggestions": [], "preview_text": "",
    })

    assert restored.field_evidence == item.field_evidence
    assert restored.birth_date == "2015-01-01"
    assert old.field_evidence == [] and old.birth_date is None


def test_attendance_parser_keeps_compatible_catalog_view():
    catalog = AttendanceFormParser._subject_catalog()

    assert catalog["sachunt"] == "Sachunterricht"
    assert catalog["informatik"] == "Informatik"
