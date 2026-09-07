"""Synthetic coverage for the UI-independent processing pipeline."""

from pathlib import Path
from types import SimpleNamespace

import pytest

import main as main_module
from file_renamer import FileRenamer
from form_parser import FormParseResult, FormType
from main import PDFRenamerApp
from processing_service import (
    DocumentProcessingService,
    PerformedAction,
    PlannedAction,
    ProcessingStage,
)
from review_actions import ReviewActionRequest, ReviewActionService, ReviewDecision
from review_queue import ReviewStore
from student_registry import MatchStatus, Student, StudentMatch
from text_parser import TextParser
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


@pytest.mark.parametrize("raises", [False, True])
def test_failed_rename_preserves_plan_and_releases_target(tmp_path, monkeypatch, raises):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"original")
    pipeline, _store = service(tmp_path, {source.stem: parsed()}, {"Noisy Name": exact()})

    def fail_rename(*_args):
        if raises:
            raise OSError("synthetic rename failure")
        return False

    monkeypatch.setattr(pipeline.renamer, "rename_file", fail_rename)
    batch = pipeline.process_batch([source], 3, 2040)
    result = batch.results[0]

    assert result.error
    assert result.failed_stage == ProcessingStage.ACTION
    assert result.planned_action == PlannedAction.RENAME
    assert result.planned_name == "Canonical_Student_Mathematik_März_2040.pdf"
    assert result.actual_path == source
    assert result.performed_action == PerformedAction.ERROR
    assert result.stages[-1] == ProcessingStage.PLANNING
    assert batch.renamed == 0
    assert batch.errors == 1
    assert pipeline.renamer.reserve_target_path(result.planned_path) == result.planned_path
    assert source.read_bytes() == b"original"


@pytest.mark.parametrize("failure", ["add", "save"])
@pytest.mark.parametrize("rollback_fails", [False, True])
def test_review_failure_keeps_plan_and_reports_actual_pdf_location(
    tmp_path, monkeypatch, failure, rollback_fails
):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"original")
    pipeline, store = service(
        tmp_path,
        {source.stem: parsed()},
        {"Noisy Name": StudentMatch(MatchStatus.UNKNOWN, None, (), "unknown")},
    )
    previous_item = pipeline.process_document(source, 4, 2041, dry_run=True).review_item
    previous_item.file_path = "manual_check/pending.pdf"
    store.add(previous_item)
    assert store.save()
    original_queue = store.store_path.read_bytes()
    pipeline.renamer.reset_reservations()

    def fail_store(*args):
        if failure == "add":
            store.items.append(args[0])
        raise OSError(f"synthetic {failure} failure")

    monkeypatch.setattr(store, failure, fail_store)
    if rollback_fails:
        real_move = pipeline.renamer.move_file

        def move_without_rollback(source_path, target_path):
            if target_path == source:
                raise OSError("synthetic rollback failure")
            return real_move(source_path, target_path)

        monkeypatch.setattr(pipeline.renamer, "move_file", move_without_rollback)

    result = pipeline.process_document(source, 4, 2041)

    assert result.error
    assert f"synthetic {failure} failure" in result.error
    assert result.failed_stage == ProcessingStage.ACTION
    assert result.planned_action == PlannedAction.MANUAL_REVIEW
    assert result.review_item.selected_year == 2041
    assert result.planned_path == tmp_path / "manual_check" / "scan.pdf"
    assert store.store_path.read_bytes() == original_queue
    if rollback_fails:
        assert result.actual_path == result.planned_path
        assert result.performed_action == PerformedAction.MOVED_TO_REVIEW
        assert store.get_all() == [previous_item, result.review_item]
        assert not source.exists()
    else:
        assert result.actual_path == source
        assert result.performed_action == PerformedAction.ERROR
        assert store.get_all() == [previous_item]
        assert not result.planned_path.exists()
    assert result.actual_path.read_bytes() == b"original"


def test_queue_paths_are_relative_to_store_for_multiple_source_folders(tmp_path):
    folders = [tmp_path / "a", tmp_path / "b"]
    for folder in folders:
        folder.mkdir()
        (folder / "scan.pdf").write_bytes(folder.name.encode())
    pipeline, store = service(
        tmp_path,
        {"scan": parsed()},
        {"Noisy Name": StudentMatch(MatchStatus.UNKNOWN, None, (), "unknown")},
    )
    pipeline.manual_review_folder = None

    batch = pipeline.process_batch([folder / "scan.pdf" for folder in folders], 1, 2042)

    assert batch.errors == 0
    assert len({item.file_path for item in store.get_all()}) == 2
    assert store.load()
    for item, result in zip(store.get_all(), batch.results):
        assert store.store_path.parent / item.file_path == result.actual_path
        assert result.actual_path.is_file()


def test_unrecognized_subject_validation_never_authorizes_rename(tmp_path):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"original")
    pipeline, _store = service(tmp_path, {source.stem: parsed()}, {"Noisy Name": exact()})
    pipeline.validator = lambda _subject: SimpleNamespace(level="error", message="Prüfung fehlgeschlagen")

    result = pipeline.process_document(source, 6, 2043, dry_run=True)

    assert result.requires_review
    assert "Prüfung fehlgeschlagen" in result.review_item.reason
    assert source.exists()


@pytest.mark.parametrize("first_requires_review", [False, True])
def test_repeated_dry_run_and_real_batch_choose_identical_targets(
    tmp_path, first_requires_review
):
    # The first PDF vacates a filename needed by the second PDF. Keep initial
    # occupants in the plan so the preview does not change when it is applied.
    original_name = "Canonical_Student_Mathematik_April_2044.pdf"
    first, second = tmp_path / original_name, tmp_path / "scan.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    first_match = (
        StudentMatch(MatchStatus.UNKNOWN, None, (), "unknown")
        if first_requires_review
        else exact(Student(2, "Other", "Person"))
    )
    pipeline, _store = service(
        tmp_path,
        {first.stem: parsed("Other Person"), second.stem: parsed()},
        {"Other Person": first_match, "Noisy Name": exact()},
    )

    preview = pipeline.process_batch([first, second], 4, 2044, dry_run=True)
    repeated = pipeline.process_batch([first, second], 4, 2044, dry_run=True)
    actual = pipeline.process_batch([first, second], 4, 2044)

    assert [item.planned_path for item in preview.results] == [
        item.planned_path for item in repeated.results
    ] == [item.planned_path for item in actual.results]
    assert actual.results[1].planned_name.endswith("_1.pdf")
    assert [item.planned_path.read_bytes() for item in actual.results] == [b"first", b"second"]


def test_manual_correction_and_processing_fill_suffix_gaps_without_overwriting(tmp_path):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"automatic")
    base = "Canonical_Student_Mathematik_April_2045"
    for suffix in ("", "_1", "_3"):
        (tmp_path / f"{base}{suffix}.pdf").write_bytes(b"existing")
    pipeline, store = service(tmp_path, {source.stem: parsed()}, {"Noisy Name": exact()})
    regular = pipeline.process_document(source, 4, 2045)
    # The parser/registry decision is still made by the processing service.
    pending = tmp_path / "pending.pdf"
    pending.write_bytes(b"review")
    pipeline.parser.results[pending.stem] = parsed("Unknown Student")
    pipeline.student_registry.matches["Unknown Student"] = StudentMatch(
        MatchStatus.UNKNOWN, None, (), "unknown"
    )
    reviewed = pipeline.process_document(pending, 4, 2045)
    actions = ReviewActionService(pipeline.student_registry, None, store, pipeline.renamer)

    corrected = actions.apply(ReviewActionRequest(
        reviewed.review_item, tmp_path, "Canonical", "Student", "Mathematik",
        ReviewDecision.RENAME_ONLY,
    ))

    assert regular.planned_name == f"{base}_2.pdf"
    assert corrected.succeeded
    assert corrected.target_path.name == f"{base}_4.pdf"
    assert regular.planned_path.read_bytes() == b"automatic"
    assert corrected.target_path.read_bytes() == b"review"
    assert store.count() == 0
    for suffix in ("", "_1", "_3"):
        assert (tmp_path / f"{base}{suffix}.pdf").read_bytes() == b"existing"


@pytest.mark.parametrize("with_read_error", [False, True])
def test_cli_entrypoint_dry_run_preserves_registry_aliases_queue_and_sources(
    tmp_path, monkeypatch, capsys, with_read_error
):
    folder = tmp_path / "input"
    folder.mkdir()
    (tmp_path / "Schuler.csv").write_text(
        "Nummer;Nachname;Vorname\n1;Canonical;Student\n2;Same;Name\n3;Same;Name\n",
        encoding="utf-8-sig",
    )
    (tmp_path / "subjects_aliases.json").write_text('{"aliases": {}}', encoding="utf-8")
    (folder / "manual_review.json").write_text('{"review_items": []}', encoding="utf-8")
    results = {
        "known": parsed("Canonical Student"),
        "unknown": parsed("Unlisted Student"),
        "ambiguous": parsed("Same Name"),
    }
    for stem in results:
        (folder / f"{stem}.pdf").write_bytes(stem.encode())
    if with_read_error:
        (folder / "bad.pdf").write_bytes(b"bad")
    monkeypatch.setattr(main_module, "__file__", str(tmp_path / "main.py"))
    monkeypatch.setattr(main_module, "OCRFallbackExtractor", lambda *_a: StubExtractor({"bad.pdf"}))
    monkeypatch.setattr(main_module, "FormParser", lambda: StubParser(results))
    monkeypatch.setattr(TextParser, "_alias_manager", None)
    monkeypatch.setattr(Validator, "_subjects_cache", None)
    monkeypatch.setattr(Validator, "_config_path", tmp_path / "subjects_config.json")

    def snapshot():
        return {
            path.relative_to(tmp_path): (path.read_bytes(), path.stat().st_mtime_ns)
            if path.is_file() else None
            for path in tmp_path.rglob("*")
        }

    before = snapshot()
    exit_code = main_module.main([
        str(folder), "--month", "8", "--year", "2056", "--dry-run",
    ])

    assert exit_code == (2 if with_read_error else 0)
    assert snapshot() == before
    output = capsys.readouterr().out
    assert "Canonical_Student_Mathematik_August_2056.pdf" in output
    assert "Manuelle Prüfung erforderlich: 2" in output
    if with_read_error:
        assert "synthetic read failure" in output


@pytest.mark.parametrize("dependency,method,failed_stage", [
    ("extractor", "extract_page_data", ProcessingStage.EXTRACTION),
    ("parser", "parse", ProcessingStage.PARSING),
    ("student_registry", "match", ProcessingStage.STUDENT_MATCHING),
    ("validator", "validate_subject", ProcessingStage.SUBJECT_VALIDATION),
])
def test_dependency_error_preserves_completed_stages_and_continues_batch(
    tmp_path, monkeypatch, dependency, method, failed_stage
):
    sources = [tmp_path / "bad.pdf", tmp_path / "good.pdf"]
    for source in sources:
        source.write_bytes(b"pdf")
    pipeline, _store = service(
        tmp_path, {source.stem: parsed() for source in sources}, {"Noisy Name": exact()}
    )
    target = getattr(pipeline, dependency)
    original = getattr(target, method)
    calls = []

    def fail_once(*args):
        calls.append(args)
        if len(calls) == 1:
            raise OSError("synthetic dependency failure")
        return original(*args)

    monkeypatch.setattr(target, method, fail_once)
    batch = pipeline.process_batch(sources, 7, 2057)
    failed, succeeded = batch.results

    assert failed.failed_stage == failed_stage
    assert failed.stages == tuple(ProcessingStage)[:tuple(ProcessingStage).index(failed_stage)]
    assert failed.planned_action == PlannedAction.NONE
    assert failed.actual_path == sources[0]
    assert sources[0].read_bytes() == b"pdf"
    assert succeeded.performed_action == PerformedAction.RENAMED
    assert succeeded.actual_path.read_bytes() == b"pdf"
    assert batch.errors == batch.renamed == 1
