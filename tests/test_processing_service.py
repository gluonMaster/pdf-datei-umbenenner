"""Synthetic coverage for the UI-independent processing pipeline."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from file_renamer import FileRenamer
from form_parser import FormParseResult, FormType
from main import PDFRenamerApp
from processing_service import (
    DocumentProcessingService,
    PerformedAction,
    PlannedAction,
)
from review_queue import ReviewStore
from student_registry import MatchStatus, Student, StudentMatch
from validator import Validator


class StubExtractor:
    def __init__(self, failing=()):
        self.failing = set(failing)

    def extract_page_data(self, path):
        if Path(path).name in self.failing:
            raise OSError("synthetic read failure")
        return SimpleNamespace(text=Path(path).stem)


class StubParser:
    def __init__(self, results):
        self.results = results

    def parse(self, page):
        return self.results[page.text]


class StubRegistry:
    def __init__(self, matches):
        self.matches = matches

    def match(self, raw_name):
        return self.matches[raw_name]


def parsed(
    name="Noisy Name",
    subject="Mathematik",
    *,
    form_type=FormType.LEGACY,
    confidence=0.95,
    warnings=(),
):
    return FormParseResult(
        form_type=form_type,
        raw_student_name=name,
        student_name=name,
        raw_subject=subject,
        subject=subject,
        confidence=confidence,
        reasons=("synthetic parser evidence",),
        warnings=tuple(warnings),
    )


def exact(student=None):
    student = student or Student(7, "Canonical", "Student")
    return StudentMatch(MatchStatus.EXACT, student, (), "exact")


def service(tmp_path, parse_results, matches, *, failing=()):
    store = ReviewStore(tmp_path / "manual_review.json")
    pipeline = DocumentProcessingService(
        extractor=StubExtractor(failing),
        parser=StubParser(parse_results),
        student_registry=StubRegistry(matches),
        validator=Validator(),
        review_store=store,
        renamer=FileRenamer(),
        manual_review_folder=tmp_path / "manual_check",
    )
    return pipeline, store


@pytest.mark.parametrize("form_type", [FormType.LEGACY, FormType.ATTENDANCE])
def test_known_student_old_and_new_forms_use_only_canonical_data(tmp_path, form_type):
    source = tmp_path / f"{form_type.value}.pdf"
    source.write_bytes(b"pdf")
    student = Student(42, "Müller", "Anna")
    pipeline, store = service(
        tmp_path,
        {source.stem: parsed("OCR Wrong", "Mathe", form_type=form_type)},
        {"OCR Wrong": exact(student)},
    )

    result = pipeline.process_document(source, month=3, year=2028)

    assert result.error is None
    assert result.form_type == form_type.value
    assert result.canonical_subject == "Mathematik"
    assert result.planned_name == "Müller_Anna_Mathematik_März_2028.pdf"
    assert result.performed_action == PerformedAction.RENAMED
    assert result.planned_path.read_bytes() == b"pdf"
    assert not source.exists()
    assert store.count() == 0


@pytest.mark.parametrize("status", [MatchStatus.UNKNOWN, MatchStatus.AMBIGUOUS])
def test_unknown_and_ambiguous_students_always_go_to_review_with_suggestions(
    tmp_path, status
):
    source = tmp_path / f"{status.value}.pdf"
    source.write_bytes(b"pdf")
    suggestions = (Student(1, "Alpha", "Ada"), Student(2, "Alfa", "Ada"))
    match = StudentMatch(status, None, suggestions, "manual registry decision")
    pipeline, store = service(
        tmp_path,
        {source.stem: parsed("Alfa Ada")},
        {"Alfa Ada": match},
    )

    result = pipeline.process_document(source, 4, 2029)

    assert result.requires_review
    assert result.performed_action == PerformedAction.MOVED_TO_REVIEW
    assert result.review_item.student_match_status == status.value
    assert result.review_item.student_suggestions == ["Alpha Ada", "Alfa Ada"]
    assert store.count() == 1
    assert result.planned_path.exists()
    assert not source.exists()


def test_unknown_subject_goes_to_review(tmp_path):
    source = tmp_path / "unknown-subject.pdf"
    source.write_bytes(b"pdf")
    pipeline, store = service(
        tmp_path,
        {source.stem: parsed(subject="Matematik")},
        {"Noisy Name": exact()},
    )

    result = pipeline.process_document(source, 5, 2030)

    assert result.requires_review
    assert "Unbekanntes Fach" in result.review_item.reason
    assert "Mathematik" in result.review_item.suggestions
    assert store.count() == 1


def test_dry_run_returns_complete_review_plan_without_any_write(tmp_path):
    source = tmp_path / "dry.pdf"
    source.write_bytes(b"original")
    queue_path = tmp_path / "manual_review.json"
    original_queue = b'{"review_items": []}\n'
    queue_path.write_bytes(original_queue)
    match = StudentMatch(MatchStatus.UNKNOWN, None, (), "unknown student")
    pipeline, store = service(
        tmp_path,
        {source.stem: parsed("Unknown Person")},
        {"Unknown Person": match},
    )

    batch = pipeline.process_batch([source], 6, 2031, dry_run=True)
    result = batch.results[0]

    assert result.planned_action == PlannedAction.MANUAL_REVIEW
    assert result.performed_action == PerformedAction.NONE
    assert result.review_item is not None
    assert result.planned_path == tmp_path / "manual_check" / "dry.pdf"
    assert source.read_bytes() == b"original"
    assert not (tmp_path / "manual_check").exists()
    assert queue_path.read_bytes() == original_queue
    assert store.count() == 0
    assert not batch.review_queue_saved


def test_disk_and_current_batch_collisions_use_next_free_suffix(tmp_path):
    sources = [tmp_path / "scan-a.pdf", tmp_path / "scan-b.pdf"]
    for index, source in enumerate(sources):
        source.write_bytes(f"source-{index}".encode())
    base = "Canonical_Student_Mathematik_April_2032"
    (tmp_path / f"{base}.pdf").write_bytes(b"existing-base")
    (tmp_path / f"{base}_1.pdf").write_bytes(b"existing-one")
    pipeline, _store = service(
        tmp_path,
        {source.stem: parsed() for source in sources},
        {"Noisy Name": exact()},
    )

    batch = pipeline.process_batch(sources, 4, 2032)

    assert [item.planned_name for item in batch.results] == [
        f"{base}_2.pdf",
        f"{base}_3.pdf",
    ]
    assert (tmp_path / f"{base}.pdf").read_bytes() == b"existing-base"
    assert (tmp_path / f"{base}_1.pdf").read_bytes() == b"existing-one"
    assert (tmp_path / f"{base}_2.pdf").read_bytes() == b"source-0"
    assert (tmp_path / f"{base}_3.pdf").read_bytes() == b"source-1"


def test_manual_correction_and_regular_processing_share_collision_planner(tmp_path):
    renamer = FileRenamer()
    base = "Canonical_Student_Physik_September_2032"
    (tmp_path / f"{base}.pdf").write_bytes(b"existing")

    regular_target = renamer.plan_new_path(
        tmp_path, "Canonical", "Student", "Physik", 9, 2032
    )
    correction_target = renamer.plan_new_path(
        tmp_path, "Canonical", "Student", "Physik", 9, 2032
    )

    assert regular_target.name == f"{base}_1.pdf"
    assert correction_target.name == f"{base}_2.pdf"


def test_one_extraction_error_does_not_stop_batch(tmp_path):
    bad = tmp_path / "bad.pdf"
    good = tmp_path / "good.pdf"
    bad.write_bytes(b"bad")
    good.write_bytes(b"good")
    pipeline, _store = service(
        tmp_path,
        {good.stem: parsed()},
        {"Noisy Name": exact()},
        failing={bad.name},
    )

    batch = pipeline.process_batch([bad, good], 7, 2033)

    assert batch.total == 2
    assert batch.results[0].error == "synthetic read failure"
    assert batch.results[1].performed_action == PerformedAction.RENAMED
    assert bad.exists()
    assert not good.exists()


def test_cli_facade_dry_run_uses_service_without_mutating_sources(tmp_path, capsys):
    source = tmp_path / "cli.pdf"
    source.write_bytes(b"cli")
    pipeline, store = service(
        tmp_path,
        {source.stem: parsed()},
        {"Noisy Name": exact()},
    )
    app = PDFRenamerApp(
        tmp_path,
        dry_run=True,
        month=8,
        year=2034,
        service=pipeline,
    )

    batch = app.run()

    assert batch.results[0].planned_name.endswith("_August_2034.pdf")
    assert source.read_bytes() == b"cli"
    assert store.count() == 0
    assert not store.store_path.exists()
    assert "Simulation" in capsys.readouterr().out


def test_old_review_json_loads_with_pipeline_defaults(tmp_path):
    queue_path = tmp_path / "manual_review.json"
    queue_path.write_text(
        """{
  "review_items": [{
    "file_path": "manual_check/old.pdf",
    "raw_name": "Old Name",
    "raw_subject": "Math",
    "reason": "legacy",
    "suggestions": ["Mathematik"],
    "preview_text": "old preview"
  }]
}
""",
        encoding="utf-8",
    )
    store = ReviewStore(queue_path)

    store.load()

    item = store.get_all()[0]
    assert item.form_type is None
    assert item.student_suggestions == []
    assert item.selected_year is None
    assert item.planned_filename is None
