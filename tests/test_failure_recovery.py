"""Failure-injection coverage for durable review and alias state."""

from pathlib import Path
import queue
import threading

import pytest

import alias_manager as alias_manager_module
from alias_manager import AliasManager
from file_renamer import FileRenamer
from form_parser import FormParseResult, FormType
import gui_main
from gui_main import PDFRenamerGUI
from gui_processing import WorkerEvent, WorkerEventKind
from main import PDFRenamerApp, main as cli_main
from processing_service import DocumentProcessingService
from review_actions import (
    ReviewActionRequest,
    ReviewActionService,
    ReviewActionStatus,
    ReviewDecision,
)
from review_queue import ReviewItem, ReviewStore
import review_queue as review_queue_module
from student_registry import MatchStatus, Student, StudentMatch, StudentRegistry
from validator import Validator


def _review_item() -> ReviewItem:
    return ReviewItem(
        file_path="manual_check/scan.pdf",
        raw_name="Sample Student",
        raw_subject="Physik",
        reason="Manuelle Prüfung",
        suggestions=[],
        preview_text="synthetic",
        selected_month=4,
        selected_year=2042,
    )


def _write_registry(path: Path) -> None:
    path.write_text(
        "Nummer;Nachname;Vorname\n1;Sample;Student\n",
        encoding="utf-8-sig",
    )


def test_corrupt_review_queue_is_fail_closed_and_never_overwritten(tmp_path):
    queue_path = tmp_path / "manual_review.json"
    original = b"{broken queue"
    queue_path.write_bytes(original)
    store = ReviewStore(queue_path)

    assert store.load() is False
    assert store.load_error
    store.add(_review_item())
    assert store.save() is False
    assert queue_path.read_bytes() == original


def test_review_queue_rejects_valid_json_with_invalid_schema(tmp_path):
    queue_path = tmp_path / "manual_review.json"
    original = (
        b'{"review_items":[{"file_path":null,"raw_name":3,'
        b'"raw_subject":null,"reason":"test","suggestions":"bad",'
        b'"preview_text":""}]}'
    )
    queue_path.write_bytes(original)
    store = ReviewStore(queue_path)

    assert store.load() is False
    assert store.load_error
    assert store.count() == 0
    assert store.save() is False
    assert queue_path.read_bytes() == original


def test_review_queue_rejects_missing_root_field(tmp_path):
    queue_path = tmp_path / "manual_review.json"
    original = b"{}"
    queue_path.write_bytes(original)
    store = ReviewStore(queue_path)

    assert store.load() is False
    assert store.save() is False
    assert queue_path.read_bytes() == original


def test_review_queue_reload_clears_stale_gui_selection(tmp_path):
    class TreeStub:
        def __init__(self):
            self.rows = []

        @staticmethod
        def get_children():
            return ()

        @staticmethod
        def delete(_item):
            return None

        def insert(self, *_args, **kwargs):
            self.rows.append(kwargs)

    old_item = _review_item()
    gui = PDFRenamerGUI.__new__(PDFRenamerGUI)
    gui.processing = False
    gui.review_store = ReviewStore(tmp_path / "manual_review.json")
    gui.review_store.add(old_item)
    gui.review_tree = TreeStub()
    gui.current_review_item = old_item
    gui.clear_edit_fields = lambda: setattr(gui, "current_review_item", None)

    assert PDFRenamerGUI.load_review_queue(gui, reload_store=False) is True
    assert gui.current_review_item is None
    assert len(gui.review_tree.rows) == 1


def test_corrupt_subject_aliases_are_fail_closed_and_rollback_memory(tmp_path):
    alias_path = tmp_path / "subjects_aliases.json"
    original = b"[not an alias object]"
    alias_path.write_bytes(original)
    manager = AliasManager(alias_path, create_if_missing=False)

    assert manager.load_error
    assert manager.add_and_save("Mathmatik", "Mathematik") is False
    assert manager.apply_alias("Mathmatik") == "Mathmatik"
    assert alias_path.read_bytes() == original


def test_subject_aliases_reject_missing_root_field(tmp_path):
    alias_path = tmp_path / "subjects_aliases.json"
    original = b"{}"
    alias_path.write_bytes(original)
    manager = AliasManager(alias_path, create_if_missing=False)

    assert manager.load_error
    assert manager.add_and_save("Mathmatik", "Mathematik") is False
    assert alias_path.read_bytes() == original


def test_subject_alias_memory_is_rolled_back_when_save_raises(tmp_path):
    manager = AliasManager(
        tmp_path / "subjects_aliases.json",
        create_if_missing=False,
    )

    def fail_save():
        raise RuntimeError("synthetic save failure")

    manager.save = fail_save
    with pytest.raises(RuntimeError, match="synthetic save failure"):
        manager.add_and_save("Mathmatik", "Mathematik")

    assert manager.apply_alias("Mathmatik") == "Mathmatik"


def test_review_store_replace_failure_preserves_original_bytes(tmp_path, monkeypatch):
    queue_path = tmp_path / "manual_review.json"
    store = ReviewStore(queue_path)
    store.add(_review_item())
    assert store.save()
    original = queue_path.read_bytes()
    store.add(
        ReviewItem(
            file_path="manual_check/second.pdf",
            raw_name=None,
            raw_subject=None,
            reason="synthetic",
            suggestions=[],
            preview_text="",
        )
    )
    monkeypatch.setattr(
        review_queue_module.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("synthetic replace failure")),
    )

    assert store.save() is False
    assert queue_path.read_bytes() == original
    assert not list(tmp_path.glob(".manual_review.json.*.tmp"))


def test_alias_replace_failure_preserves_disk_and_memory(tmp_path, monkeypatch):
    alias_path = tmp_path / "subjects_aliases.json"
    manager = AliasManager(alias_path, create_if_missing=False)
    assert manager.add_and_save("Mathe", "Mathematik")
    original = alias_path.read_bytes()
    monkeypatch.setattr(
        alias_manager_module.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("synthetic replace failure")),
    )

    assert manager.add_and_save("Physicks", "Physik") is False
    assert manager.apply_alias("Physicks") == "Physicks"
    assert alias_path.read_bytes() == original
    assert not list(tmp_path.glob(".subjects_aliases.json.*.tmp"))


def test_processing_rolls_pdf_back_when_review_queue_cannot_be_saved(tmp_path):
    class Extractor:
        @staticmethod
        def extract_page_data(_path):
            return type("Page", (), {"text": "synthetic"})()

    class Parser:
        @staticmethod
        def parse(_page):
            return FormParseResult(
                FormType.LEGACY,
                "Unknown Student",
                "Unknown Student",
                "Physik",
                "Physik",
                0.95,
                ("synthetic",),
            )

    class Registry:
        @staticmethod
        def match(_name):
            return StudentMatch(
                MatchStatus.UNKNOWN,
                None,
                (),
                "Schüler muss manuell bestätigt werden.",
            )

    class FailingStore(ReviewStore):
        def save(self):
            return False

    source = tmp_path / "scan.pdf"
    source.write_bytes(b"original")
    store = FailingStore(tmp_path / "manual_review.json")
    service = DocumentProcessingService(
        Extractor(),
        Parser(),
        Registry(),
        Validator(),
        store,
        FileRenamer(),
        tmp_path / "manual_check",
    )

    result = service.process_document(source, 4, 2042)

    assert result.error
    assert "zurückverschoben" in result.error
    assert source.read_bytes() == b"original"
    assert not (tmp_path / "manual_check" / "scan.pdf").exists()
    assert store.count() == 0


def test_processing_reports_recovery_path_when_queue_and_rollback_both_fail(tmp_path):
    class Extractor:
        @staticmethod
        def extract_page_data(_path):
            return type("Page", (), {"text": "synthetic"})()

    class Parser:
        @staticmethod
        def parse(_page):
            return FormParseResult(
                FormType.LEGACY,
                "Unknown Student",
                "Unknown Student",
                "Physik",
                "Physik",
                0.95,
                ("synthetic",),
            )

    class Registry:
        @staticmethod
        def match(_name):
            return StudentMatch(
                MatchStatus.UNKNOWN,
                None,
                (),
                "Schüler muss manuell bestätigt werden.",
            )

    class FailingStore(ReviewStore):
        def save(self):
            return False

    class RollbackFailRenamer(FileRenamer):
        def __init__(self):
            super().__init__()
            self.move_count = 0

        def move_file(self, source_path, target_path, dry_run=False):
            self.move_count += 1
            if self.move_count == 1:
                return super().move_file(source_path, target_path, dry_run)
            return False

    source = tmp_path / "scan.pdf"
    source.write_bytes(b"original")
    store = FailingStore(tmp_path / "manual_review.json")
    target = tmp_path / "manual_check" / source.name
    service = DocumentProcessingService(
        Extractor(),
        Parser(),
        Registry(),
        Validator(),
        store,
        RollbackFailRenamer(),
        target.parent,
    )

    batch = service.process_batch([source], 4, 2042)

    assert batch.errors == 1
    assert batch.review_queue_error
    assert str(target) in batch.results[0].error
    assert not source.exists()
    assert target.read_bytes() == b"original"
    assert store.count() == 1
    assert Path(store.get_all()[0].file_path) == Path("manual_check") / "scan.pdf"


def test_review_action_rolls_pdf_back_when_queue_removal_cannot_be_saved(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    _write_registry(csv_path)
    registry = StudentRegistry(csv_path, tmp_path / "student_aliases.json")
    item = _review_item()
    store = ReviewStore(tmp_path / "manual_review.json")
    store.add(item)
    assert store.save()
    original_queue = store.store_path.read_bytes()
    store.save = lambda: False
    source = tmp_path / item.file_path
    source.parent.mkdir()
    source.write_bytes(b"pdf")
    aliases = AliasManager(tmp_path / "subject_aliases.json", create_if_missing=False)
    service = ReviewActionService(registry, aliases, store, FileRenamer())

    result = service.apply(
        ReviewActionRequest(
            item,
            tmp_path,
            "Sample",
            "Student",
            "Physik",
            ReviewDecision.RENAME_ONLY,
        )
    )

    assert result.status == ReviewActionStatus.ERROR
    assert result.file_moved is False
    assert source.read_bytes() == b"pdf"
    assert store.count() == 1
    assert store.store_path.read_bytes() == original_queue
    assert not (tmp_path / "Sample_Student_Physik_April_2042.pdf").exists()


def test_review_action_creates_recovery_entry_if_item_is_stale_and_rollback_fails(
    tmp_path,
):
    class RollbackFailRenamer(FileRenamer):
        def __init__(self):
            super().__init__()
            self.move_count = 0

        def move_file(self, source_path, target_path, dry_run=False):
            self.move_count += 1
            if self.move_count == 1:
                return super().move_file(source_path, target_path, dry_run)
            return False

    csv_path = tmp_path / "Schuler.csv"
    _write_registry(csv_path)
    registry = StudentRegistry(csv_path, tmp_path / "student_aliases.json")
    item = _review_item()
    store = ReviewStore(tmp_path / "manual_review.json")
    assert store.save()
    source = tmp_path / item.file_path
    source.parent.mkdir()
    source.write_bytes(b"pdf")
    aliases = AliasManager(tmp_path / "subject_aliases.json", create_if_missing=False)
    service = ReviewActionService(registry, aliases, store, RollbackFailRenamer())

    result = service.apply(
        ReviewActionRequest(
            item,
            tmp_path,
            "Sample",
            "Student",
            "Physik",
            ReviewDecision.RENAME_ONLY,
        )
    )

    target = tmp_path / "Sample_Student_Physik_April_2042.pdf"
    assert result.status == ReviewActionStatus.PARTIAL
    assert result.file_moved is True
    assert "aktualisiert" in result.message
    assert not source.exists()
    assert target.read_bytes() == b"pdf"
    assert store.count() == 1
    assert store.get_all()[0].file_path == target.name
    reloaded = ReviewStore(store.store_path)
    assert reloaded.load() is True
    assert reloaded.get_all()[0].file_path == target.name


def test_review_action_does_not_keep_unsaved_subject_alias_in_memory(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    _write_registry(csv_path)
    registry = StudentRegistry(csv_path, tmp_path / "student_aliases.json")
    item = _review_item()
    item.raw_subject = "Physicks"
    store = ReviewStore(tmp_path / "manual_review.json")
    store.add(item)
    assert store.save()
    source = tmp_path / item.file_path
    source.parent.mkdir()
    source.write_bytes(b"pdf")
    aliases = AliasManager(tmp_path / "subject_aliases.json", create_if_missing=False)
    aliases.save = lambda: False
    service = ReviewActionService(registry, aliases, store, FileRenamer())

    result = service.apply(
        ReviewActionRequest(
            item,
            tmp_path,
            "Sample",
            "Student",
            "Physik",
            ReviewDecision.RENAME_ONLY,
        )
    )

    assert result.status == ReviewActionStatus.ERROR
    assert aliases.apply_alias("Physicks") == "Physicks"
    assert source.exists()


def test_review_action_rolls_back_legacy_alias_manager_when_save_raises(tmp_path):
    class LegacyAliases:
        def __init__(self):
            self.aliases = {}

        def get_all_aliases(self):
            return self.aliases.copy()

        def add_alias(self, incorrect, correct):
            self.aliases[incorrect] = correct
            return True

        @staticmethod
        def save():
            raise RuntimeError("synthetic alias failure")

    csv_path = tmp_path / "Schuler.csv"
    _write_registry(csv_path)
    registry = StudentRegistry(csv_path, tmp_path / "student_aliases.json")
    item = _review_item()
    item.raw_subject = "Physicks"
    store = ReviewStore(tmp_path / "manual_review.json")
    store.add(item)
    source = tmp_path / item.file_path
    source.parent.mkdir()
    source.write_bytes(b"pdf")
    aliases = LegacyAliases()

    result = ReviewActionService(registry, aliases, store, FileRenamer()).apply(
        ReviewActionRequest(
            item,
            tmp_path,
            "Sample",
            "Student",
            "Physik",
            ReviewDecision.RENAME_ONLY,
        )
    )

    assert result.status == ReviewActionStatus.ERROR
    assert aliases.aliases == {}
    assert source.exists()


def test_review_action_releases_injected_reservation_when_move_fails(tmp_path):
    class FailingRenamer:
        def __init__(self):
            self.target = None
            self.released = []

        def plan_new_path(self, folder, *_args, **_kwargs):
            self.target = Path(folder) / "planned.pdf"
            return self.target

        @staticmethod
        def move_file(_source, _target):
            return False

        def release_target_path(self, path):
            self.released.append(Path(path))

    csv_path = tmp_path / "Schuler.csv"
    _write_registry(csv_path)
    registry = StudentRegistry(csv_path, tmp_path / "student_aliases.json")
    item = _review_item()
    store = ReviewStore(tmp_path / "manual_review.json")
    store.add(item)
    source = tmp_path / item.file_path
    source.parent.mkdir()
    source.write_bytes(b"pdf")
    renamer = FailingRenamer()

    result = ReviewActionService(
        registry,
        AliasManager(tmp_path / "subjects_aliases.json", create_if_missing=False),
        store,
        renamer,
    ).apply(
        ReviewActionRequest(
            item,
            tmp_path,
            "Sample",
            "Student",
            "Physik",
            ReviewDecision.RENAME_ONLY,
        )
    )

    assert result.status == ReviewActionStatus.ERROR
    assert renamer.released == [renamer.target]
    assert source.exists()


def test_gui_queue_removal_restores_item_when_save_raises(tmp_path, monkeypatch):
    item = _review_item()
    store = ReviewStore(tmp_path / "manual_review.json")
    store.add(item)

    def fail_save():
        raise RuntimeError("synthetic queue failure")

    store.save = fail_save
    gui = PDFRenamerGUI.__new__(PDFRenamerGUI)
    gui.processing = False
    gui.review_store = store
    gui.current_review_item = item
    errors = []
    monkeypatch.setattr(gui_main.messagebox, "askyesno", lambda *_a, **_k: True)
    monkeypatch.setattr(
        gui_main.messagebox,
        "showerror",
        lambda _title, message, **_kwargs: errors.append(message),
    )

    PDFRenamerGUI.remove_from_queue(gui)

    assert store.get_all() == [item]
    assert errors and "synthetic queue failure" in errors[0]


def test_gui_worker_start_failure_restores_idle_state(tmp_path, monkeypatch):
    class Variable:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class Label:
        def __init__(self):
            self.values = []

        def config(self, **kwargs):
            self.values.append(kwargs)

    class BrokenThread:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def start():
            raise RuntimeError("synthetic thread failure")

    gui = PDFRenamerGUI.__new__(PDFRenamerGUI)
    gui.validate_input = lambda: True
    gui.selected_folder = Variable(str(tmp_path))
    gui.selected_month = Variable(4)
    gui.selected_year = Variable(2042)
    gui.dry_run_mode = Variable(True)
    gui.review_store = ReviewStore(tmp_path / "manual_review.json")
    gui.stats = {}
    gui.update_statistics = lambda: None
    gui.progress_var = Variable(0)
    gui.processing = False
    gui._active_batch_folder = None
    gui._batch_queue_failed = False
    gui.stop_event = threading.Event()
    gui.worker_events = queue.Queue()
    gui.batch_runner = type("Runner", (), {"run": lambda *_args: None})()
    reloads = []
    gui.load_review_queue = lambda **kwargs: reloads.append(kwargs)
    gui.progress_label = Label()
    control_states = []
    gui._set_batch_controls = lambda running: control_states.append(running)
    logs = []
    gui.log_message = logs.append
    errors = []
    monkeypatch.setattr(gui_main.threading, "Thread", BrokenThread)
    monkeypatch.setattr(
        gui_main.messagebox,
        "showerror",
        lambda title, message: errors.append((title, message)),
    )

    PDFRenamerGUI.start_processing(gui)

    assert gui.processing is False
    assert gui._active_batch_folder is None
    assert control_states == [True, False]
    assert reloads == [{"reload_store": False}]
    assert "nicht gestartet" in gui.progress_label.values[-1]["text"]
    assert errors and "synthetic thread failure" in errors[0][1]


def test_gui_close_request_suppresses_completion_dialog():
    class Value:
        def set(self, _value):
            return None

    class Label:
        def config(self, **_kwargs):
            return None

    gui = PDFRenamerGUI.__new__(PDFRenamerGUI)
    gui._close_requested = True
    gui._batch_queue_failed = True
    gui.progress_var = Value()
    gui.progress_label = Label()
    reports = []
    gui.show_final_report = lambda: reports.append(True)
    logs = []
    gui.log_message = logs.append
    reloads = []
    gui.load_review_queue = lambda **kwargs: reloads.append(kwargs)

    PDFRenamerGUI._handle_worker_event(
        gui,
        WorkerEvent(WorkerEventKind.COMPLETED, False),
    )

    assert reports == []
    assert reloads == [{"reload_store": False}]
    assert any("unterdrückt" in line for line in logs)


def test_cli_returns_failure_for_a_corrupt_queue_without_touching_it(tmp_path):
    queue_path = tmp_path / "manual_review.json"
    original = b"{broken queue"
    queue_path.write_bytes(original)
    app = PDFRenamerApp(tmp_path, month=1, year=2042)

    batch = app.run()

    assert batch.review_queue_error
    assert queue_path.read_bytes() == original
    assert cli_main([str(tmp_path), "--month", "1", "--year", "2042"]) == 2
    assert queue_path.read_bytes() == original
