"""Headless coverage for session-4 review actions and worker events."""

import json
from pathlib import Path
import queue
import threading
from threading import Event
from types import SimpleNamespace

import pytest

import gui_main
from alias_manager import AliasManager
from file_renamer import FileRenamer
from gui_main import PDFRenamerGUI
from gui_processing import BatchRunner, BatchSettings, WorkerEventKind
from processing_service import PlannedAction
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


def test_new_student_preserves_explicit_name_field_boundaries(tmp_path):
    item = review_item(raw_name=None, raw_subject="Physik")
    service, registry, _store, _source = action_service(
        tmp_path, item, rows=((1, "Van", "Buren Alex"),)
    )

    result = service.apply(
        ReviewActionRequest(item, tmp_path, "Van Buren", "Alex", "Physik")
    )

    assert result.succeeded
    assert result.registry_saved
    assert result.student.number == 2
    assert result.student.last_name == "Van Buren"
    assert result.student.first_name == "Alex"
    assert len(registry.students) == 2


def test_move_exception_releases_reservation_and_retry_reuses_student(tmp_path):
    class RetryRenamer(FileRenamer):
        fail = True

        def move_file(self, source, target, dry_run=False):
            if self.fail:
                raise OSError("PDF is locked")
            return super().move_file(source, target, dry_run)

    renamer = RetryRenamer()
    item = review_item()
    service, registry, store, source = action_service(tmp_path, item, renamer=renamer)
    request = ReviewActionRequest(item, tmp_path, "Doe", "Jane", "Mathematik")

    failed = service.apply(request)
    assert failed.status == ReviewActionStatus.PARTIAL
    assert failed.registry_saved and failed.student_alias_saved
    assert source.exists() and store.count() == 1

    renamer.fail = False
    retried = service.apply(request)
    assert retried.succeeded
    assert not retried.registry_saved
    assert retried.target_path.name == "Doe_Jane_Mathematik_Februar_2044.pdf"
    assert len(registry.students) == 2
    assert not source.exists() and store.count() == 0


def test_alias_write_failure_preserves_pdf_and_can_be_retried(tmp_path, monkeypatch):
    item = review_item()
    service, registry, store, source = action_service(tmp_path, item)
    write_aliases = registry._write_aliases

    def fail_write(_aliases):
        raise OSError("Alias write failed")

    monkeypatch.setattr(registry, "_write_aliases", fail_write)
    request = ReviewActionRequest(item, tmp_path, "Doe", "Jane", "Mathematik")
    failed = service.apply(request)
    assert failed.status == ReviewActionStatus.PARTIAL
    assert failed.registry_saved and not failed.student_alias_saved
    assert source.exists() and store.count() == 1
    assert StudentRegistry(registry.csv_path, registry.aliases_path).match_parts(
        "Doe", "Jane"
    ).status == MatchStatus.EXACT

    monkeypatch.setattr(registry, "_write_aliases", write_aliases)
    retried = service.apply(request)
    assert retried.succeeded and retried.student_alias_saved
    assert len(registry.students) == 2
    assert store.count() == 0


@pytest.mark.parametrize("queue_failure", ["save", "remove"])
@pytest.mark.parametrize("rollback_raises", [False, True])
def test_queue_failure_and_failed_rollback_retain_actual_pdf_path(
    tmp_path, monkeypatch, queue_failure, rollback_raises
):
    class RollbackFailRenamer(FileRenamer):
        moves = 0

        def move_file(self, source, target, dry_run=False):
            self.moves += 1
            if self.moves == 1:
                return super().move_file(source, target, dry_run)
            if rollback_raises:
                raise OSError("Rollback failed")
            return False

    item = review_item(raw_subject="Physik")
    service, _registry, store, source = action_service(
        tmp_path, item, renamer=RollbackFailRenamer()
    )
    original_operation = getattr(store, queue_failure)
    calls = []

    def fail_once(*args):
        calls.append(args)
        if len(calls) == 1:
            if queue_failure == "remove":
                original_operation(*args)  # Failure after in-memory mutation.
            raise OSError("Queue failure")
        return original_operation(*args)

    monkeypatch.setattr(store, queue_failure, fail_once)
    result = service.apply(
        ReviewActionRequest(
            item, tmp_path, "Doe", "Jane", "Physik", ReviewDecision.RENAME_ONLY
        )
    )

    assert result.status == ReviewActionStatus.PARTIAL
    assert result.file_moved and not result.queue_removed
    assert not source.exists()
    assert result.target_path.read_bytes() == b"pdf"
    assert store.get_all()[0].file_path == result.target_path.name
    reloaded = ReviewStore(store.store_path)
    assert reloaded.load()
    assert reloaded.get_all() == store.get_all()


class MainThreadValue:
    """Tk-like test double that rejects worker-thread access."""

    def __init__(self, value=None):
        self.value = value
        self.options = {}

    def get(self):
        assert threading.current_thread() is threading.main_thread()
        return self.value

    def set(self, value):
        assert threading.current_thread() is threading.main_thread()
        self.value = value

    def config(self, **kwargs):
        assert threading.current_thread() is threading.main_thread()
        self.options.update(kwargs)


def test_review_choice_populates_canonical_compound_surname(tmp_path):
    item = review_item(canonical_student_number=7)
    _service, registry, _store, _source = action_service(
        tmp_path, item, rows=((7, "Van Buren", "Alex"),)
    )
    gui = PDFRenamerGUI.__new__(PDFRenamerGUI)
    gui.student_registry = registry
    gui.student_combo = MainThreadValue()
    gui.selected_student = MainThreadValue()
    gui.edit_surname = MainThreadValue()
    gui.edit_name = MainThreadValue()

    gui._update_student_choices(item)

    assert gui.edit_surname.get() == "Van Buren"
    assert gui.edit_name.get() == "Alex"


def test_review_uses_loaded_queue_folder_and_saved_period(tmp_path, monkeypatch):
    item = review_item(raw_subject="Physik")
    service, _registry, store, source = action_service(tmp_path, item)
    other_folder = tmp_path / "next_batch"
    decoy = other_folder / item.file_path
    decoy.parent.mkdir(parents=True)
    decoy.write_bytes(b"other PDF")
    gui = PDFRenamerGUI.__new__(PDFRenamerGUI)
    gui.processing = False
    gui.current_review_item = item
    gui.review_store = store
    gui.review_actions = service
    gui.selected_folder = MainThreadValue(str(other_folder))
    # Invalid settings for a future batch must not affect an existing item.
    gui.selected_month = MainThreadValue("")
    gui.selected_year = MainThreadValue("invalid")
    gui.edit_surname = MainThreadValue("Doe")
    gui.edit_name = MainThreadValue("Jane")
    gui.edit_subject = MainThreadValue("Physik")
    gui.save_student_alias = MainThreadValue(False)
    gui.subjects = ["Physik"]
    gui.stats = gui._empty_stats()
    gui.log_message = lambda _message: None
    gui.update_statistics = lambda: None
    reloads = []
    gui.load_review_queue = lambda **kwargs: reloads.append(kwargs)
    gui.clear_edit_fields = lambda: None
    gui.review_tree = SimpleNamespace(
        selection=lambda: ("row",), item=lambda *_args: (item.file_path,)
    )
    opened = []
    monkeypatch.setattr(gui_main.FileOpener, "open_pdf", opened.append)

    def unexpected_dialog(*args, **_kwargs):
        pytest.fail(f"Unexpected dialog: {args}")

    monkeypatch.setattr(gui_main.messagebox, "showerror", unexpected_dialog)
    gui.on_review_item_double_click()
    gui._apply_review_action(ReviewDecision.RENAME_ONLY)

    assert opened == [source]
    assert (tmp_path / "Doe_Jane_Physik_Februar_2044.pdf").read_bytes() == b"pdf"
    assert decoy.read_bytes() == b"other PDF"
    assert not source.exists() and store.count() == 0
    assert reloads == [{"reload_store": False}]


@pytest.mark.parametrize("dry_run", [False, True])
def test_fatal_worker_preserves_pending_queue_and_dry_run_is_read_only(tmp_path, dry_run):
    store = ReviewStore(tmp_path / "manual_review.json")
    store.add(review_item())
    service = SimpleNamespace(renamer=FileRenamer(), review_store=store)
    events = []

    BatchRunner(service).run(
        BatchSettings(tmp_path / "missing", 3, 2044, dry_run), Event(), events.append
    )

    kinds = [event.kind for event in events]
    assert kinds[-2:] == [WorkerEventKind.FATAL, WorkerEventKind.FINISHED]
    assert (WorkerEventKind.QUEUE_SAVED in kinds) is (not dry_run)
    assert store.store_path.exists() is (not dry_run)
    assert store.count() == 1


@pytest.mark.parametrize("outcome", ["complete", "stop", "fatal"])
def test_gui_worker_events_are_handled_only_on_main_thread(tmp_path, monkeypatch, outcome):
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (tmp_path / name).write_bytes(b"synthetic")
    gui = PDFRenamerGUI.__new__(PDFRenamerGUI)
    gui.processing = False
    gui._close_requested = False
    gui.selected_folder = MainThreadValue(str(tmp_path))
    gui.selected_month = MainThreadValue(4)
    gui.selected_year = MainThreadValue(2099)
    gui.dry_run_mode = MainThreadValue(True)
    gui.progress_var = MainThreadValue(0)
    gui.progress_label = MainThreadValue()
    gui.stop_button = MainThreadValue()
    gui.stop_event = Event()
    gui.worker_events = queue.Queue()
    gui.review_store = ReviewStore(tmp_path / "manual_review.json")
    calls = []

    def process_document(path, month, year, dry_run):
        assert threading.current_thread() is not threading.main_thread()
        calls.append((path.name, month, year, dry_run))
        if path.name == "a.pdf":
            raise RuntimeError("One bad document")
        if outcome == "stop":
            gui.stop_event.set()
        return SimpleNamespace(
            error=None, planned_action=PlannedAction.RENAME, warnings=(),
            dry_run=dry_run, pdf_path=path, planned_name="planned.pdf",
        )

    def reset_reservations():
        if outcome == "fatal":
            raise RuntimeError("Batch initialization failed")

    service = SimpleNamespace(
        renamer=SimpleNamespace(reset_reservations=reset_reservations),
        review_store=gui.review_store, process_document=process_document,
    )
    gui.batch_runner = BatchRunner(service)
    control_states = []

    def check_main(*_args, **_kwargs):
        assert threading.current_thread() is threading.main_thread()

    def controls(running):
        check_main()
        control_states.append(running)

    gui._set_batch_controls = controls
    gui.log_message = check_main
    gui.update_statistics = check_main
    gui.show_final_report = check_main
    gui.load_review_queue = check_main
    gui.root = SimpleNamespace(after=check_main)
    monkeypatch.setattr(gui_main.messagebox, "showerror", check_main)

    gui.start_processing()
    gui.selected_year.set(2100)
    gui.processing_thread.join(timeout=5)
    assert not gui.processing_thread.is_alive()
    assert gui.processing  # The worker cannot change UI state itself.
    gui._drain_worker_events()

    assert not gui.processing
    assert control_states == [True, False]
    assert all(call[1:] == (4, 2099, True) for call in calls)
    assert len(calls) == {"complete": 3, "stop": 2, "fatal": 0}[outcome]
    assert gui.stats["error"] == 1
    assert gui.progress_label.options["text"] == {
        "complete": "Verarbeitung abgeschlossen",
        "stop": "Verarbeitung gestoppt",
        "fatal": "Verarbeitung fehlgeschlagen",
    }[outcome]
