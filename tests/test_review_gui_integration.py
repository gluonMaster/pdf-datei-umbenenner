"""Headless coverage for session-4 review actions and worker events."""

import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from alias_manager import AliasManager
from file_renamer import FileRenamer
from gui_processing import BatchRunner, BatchSettings, WorkerEventKind
from review_actions import (
    ReviewActionRequest,
    ReviewActionService,
    ReviewActionStatus,
    ReviewDecision,
)
from review_queue import ReviewItem, ReviewStore
from student_registry import MatchStatus, StudentRegistry


def write_registry(path: Path, rows=()):
    lines = ["Nummer;Nachname;Vorname"]
    lines.extend(f"{number};{last};{first}" for number, last, first in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def review_item(**overrides):
    values = dict(
        file_path="manual_check/scan.pdf",
        raw_name="D0e Jane",
        raw_subject="Mathmatik",
        reason="unknown student",
        suggestions=["Mathematik"],
        preview_text="preview",
        form_type="attendance",
        student_match_status="unknown",
        student_suggestions=[],
        selected_month=2,
        selected_year=2044,
    )
    values.update(overrides)
    return ReviewItem(**values)


def action_service(tmp_path, item, rows=((1, "Other", "Person"),), renamer=None):
    csv_path = tmp_path / "Schuler.csv"
    write_registry(csv_path, rows)
    registry = StudentRegistry(csv_path, tmp_path / "student_aliases.json")
    store = ReviewStore(tmp_path / "manual_review.json")
    store.add(item)
    assert store.save()
    aliases = AliasManager(tmp_path / "subject_aliases.json", create_if_missing=False)
    service = ReviewActionService(registry, aliases, store, renamer or FileRenamer())
    source = tmp_path / item.file_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"pdf")
    return service, registry, store, source


def test_unknown_student_is_added_aliased_renamed_and_removed(tmp_path):
    item = review_item()
    service, registry, store, source = action_service(tmp_path, item)

    result = service.apply(
        ReviewActionRequest(
            item,
            tmp_path,
            "Doe",
            "Jane",
            "Mathematik",
            ReviewDecision.ADD_NEW,
        )
    )

    assert result.status == ReviewActionStatus.SUCCESS
    assert result.registry_saved
    assert result.student_alias_saved
    assert result.subject_alias_saved
    assert result.target_path.name == "Doe_Jane_Mathematik_Februar_2044.pdf"
    assert result.target_path.read_bytes() == b"pdf"
    assert not source.exists()
    assert store.count() == 0
    assert json.loads(store.store_path.read_text(encoding="utf-8")) == {
        "review_items": []
    }
    assert StudentRegistry(
        registry.csv_path, registry.aliases_path
    ).match("D0e Jane").status == MatchStatus.EXACT


def test_existing_student_selection_creates_alias_without_new_row(tmp_path):
    item = review_item(raw_subject="Physik", selected_month=11, selected_year=2047)
    service, registry, store, _source = action_service(
        tmp_path, item, rows=((7, "Doe", "Jane"),)
    )

    result = service.apply(
        ReviewActionRequest(
            item,
            tmp_path,
            "ignored",
            "ignored",
            "Physik",
            ReviewDecision.USE_EXISTING,
            student_number=7,
            save_student_alias=True,
        )
    )

    assert result.succeeded
    assert result.target_path.name == "Doe_Jane_Physik_November_2047.pdf"
    assert len(registry.students) == 1
    assert store.count() == 0
    assert StudentRegistry(
        registry.csv_path, registry.aliases_path
    ).match("D0e Jane").student.number == 7


def test_explicit_rename_only_does_not_change_student_registry(tmp_path):
    item = review_item(raw_subject="Physik")
    service, registry, store, _source = action_service(tmp_path, item)
    original_csv = registry.csv_path.read_bytes()

    result = service.apply(
        ReviewActionRequest(
            item,
            tmp_path,
            "Unlisted",
            "Student",
            "Physik",
            ReviewDecision.RENAME_ONLY,
        )
    )

    assert result.succeeded
    assert registry.csv_path.read_bytes() == original_csv
    assert registry.match("Unlisted Student").status == MatchStatus.UNKNOWN
    assert store.count() == 0


def test_move_failure_keeps_queue_item_and_reports_persisted_registry(tmp_path):
    class FailingRenamer(FileRenamer):
        @staticmethod
        def move_file(source_path, target_path, dry_run=False):
            return False

    item = review_item(raw_name="New Student", raw_subject="Physik")
    service, registry, store, source = action_service(
        tmp_path, item, renamer=FailingRenamer()
    )

    result = service.apply(
        ReviewActionRequest(
            item,
            tmp_path,
            "New",
            "Student",
            "Physik",
            ReviewDecision.ADD_NEW,
        )
    )

    assert result.status == ReviewActionStatus.PARTIAL
    assert result.registry_saved
    assert not result.file_moved
    assert source.exists()
    assert store.count() == 1
    assert registry.match("New Student").status == MatchStatus.EXACT


def test_batch_runner_uses_snapshot_and_stops_between_files(tmp_path):
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    stop = Event()

    class Store:
        store_path = tmp_path / "manual_review.json"

        @staticmethod
        def count():
            return 0

    class Renamer:
        @staticmethod
        def reset_reservations():
            pass

    class Service:
        renamer = Renamer()
        review_store = Store()
        manual_review_folder = None

        def __init__(self):
            self.calls = []

        def process_document(self, path, month, year, dry_run):
            self.calls.append((Path(path).name, month, year, dry_run))
            stop.set()
            return SimpleNamespace(name=Path(path).name)

    service = Service()
    events = []
    BatchRunner(service).run(BatchSettings(tmp_path, 8, 2099, True), stop, events.append)

    assert service.calls == [("a.pdf", 8, 2099, True)]
    assert [event.kind for event in events].count(WorkerEventKind.RESULT) == 1
    assert any(
        event.kind == WorkerEventKind.COMPLETED and event.payload is True
        for event in events
    )
    assert events[-1].kind == WorkerEventKind.FINISHED


def test_batch_runner_reports_fatal_exception_and_always_finishes(tmp_path):
    service = SimpleNamespace(
        renamer=SimpleNamespace(reset_reservations=lambda: None),
        review_store=SimpleNamespace(count=lambda: 0),
    )
    events = []

    BatchRunner(service).run(
        BatchSettings(tmp_path / "missing", 1, 2030), Event(), events.append
    )

    assert any(event.kind == WorkerEventKind.FATAL for event in events)
    assert events[-1].kind == WorkerEventKind.FINISHED
