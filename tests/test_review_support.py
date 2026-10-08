"""Display-independent review helpers: search, preselection, status, previews."""

from datetime import date
import json
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from review_preview import render_preview
from review_queue import ReviewItem
from review_support import (
    MATCH,
    RECOMMENDED,
    SIMILAR,
    add_subject_to_config,
    candidate_numbers,
    field_boxes,
    format_iso_date,
    is_value_list,
    parse_operator_date,
    preselected_number,
    rank_students,
    status_lines,
)
from student_registry import Student

STUDENTS = (
    Student(1, "Van Buren", "Alex"),
    Student(2, "Muster", "Max"),
    Student(3, "Muster", "Moritz"),
    Student(4, "Probe", "Paula"),
    Student(12, "Beispiel", "Bea"),
)


def _numbers(choices):
    return [choice.number for choice in choices]


def test_empty_query_lists_recommended_first_in_their_order():
    choices = rank_students(STUDENTS, "", recommended=(3, 2))

    assert _numbers(choices)[:2] == [3, 2]
    assert [choice.kind for choice in choices[:2]] == [RECOMMENDED, RECOMMENDED]
    assert choices[0].label.startswith("★ Nr. 3")
    assert set(_numbers(choices)) == {1, 2, 3, 4, 12}


@pytest.mark.parametrize("query,expected", [
    ("muster", [2, 3]),
    ("max mus", [2]),            # several parts in any order
    ("  MORITZ   muster ", [3]),  # case and whitespace ignored
    ("12", [12]),                # by number
    ("van bu", [1]),             # compound surname, prefixes
])
def test_search_by_name_parts_and_number(query, expected):
    assert _numbers(rank_students(STUDENTS, query)) == expected


def test_recommended_students_lead_among_equal_matches():
    assert _numbers(rank_students(STUDENTS, "muster", recommended=(3,))) == [3, 2]


def test_similar_names_are_offered_and_marked_when_nothing_matches():
    choices = rank_students(STUDENTS, "Mustr Mx")

    assert choices and choices[0].kind == SIMILAR
    assert choices[0].label.startswith("≈")
    assert rank_students(STUDENTS, "Zz") == []


def test_preselection_needs_a_clear_leader():
    def item(*scores, canonical=None):
        return SimpleNamespace(
            canonical_student_number=canonical,
            student_candidates=[
                {"number": index + 1, "name": "x", "score": score}
                for index, score in enumerate(scores)
            ],
        )

    assert preselected_number(item(0.9, 0.5)) == 1
    assert preselected_number(item(0.9, 0.8)) is None      # close second
    assert preselected_number(item(0.6)) is None            # weak leader
    assert preselected_number(item(canonical=7)) == 7
    # Weak candidates (a probably new student) are not recommended.
    assert candidate_numbers(item(0.9, 0.5, canonical=7)) == [7, 1]


@pytest.mark.parametrize("fields,expected", [
    ({"student_match_status": "unknown", "raw_name": "Neu Nina", "student_candidates": []},
     "nicht im Verzeichnis"),
    ({"student_match_status": "ambiguous", "raw_name": "Muster M"}, "mehrere passende"),
    ({"student_match_status": "ambiguous", "raw_name": "Muster Max",
      "reason": "Geburtsdatum auf dem Formular weicht ab"}, "Geburtsdatum weicht"),
    ({"student_match_status": "unknown", "raw_name": "Mustr Max",
      "student_candidates": [{"number": 2, "score": 0.8}]}, "ähnlicher Eintrag"),
])
def test_status_lines_name_the_open_decision(fields, expected):
    item = ReviewItem("a.pdf", fields.get("raw_name"), None, fields.get("reason", ""), [], "",
                      student_match_status=fields["student_match_status"],
                      student_candidates=fields.get("student_candidates", []))

    assert expected in status_lines(item)[0]


def test_status_lines_for_subject_proposal_and_conflict():
    proposal = ReviewItem("a.pdf", "X Y", "Robotik", "", [], "", subject_proposal="Robotik")
    conflict = ReviewItem(
        "b.pdf", "X Y", "Deutsch, Englisch", "Widersprüchliche Fächer ohne Mehrheit", [], ""
    )

    assert "Robotik" in status_lines(proposal)[1]
    assert "widersprüchlich" in status_lines(conflict)[1]


def test_field_boxes_and_dates():
    item = ReviewItem("a.pdf", None, None, "", [], "", field_evidence=[
        {"field": "student_name", "bbox": {"x0": 1, "top": 2, "x1": 3, "bottom": 4}},
        {"field": "subject", "bbox": None},
    ])

    assert field_boxes(item) == {"student_name": (1.0, 2.0, 3.0, 4.0)}
    assert format_iso_date("2015-09-09") == "09.09.2015"
    assert format_iso_date("kaputt") == ""
    assert parse_operator_date(" 09.09.2015 ") == date(2015, 9, 9)
    assert parse_operator_date("") is None
    with pytest.raises(ValueError):
        parse_operator_date("2015-09-09")


def test_value_lists_are_detected():
    assert is_value_list("Deutsch, Englisch") and is_value_list("Deutsch/Englisch")
    assert not is_value_list("Sachunt.")


def test_adding_a_subject_keeps_other_entries(tmp_path):
    config = tmp_path / "subjects_config.json"
    config.write_text(json.dumps({
        "subjects": ["Deutsch", "Mathematik"],
        "aliases": {"Mathe": "Mathematik"},
        "custom": 1,
    }), encoding="utf-8")

    assert add_subject_to_config(config, " Robotik ") is True
    assert add_subject_to_config(config, "robotik") is False

    data = json.loads(config.read_text(encoding="utf-8"))
    assert data["subjects"] == ["Deutsch", "Mathematik", "Robotik"]
    assert data["aliases"] == {"Mathe": "Mathematik"} and data["custom"] == 1
    assert not list(tmp_path.glob(".subjects_config.json.*"))


def _form_pdf(path, rotate=0):
    image = Image.new("RGB", (850, 1100), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 200, 400, 240), fill="black")  # stands in for the name
    if rotate:
        image = image.rotate(rotate, expand=True)
    image.save(path, "PDF", resolution=100.0)


def test_preview_crops_field_boxes_from_the_scan(tmp_path):
    source = tmp_path / "form.pdf"
    _form_pdf(source)
    # 100 dpi image -> 72 pt per inch: the dark bar is at x 72..288, y 144..173 pt.
    images = render_preview(source, {"student_name": (72, 144, 288, 173)})

    assert images.error is None and images.page is None
    label, png = images.fragments[0]
    assert label == "Name"
    crop = Image.open(__import__("io").BytesIO(png)).convert("L")
    assert crop.getextrema()[0] < 50  # the dark field is inside the fragment


def test_preview_falls_back_to_page_and_reports_errors(tmp_path):
    source = tmp_path / "form.pdf"
    _form_pdf(source)

    assert render_preview(source, {}).page is not None
    missing = render_preview(tmp_path / "missing.pdf", {"student_name": (1, 1, 2, 2)})
    assert missing.fragments == () and "Vorschau nicht verfügbar" in missing.error
