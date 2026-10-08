"""Registry identification from OCR readings and birth dates (synthetic data)."""

from datetime import date
from pathlib import Path

import pytest

from file_renamer import FileRenamer
from form_parser import FieldEvidence, FieldReading, FormParseResult, FormType
from name_matching import compare_birth_dates, compare_name
from processing_service import DocumentProcessingService, PerformedAction, PlannedAction
from review_actions import ReviewActionRequest, ReviewActionService, ReviewDecision
from review_queue import ReviewItem, ReviewStore
from student_registry import MatchStatus, NameEvidence, StudentRegistry
from student_registry_service import StudentRegistryService
from validator import Validator


def _registry(tmp_path, rows, header="Nummer;Nachname;Vorname"):
    path = tmp_path / "Schuler.csv"
    lines = [header] + [";".join(str(value) for value in row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return StudentRegistry(path, tmp_path / "student_aliases.json")


ROWS = (
    (1, "Musterfeld", "Erika"),
    (2, "Beispielmann", "Viktor"),
    (3, "Probe", "Anne"),
    (4, "Muster", "Maximilian"),
)


def _evidence(*texts, localized=True, birth_date=None, sources=None):
    sources = sources or ["ocr_page", "ocr_field", "text_layer"]
    return NameEvidence(tuple(zip(sources, texts)), localized, birth_date)


# --- comparison ------------------------------------------------------------

def test_typical_confusions_are_cheap_and_real_edits_are_counted():
    confusion = compare_name("Musterfeid Erlka", "Musterfeld", "Erika")
    sibling = compare_name("Muster Maximiliane", "Muster", "Maximilian")

    assert confusion.real_edits == 0 and confusion.score > 0.9
    assert sibling.real_edits == 1


@pytest.mark.parametrize("read,stored,expected", [
    (date(2011, 6, 25), date(2011, 6, 25), "match"),
    (date(2011, 6, 26), date(2011, 6, 25), "near"),
    (date(2017, 6, 25), date(2011, 6, 25), "near"),
    (date(2012, 6, 25), date(2011, 6, 25), "contradiction"),
    (date(2011, 7, 25), date(2011, 6, 25), "contradiction"),
    (date(2013, 3, 4), date(2011, 6, 25), "contradiction"),
])
def test_birth_dates_tolerate_one_confusable_digit(read, stored, expected):
    assert compare_birth_dates(read, stored) == expected


# --- identification ----------------------------------------------------------

def test_typical_ocr_errors_are_corrected_automatically(tmp_path):
    registry = _registry(tmp_path, ROWS)

    match = registry.identify(_evidence("Musterfeid Erika", "Musterfeid Erika"))

    assert match.status == MatchStatus.AUTO_CORRECTED
    assert match.student.number == 1
    assert "automatisch korrigiert" in match.reason
    assert match.decided_by.startswith("ocr_page")
    assert not match.requires_manual_review


def test_exact_text_layer_reading_wins_over_misread_ocr(tmp_path):
    registry = _registry(tmp_path, ROWS)

    match = registry.identify(
        _evidence("Musterfeid Erika", "Musterfeid Erika", "Musterfeld Erika")
    )

    assert match.status == MatchStatus.EXACT
    assert match.student.number == 1
    assert "text_layer" in match.reason


def test_swapped_name_order_is_recognized(tmp_path):
    registry = _registry(tmp_path, ROWS)

    assert registry.identify(_evidence("Erika Musterfeld")).status == MatchStatus.EXACT
    assert registry.identify(_evidence("Erika Musterfeid")).status == MatchStatus.AUTO_CORRECTED


@pytest.mark.parametrize("reading", [
    "Muster Maximiliane",  # sibling: an inserted letter is a real difference
    "Probe Anna",        # a/e is no typical confusion: possibly another person
    "Neumann Lena",      # a new student
])
def test_real_differences_are_never_corrected(tmp_path, reading):
    registry = _registry(tmp_path, ROWS)

    match = registry.identify(_evidence(reading))

    assert match.status in (MatchStatus.UNKNOWN, MatchStatus.AMBIGUOUS)
    assert match.student is None
    assert match.new_student_proposal is not None
    assert match.new_student_proposal.last_name == reading.split()[0]


def test_new_student_proposal_carries_recognized_values(tmp_path):
    registry = _registry(tmp_path, ROWS)

    match = registry.identify(_evidence("Neumann Lena Marie", birth_date=date(2014, 2, 3)))

    proposal = match.new_student_proposal
    assert (proposal.last_name, proposal.first_name, proposal.birth_date) == (
        "Neumann", "Lena Marie", date(2014, 2, 3),
    )
    assert "neuer Schüler" in match.reason


def test_close_candidates_need_manual_choice(tmp_path):
    registry = _registry(tmp_path, ((1, "Muster", "Lina"), (2, "Muster", "Lima")))

    match = registry.identify(_evidence("Muster Lirna"))

    assert match.status == MatchStatus.AMBIGUOUS
    assert [item.student.number for item in match.ranked_suggestions][:2] == [2, 1]
    assert match.ranked_suggestions[0].score >= match.ranked_suggestions[1].score


def test_unlocalized_name_field_is_not_corrected(tmp_path):
    registry = _registry(tmp_path, ROWS)

    match = registry.identify(_evidence("Musterfeid Erika", localized=False))

    assert match.status == MatchStatus.UNKNOWN
    assert match.suggestions[0].number == 1


def test_readings_pointing_to_different_students_need_review(tmp_path):
    registry = _registry(tmp_path, ROWS)

    match = registry.identify(_evidence("Musterfeld Erika", "Beispielmann Viktor"))

    assert match.status == MatchStatus.AMBIGUOUS


def test_independent_readings_agreeing_on_another_spelling_block_correction(tmp_path):
    registry = _registry(tmp_path, ((4, "Muster", "Maximilian", "15.03.2013"),),
                         header="Nummer;Nachname;Vorname;Geburtsdatum")
    evidence = NameEvidence(
        (("ocr_page", "Muster Maximiliane"), ("text_layer", "Muster Maximiliane")),
        True,
        date(2013, 3, 15),
    )

    match = registry.identify(evidence)

    assert match.status != MatchStatus.AUTO_CORRECTED
    assert "übereinstimmend" in match.reason


def test_matching_birth_date_allows_stronger_name_errors(tmp_path):
    registry = _registry(tmp_path, ((1, "Musterfeld", "Erika", "15.03.2013"),
                                    (2, "Beispielmann", "Viktor", "")),
                         header="Nummer;Nachname;Vorname;Geburtsdatum")
    reading = "Musterfexd Erxka"  # two real edits

    without = registry.identify(_evidence(reading))
    with_date = registry.identify(_evidence(reading, birth_date=date(2013, 3, 15)))

    assert without.status != MatchStatus.AUTO_CORRECTED
    assert with_date.status == MatchStatus.AUTO_CORRECTED
    assert with_date.birth_date_check == "match"
    assert "Geburtsdatum stimmt" in with_date.reason


def test_contradicting_birth_date_blocks_even_an_exact_name(tmp_path):
    registry = _registry(tmp_path, ((1, "Musterfeld", "Erika", "15.03.2013"),),
                         header="Nummer;Nachname;Vorname;Geburtsdatum")

    contradiction = registry.identify(_evidence("Musterfeld Erika", birth_date=date(2013, 4, 2)))
    near = registry.identify(_evidence("Musterfeld Erika", birth_date=date(2013, 3, 16)))

    assert contradiction.status == MatchStatus.AMBIGUOUS
    assert "Geburtsdatum" in contradiction.reason
    assert near.status == MatchStatus.EXACT and near.birth_date_check == "near"


def test_strict_match_keeps_its_semantics(tmp_path):
    registry = _registry(tmp_path, ROWS)

    assert registry.match("Musterfeid Erika").status == MatchStatus.UNKNOWN
    assert registry.match("Musterfeld Erika").status == MatchStatus.EXACT


def test_unavailable_registry_never_identifies(tmp_path):
    registry = StudentRegistry(tmp_path / "missing.csv")

    match = registry.identify(_evidence("Musterfeld Erika"))

    assert match.status == MatchStatus.ERROR and match.student is None


# --- registry birth dates ----------------------------------------------------

def test_birth_date_column_is_optional_and_preserved(tmp_path):
    registry = _registry(tmp_path, ((1, "Muster", "Max", "01.02.2014", "x"),
                                    (2, "Probe", "Paula", "", "y")),
                         header="Nummer;Nachname;Vorname;Geburtsdatum;Notiz")

    assert registry.students[0].birth_date == date(2014, 2, 1)
    assert registry.students[1].birth_date is None
    registry.update_student(2, first_name="Paulina")
    text = registry.csv_path.read_text(encoding="utf-8-sig")
    assert "1;Muster;Max;01.02.2014;x" in text
    assert "2;Probe;Paulina;;y" in text


def test_invalid_birth_date_makes_registry_unavailable(tmp_path):
    registry = _registry(tmp_path, ((1, "Muster", "Max", "2014-02-01"),),
                         header="Nummer;Nachname;Vorname;Geburtsdatum")

    assert not registry.is_available
    assert "TT.MM.JJJJ" in registry.load_error


def test_learning_adds_the_column_once_and_never_overwrites(tmp_path):
    registry = _registry(tmp_path, ((1, "Muster", "Max"), (2, "Probe", "Paula")))

    assert registry.learn_birth_date(1, date(2014, 2, 1)) == "stored"
    assert registry.learn_birth_date(1, date(2014, 2, 1)) == "known"
    assert registry.learn_birth_date(1, date(2015, 3, 3)) == "conflict"

    reloaded = StudentRegistry(registry.csv_path, registry.aliases_path)
    assert reloaded.students[0].birth_date == date(2014, 2, 1)
    assert reloaded.students[1].birth_date is None
    assert (tmp_path / "Schuler.csv.bak").exists()  # schema migration backup


def test_import_reads_birth_dates_and_ignores_one_sided_dates(tmp_path):
    registry = _registry(tmp_path, ((1, "Muster", "Max", "01.02.2014"),),
                         header="Nummer;Nachname;Vorname;Geburtsdatum")
    source = tmp_path / "import.csv"
    source.write_text(
        "Nummer;Nachname;Vorname\n1;Muster;Max\n2;Probe;Paula\n", encoding="utf-8"
    )
    dated = tmp_path / "dated.csv"
    dated.write_text(
        "Nummer;Nachname;Vorname;Geburtsdatum\n3;Neu;Nina;09.09.2015\n", encoding="utf-8"
    )
    service = StudentRegistryService(registry)

    preview = service.preview_import(source)
    assert [item.number for item in preview.unchanged] == [1]
    assert [item.number for item in preview.new] == [2]
    service.apply_import(preview, confirmed=True)
    service.apply_import(service.preview_import(dated), confirmed=True)

    added = registry.select_existing(3)
    assert added.birth_date == date(2015, 9, 9)
    assert registry.select_existing(1).birth_date == date(2014, 2, 1)


# --- processing service ------------------------------------------------------

class _Extractor:
    def extract_page_data(self, _path):
        return object()


class _Parser:
    """Returns a fixed parse result with name and birth-date evidence."""

    def __init__(self, readings, birth_date=None, birth_uncertain=False):
        self.readings = readings
        self.birth_date = birth_date
        self.birth_uncertain = birth_uncertain

    def parse(self, _page):
        name = self.readings[0][1]
        evidence = [
            FieldEvidence(
                "student_name", name, localization="anchor",
                readings=tuple(FieldReading(source, text) for source, text in self.readings),
            ),
        ]
        if self.birth_date:
            evidence.append(FieldEvidence(
                "birth_date", self.birth_date.strftime("%d.%m.%Y"), self.birth_date.isoformat(),
                uncertainties=("widersprüchlich",) if self.birth_uncertain else (),
            ))
        return FormParseResult(
            FormType.ATTENDANCE, name, name, "Deutsch", "Deutsch", 0.98, ("synthetisch",),
            birth_date=self.birth_date, evidence=tuple(evidence),
        )


def _service(tmp_path, registry, parser):
    return DocumentProcessingService(
        extractor=_Extractor(),
        parser=parser,
        student_registry=registry,
        validator=Validator(),
        review_store=ReviewStore(tmp_path / "manual_review.json"),
        renamer=FileRenamer(),
        manual_review_folder=tmp_path / "manual_check",
    )


def _scan(tmp_path, name="scan.pdf"):
    path = tmp_path / name
    path.write_bytes(b"%PDF synthetic")
    return path


def test_exact_match_teaches_a_clean_birth_date_only_in_a_real_run(tmp_path):
    registry = _registry(tmp_path, ROWS)
    parser = _Parser((("ocr_page", "Musterfeld Erika"),), date(2013, 3, 15))
    service = _service(tmp_path, registry, parser)
    csv_before = registry.csv_path.read_bytes()

    preview = service.process_document(_scan(tmp_path), 9, 2026, dry_run=True)
    assert registry.csv_path.read_bytes() == csv_before
    assert any("würde" in warning for warning in preview.warnings)

    result = service.process_document(_scan(tmp_path, "real.pdf"), 9, 2026)
    assert result.performed_action == PerformedAction.RENAMED
    assert any("übernommen" in warning for warning in result.warnings)
    assert registry.select_existing(1).birth_date == date(2013, 3, 15)


@pytest.mark.parametrize("readings,uncertain", [
    ((("ocr_page", "Musterfeid Erika"),), False),   # automatic correction
    ((("ocr_page", "Musterfeld Erika"),), True),    # readings disagree
])
def test_no_learning_from_corrections_or_uncertain_dates(tmp_path, readings, uncertain):
    registry = _registry(tmp_path, ROWS)
    service = _service(tmp_path, registry, _Parser(readings, date(2013, 3, 15), uncertain))
    csv_before = registry.csv_path.read_bytes()

    result = service.process_document(_scan(tmp_path), 9, 2026)

    assert result.planned_action == PlannedAction.RENAME
    assert registry.csv_path.read_bytes() == csv_before
    assert not (tmp_path / "student_aliases.json").exists()


def test_review_item_keeps_ranked_candidates_and_proposal(tmp_path):
    registry = _registry(tmp_path, ROWS)
    parser = _Parser((("ocr_page", "Musterfeid Erika Sofie"),), date(2013, 3, 15))
    result = _service(tmp_path, registry, parser).process_document(
        _scan(tmp_path), 9, 2026, dry_run=True
    )

    item = ReviewItem.from_dict(result.review_item.to_dict())
    assert item.student_candidates[0]["number"] == 1
    assert item.new_student_proposal == {
        "last_name": "Musterfeid", "first_name": "Erika Sofie", "birth_date": "2013-03-15",
    }


@pytest.mark.parametrize("decision", [ReviewDecision.USE_EXISTING, ReviewDecision.ADD_NEW])
def test_confirmed_review_teaches_the_birth_date(tmp_path, decision):
    registry = _registry(tmp_path, ROWS)
    source = _scan(tmp_path)
    item = ReviewItem(
        "scan.pdf", "Neu Nina", "Deutsch", "Prüfung", [], "",
        selected_month=9, selected_year=2026, birth_date="2015-09-09",
        field_evidence=[{"field": "birth_date", "uncertainties": []}],
    )
    store = ReviewStore(tmp_path / "manual_review.json")
    store.add(item)
    actions = ReviewActionService(registry, _NoAliases(), store, FileRenamer())

    result = actions.apply(ReviewActionRequest(
        item=item, base_folder=tmp_path, last_name="Neu", first_name="Nina",
        subject="Deutsch", decision=decision, student_number=2, save_student_alias=False,
    ))

    assert result.succeeded and result.birth_date_saved
    number = 2 if decision == ReviewDecision.USE_EXISTING else 5
    assert registry.select_existing(number).birth_date == date(2015, 9, 9)
    assert not source.exists()


class _NoAliases:
    def add_and_save(self, *_args):
        return True
