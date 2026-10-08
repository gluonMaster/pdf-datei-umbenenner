"""Controlled end-to-end regressions for the final QA scenarios."""

from pathlib import Path

import pytest

from alias_manager import AliasManager
from file_renamer import FileRenamer
from form_parser import FormParser
from page_data import ExtractionSource, PageData, PageWord
from processing_service import (
    DocumentProcessingService,
    PerformedAction,
    PlannedAction,
)
from review_actions import (
    ReviewActionRequest,
    ReviewActionService,
    ReviewDecision,
)
from review_queue import ReviewItem, ReviewStore
from student_registry import MatchStatus, StudentRegistry
from validator import Validator


def _word(text: str, x0: float, top: float) -> PageWord:
    width = max(8.0, len(text) * 5.0)
    return PageWord(text, x0, top, x0 + width, top + 10.0)


def _line(text: str, x0: float, top: float, gap: float = 4.0) -> list[PageWord]:
    words = []
    cursor = x0
    for token in text.split():
        word = _word(token, cursor, top)
        words.append(word)
        cursor = word.x1 + gap
    return words


def _attendance_page(student_name: str, subject="Mathematik") -> PageData:
    subjects = (subject,) * 3 if isinstance(subject, str) else tuple(subject)
    words = []
    words += _line("Formular Anwesenheitsnachweis", 40, 70)
    words += _line(student_name, 40, 140)
    words += _line("Name des Schülers", 40, 155)
    words += _line("Geburtsdatum", 360, 155)
    words += _line("Dauer", 180, 335)
    words += _line("Fach", 242, 340)
    words += _line("Ort", 300, 340)
    for index, value in enumerate(subjects):
        words += _line(value, 238, 370 + index * 24)
    words += _line("Name der Lehrkraft", 40, 710)
    words += _line("Unrelated Adult", 180, 710)
    text = "\n".join(
        (
            "Formular Anwesenheitsnachweis",
            student_name,
            "Name des Schülers Geburtsdatum",
            "Dauer Fach Ort",
            *subjects,
            "Name der Lehrkraft Unrelated Adult",
        )
    )
    return PageData(
        width=595,
        height=842,
        text=text,
        words=tuple(words),
        source=ExtractionSource.PDF_TEXT,
    )


class StaticPageExtractor:
    def __init__(self, page: PageData):
        self.page = page

    def extract_page_data(self, _path: Path) -> PageData:
        return self.page


def _write_registry(path: Path, rows) -> None:
    lines = ["Nummer;Nachname;Vorname"]
    lines.extend(f"{number};{last};{first}" for number, last, first in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def _stack(tmp_path: Path, raw_name: str, rows):
    registry_path = tmp_path / "Schuler.csv"
    aliases_path = tmp_path / "student_aliases.json"
    _write_registry(registry_path, rows)
    registry = StudentRegistry(registry_path, aliases_path)
    review_store = ReviewStore(tmp_path / "manual_review.json")
    service = DocumentProcessingService(
        extractor=StaticPageExtractor(_attendance_page(raw_name)),
        parser=FormParser(),
        student_registry=registry,
        validator=Validator(),
        review_store=review_store,
        renamer=FileRenamer(),
        manual_review_folder=tmp_path / "manual_check",
    )
    review_actions = ReviewActionService(
        registry,
        AliasManager(tmp_path / "subject_aliases.json", create_if_missing=False),
        review_store,
        FileRenamer(),
    )
    return service, registry, review_store, review_actions


def _first_document_to_review(
    tmp_path: Path,
    service: DocumentProcessingService,
    review_store: ReviewStore,
    *,
    month: int,
    year: int,
) -> ReviewItem:
    source = tmp_path / "first.pdf"
    source.write_bytes(b"first PDF")

    batch = service.process_batch([source], month, year)
    result = batch.results[0]

    assert result.planned_action == PlannedAction.MANUAL_REVIEW
    assert result.performed_action == PerformedAction.MOVED_TO_REVIEW
    assert result.planned_path.read_bytes() == b"first PDF"
    assert not source.exists()
    assert batch.review_queue_saved
    assert review_store.count() == 1
    return review_store.get_all()[0]


def test_unknown_student_is_confirmed_once_then_next_document_is_automatic(tmp_path):
    service, registry, review_store, actions = _stack(
        tmp_path,
        "Nuw Student",
        ((1, "Other", "Person"),),
    )
    item = _first_document_to_review(
        tmp_path,
        service,
        review_store,
        month=3,
        year=2049,
    )

    resolved = actions.apply(
        ReviewActionRequest(
            item=item,
            base_folder=tmp_path,
            last_name="New",
            first_name="Student",
            subject="Mathematik",
            decision=ReviewDecision.ADD_NEW,
        )
    )

    assert resolved.succeeded
    assert resolved.registry_saved
    assert resolved.student_alias_saved
    assert review_store.count() == 0
    assert registry.match("Nuw Student").status == MatchStatus.EXACT

    repeated_source = tmp_path / "second.pdf"
    repeated_source.write_bytes(b"second PDF")
    repeated = service.process_batch([repeated_source], 3, 2049).results[0]

    assert repeated.planned_action == PlannedAction.RENAME
    assert repeated.performed_action == PerformedAction.RENAMED
    assert repeated.student_match.status == MatchStatus.EXACT
    assert repeated.planned_name == "New_Student_Mathematik_März_2049_1.pdf"
    assert repeated.planned_path.read_bytes() == b"second PDF"
    assert not repeated_source.exists()
    reloaded = StudentRegistry(registry.csv_path, registry.aliases_path)
    assert reloaded.match("Nuw Student").status == MatchStatus.EXACT


def test_ocr_name_correction_alias_makes_next_document_automatic(tmp_path):
    service, registry, review_store, actions = _stack(
        tmp_path,
        "Dae Jane",
        ((7, "Doe", "Jane"),),
    )
    item = _first_document_to_review(
        tmp_path,
        service,
        review_store,
        month=11,
        year=2050,
    )

    resolved = actions.apply(
        ReviewActionRequest(
            item=item,
            base_folder=tmp_path,
            last_name="ignored",
            first_name="ignored",
            subject="Mathematik",
            decision=ReviewDecision.USE_EXISTING,
            student_number=7,
            save_student_alias=True,
        )
    )

    assert resolved.succeeded
    assert resolved.student_alias_saved
    assert not resolved.registry_saved
    assert review_store.count() == 0
    assert registry.match("Dae Jane").status == MatchStatus.EXACT

    repeated_source = tmp_path / "second.pdf"
    repeated_source.write_bytes(b"second PDF")
    repeated = service.process_batch([repeated_source], 11, 2050).results[0]

    assert repeated.planned_action == PlannedAction.RENAME
    assert repeated.performed_action == PerformedAction.RENAMED
    assert repeated.student_match.status == MatchStatus.EXACT
    assert repeated.planned_name == "Doe_Jane_Mathematik_November_2050_1.pdf"
    assert repeated.planned_path.read_bytes() == b"second PDF"
    reloaded = StudentRegistry(registry.csv_path, registry.aliases_path)
    assert reloaded.match("Dae Jane").status == MatchStatus.EXACT


def test_dry_run_preserves_real_registry_alias_queue_and_pdf_files(tmp_path):
    service, registry, review_store, _actions = _stack(
        tmp_path,
        "Known Learner",
        ((5, "Known", "Learner"),),
    )
    registry.confirm_existing("Known OCR", 5)
    review_store.add(
        ReviewItem(
            file_path="manual_check/pending.pdf",
            raw_name="Pending Student",
            raw_subject="Matematik",
            reason="Unbekanntes Fach",
            suggestions=["Mathematik"],
            preview_text="Anonymous preview",
        )
    )
    assert review_store.save()
    source = tmp_path / "dry-run.pdf"
    source.write_bytes(b"unchanged PDF")
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    batch = service.process_batch([source], 8, 2051, dry_run=True)
    result = batch.results[0]
    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    assert result.planned_action == PlannedAction.RENAME
    assert result.performed_action == PerformedAction.NONE
    assert result.planned_name == "Known_Learner_Mathematik_August_2051.pdf"
    assert before == after
    assert source.read_bytes() == b"unchanged PDF"
    assert review_store.count() == 1
    assert not batch.review_queue_saved
    assert not (tmp_path / "manual_check").exists()


@pytest.mark.parametrize("form_type", ["legacy", "attendance"])
def test_both_parsers_feed_the_real_registry_and_selected_period(tmp_path, form_type):
    service, _registry, _queue, _actions = _stack(
        tmp_path, "Known Learner", ((5, "Known", "Learner"),)
    )
    if form_type == "legacy":
        service.extractor.page = PageData(
            595, 842,
            "Known Learner Mathematik\n"
            "Bewilligungszeitraum von 01.01.2026 bis 31.01.2026\n"
            "Monat Januar\nUnterrichtsart\nLehrer(in): Unrelated Adult Englisch",
            source=ExtractionSource.PDF_TEXT,
        )
    source = tmp_path / "unrelated-original-name.pdf"
    source.write_bytes(b"synthetic PDF")

    result = service.process_batch([source], 11, 2050).results[0]

    assert result.succeeded and result.form_type == form_type
    assert result.planned_name == "Known_Learner_Mathematik_November_2050.pdf"
    assert result.planned_path.read_bytes() == b"synthetic PDF"
    assert not source.exists()


@pytest.mark.parametrize("subjects", [
    ("Testfach",),
    ("Mathematik", "Deutsch"),
    ("Deutsch", "Deutsch", "Englisch"),
    ("Deutsch/Englisch",),
])
def test_uncertain_fach_values_require_review_without_mutating_dry_run(tmp_path, subjects):
    service, _registry, queue, _actions = _stack(
        tmp_path, "Known Learner", ((5, "Known", "Learner"),)
    )
    service.extractor.page = _attendance_page("Known Learner", subjects)
    source = tmp_path / "uncertain.pdf"
    source.write_bytes(b"synthetic PDF")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    result = service.process_batch([source], 9, 2047, dry_run=True).results[0]

    assert result.error is None and result.requires_review
    assert result.student_match.status == MatchStatus.EXACT
    assert result.review_item.reason and result.found_data.raw_subject
    assert result.performed_action == PerformedAction.NONE
    assert queue.count() == 0
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("known", [True, False])
def test_native_move_diagnostic_reaches_processing_result(tmp_path, monkeypatch, known):
    service, _registry, _queue, _actions = _stack(
        tmp_path, "Known Learner" if known else "Unknown Learner",
        ((5, "Known", "Learner"),),
    )
    source = tmp_path / "failed.pdf"
    source.write_bytes(b"unchanged PDF")

    def fail_move(*_args):
        raise OSError("Datenträger ist schreibgeschützt; Zielkopie konnte nicht entfernt werden")

    monkeypatch.setattr(service.renamer, "_move_no_clobber", fail_move)
    result = service.process_batch([source], 9, 2047).results[0]

    assert "Datenträger ist schreibgeschützt" in result.error
    assert "Zielkopie konnte nicht entfernt werden" in result.error
    assert result.actual_path == source
    assert source.read_bytes() == b"unchanged PDF"


def test_native_move_diagnostic_reaches_manual_review_result(tmp_path, monkeypatch):
    service, _registry, queue, actions = _stack(
        tmp_path, "Unknown Learner", ((5, "Known", "Learner"),)
    )
    item = _first_document_to_review(tmp_path, service, queue, month=9, year=2047)

    def fail_move(*_args):
        raise OSError("Zugriff auf Ziel verweigert")

    monkeypatch.setattr(actions.renamer, "_move_no_clobber", fail_move)
    result = actions.apply(ReviewActionRequest(
        item=item, base_folder=tmp_path, last_name="Known", first_name="Learner",
        subject="Mathematik", decision=ReviewDecision.RENAME_ONLY,
    ))

    assert not result.succeeded
    assert "Zugriff auf Ziel verweigert" in result.message
    assert queue.count() == 1
    assert (tmp_path / item.file_path).read_bytes() == b"first PDF"
