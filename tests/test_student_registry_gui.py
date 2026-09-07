"""Registry GUI workflows use synthetic files and enforce Tk thread ownership."""

from collections import deque
import threading
import tkinter as tk
from tkinter import ttk

import pytest

import gui_main
from gui_main import PDFRenamerGUI
from student_registry import MatchStatus, StudentRegistry
from student_registry_service import StudentRegistryService


class MainThreadWidget:
    def __init__(self, *args, **kwargs):
        self.callbacks = deque()
        self.check()

    def check(self, *args, **kwargs):
        assert threading.current_thread() is threading.main_thread()

    title = transient = resizable = protocol = pack = grab_set = start = stop = destroy = check

    def after(self, delay, callback):
        self.check()
        self.callbacks.append(callback)


@pytest.mark.parametrize("fails", [False, True])
def test_registry_worker_keeps_tk_responsive_and_delivers_success_or_error(monkeypatch, fails):
    for owner, name in ((gui_main.tk, "Toplevel"), (gui_main.ttk, "Label"), (gui_main.ttk, "Progressbar")):
        monkeypatch.setattr(owner, name, MainThreadWidget)
    errors = []
    warnings = []
    monkeypatch.setattr(gui_main.messagebox, "showerror", lambda *a, **kw: errors.append(a))
    monkeypatch.setattr(gui_main.messagebox, "showwarning", lambda *a, **kw: warnings.append(a))
    gui = PDFRenamerGUI.__new__(PDFRenamerGUI)
    gui.root = MainThreadWidget()
    gui.processing = False
    gui._student_task_running = False
    controls = []
    gui._set_batch_controls = lambda running: controls.append((running, gui._student_task_running))
    release = threading.Event()
    entered = threading.Event()
    delivered = []

    def work():
        assert threading.current_thread() is not threading.main_thread()
        entered.set()
        assert release.wait(5)
        if fails:
            raise OSError("synthetic failure")
        return 42

    def receive(value):
        gui.root.check()
        assert not gui._student_task_running
        delivered.append(value)

    try:
        gui._run_student_task("Test", work, receive)
        assert entered.wait(2)
        assert gui._student_task_running
        # A Tk callback can execute while registry work is still blocked.
        heartbeat = []
        gui.root.after(0, lambda: heartbeat.append(True))
        gui.root.callbacks.popleft()()  # poll: no result yet, reschedule
        gui.root.callbacks.popleft()()  # heartbeat
        assert heartbeat == [True] and not delivered
        gui._run_student_task("Second", lambda: pytest.fail("Concurrent registry work"), receive)
        gui.start_processing()  # must return before reading batch settings
        gui._apply_review_action(None)  # must return before reading review fields
        gui._on_close()
        assert len(warnings) == 4
    finally:
        release.set()
        gui.student_task_thread.join(5)
    assert not gui.student_task_thread.is_alive()
    gui.root.callbacks.popleft()()
    assert not gui._student_task_running
    assert controls == [(False, True), (False, False)]
    assert delivered == ([] if fails else [42])
    assert bool(errors) is fails


def test_worker_start_failure_restores_controls(monkeypatch):
    for owner, name in ((gui_main.tk, "Toplevel"), (gui_main.ttk, "Label"), (gui_main.ttk, "Progressbar")):
        monkeypatch.setattr(owner, name, MainThreadWidget)
    gui = PDFRenamerGUI.__new__(PDFRenamerGUI)
    gui.root = MainThreadWidget()
    gui.processing = False
    gui._student_task_running = False
    controls, errors = [], []
    gui._set_batch_controls = lambda running: controls.append(gui._student_task_running)
    monkeypatch.setattr(gui_main.messagebox, "showerror", lambda *a, **kw: errors.append(a))

    def fail_start(thread):
        raise RuntimeError("thread unavailable")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    gui._run_student_task("Test", lambda: None, lambda value: pytest.fail("Unexpected success"))
    assert controls == [True, False]
    assert not gui._student_task_running and not gui.root.callbacks
    assert errors == [("Test", "thread unavailable")]


@pytest.fixture
def registry_gui(tmp_path, monkeypatch):
    """Real Tk widget smoke coverage; service tests also run without a display."""
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"Tk display unavailable: {error}")
    root.withdraw()
    csv_path = tmp_path / "Schuler.csv"
    csv_path.write_text("Nummer;Nachname;Forname\n1;Doe;Jane\n", encoding="utf-8-sig")
    gui = PDFRenamerGUI.__new__(PDFRenamerGUI)
    gui.root = root
    gui.processing = False
    gui._student_task_running = False
    gui.notebook = ttk.Notebook(root)
    gui.student_registry = StudentRegistry(csv_path)
    gui.student_admin = StudentRegistryService(gui.student_registry)
    gui._set_batch_controls = lambda _running: None
    gui.setup_students_tab()
    errors = []
    monkeypatch.setattr(gui_main.messagebox, "showerror", lambda *a, **kw: errors.append(a))
    monkeypatch.setattr(gui_main.messagebox, "showinfo", lambda *a, **kw: None)
    monkeypatch.setattr(gui_main.messagebox, "askyesno", lambda *a, **kw: True)
    try:
        yield gui, errors
    finally:
        root.destroy()


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def drain_registry_task(gui):
    """Let real Tk timers deliver a completed worker without an unbounded wait."""
    gui.student_task_thread.join(5)
    assert not gui.student_task_thread.is_alive()
    done = tk.BooleanVar(master=gui.root, value=False)
    gui.root.after(100, lambda: done.set(True))
    gui.root.wait_variable(done)
    assert not gui._student_task_running


def test_student_tab_crud_filters_alias_removal_and_external_reload(registry_gui, monkeypatch):
    gui, errors = registry_gui
    assert gui.notebook.tab(0, "text") == "Schüler"
    monkeypatch.setattr(gui, "_show_student_editor", lambda *a: ("  New  ", " Student ", True))
    gui.add_student()
    assert gui.student_tree.selection() == ("2",)
    assert gui.student_registry.match("New Student").status == MatchStatus.EXACT
    monkeypatch.setattr(gui, "_show_student_editor", lambda *a: ("Changed", "Student", True))
    gui.edit_student()
    assert gui.student_registry.match("Changed Student").student.number == 2
    gui.toggle_student_active()
    assert gui.student_tree.item("2", "values")[-1] == "Inaktiv"
    gui.student_search.set("  STUDENT    changed ")
    assert gui.student_tree.get_children() == ("2",)
    gui.student_active_filter.set("Aktiv")
    gui.load_students_table()
    assert not gui.student_tree.get_children()
    gui.student_active_filter.set("Alle")
    gui.student_search.set("")
    gui.student_tree.selection_set("2")
    gui.student_registry.confirm_existing("Changed Studnt", 2)
    gui.show_student_aliases()
    alias_dialog = next(w for w in gui.root.winfo_children() if isinstance(w, tk.Toplevel))
    alias_tree = next(w for w in descendants(alias_dialog) if isinstance(w, ttk.Treeview))
    alias_tree.selection_set(alias_tree.get_children()[0])
    next(w for w in descendants(alias_dialog) if isinstance(w, ttk.Button) and w.cget("text") == "Alias löschen").invoke()
    assert not gui.student_admin.aliases_for(2)
    assert gui.student_registry.match("Changed Studnt").status != MatchStatus.EXACT
    assert gui.student_registry.select_existing(2).active is False
    alias_dialog.destroy()
    path = gui.student_admin.paths.csv_path
    path.write_text("Nummer;Nachname;Vorname\n1;Reloaded;Student\n", encoding="utf-8")
    gui.reload_students()
    drain_registry_task(gui)
    assert gui.student_tree.get_children() == ("1",)
    assert gui.student_registry.match("Reloaded Student").status == MatchStatus.EXACT
    assert not errors


def test_import_preview_is_read_only_until_confirmed_and_displays_status(registry_gui, monkeypatch, tmp_path):
    gui, errors = registry_gui
    source = tmp_path / "monthly.csv"
    source.write_text("Nummer;Nachname;Vorname;Aktiv\n3;New;Student;0\n", encoding="utf-8")
    preview = gui.student_admin.preview_import(source)
    original = gui.student_registry.csv_path.read_bytes()
    gui._show_import_preview(preview)
    dialog = next(w for w in gui.root.winfo_children() if isinstance(w, tk.Toplevel))
    tree = next(w for w in descendants(dialog) if isinstance(w, ttk.Treeview))
    assert tree.item(tree.get_children()[0], "values")[4] == "Inaktiv"
    confirm = next(w for w in descendants(dialog) if isinstance(w, ttk.Button) and w.cget("text") == "Import ausdrücklich bestätigen")
    monkeypatch.setattr(gui_main.messagebox, "askyesno", lambda *a, **kw: False)
    confirm.invoke()
    assert gui.student_registry.csv_path.read_bytes() == original
    assert not list(tmp_path.glob("*.bak*"))
    monkeypatch.setattr(gui_main.messagebox, "askyesno", lambda *a, **kw: True)
    confirm.invoke()
    drain_registry_task(gui)
    assert gui.student_tree.get_children() == ("1", "3")
    assert gui.student_registry.match("New Student").student.active is False
    assert (tmp_path / "Schuler.csv.bak").read_bytes() == original
    assert not errors
