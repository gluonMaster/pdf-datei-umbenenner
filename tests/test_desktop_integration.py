"""Single instance, window size, icon and the pythonw entry point."""

import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import ttk
import types

import pytest

import desktop_integration
import start_gui


ROOT = Path(__file__).resolve().parents[1]
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Windows desktop API")


@pytest.mark.parametrize("work_area,expected", [
    # 1536x864 at 100 % or 1920x1080 at 125 %: full size, centred.
    ((0, 0, 1536, 816), ("1200x760+160+8", False)),
    ((0, 0, 1920, 1040), ("1200x760+352+120", False)),
    # 1366x768 and 1280x720 (1920x1080 at 150 %): maximized.
    ((0, 0, 1366, 728), ("1200x689+0+0", True)),
    ((0, 0, 1280, 672), ("1200x633+0+0", True)),
])
def test_initial_window_fits_the_work_area(work_area, expected):
    assert desktop_integration.initial_window_geometry(work_area) == expected


@WINDOWS
def test_second_instance_of_the_same_installation_is_refused(tmp_path):
    first, other = tmp_path / "app", tmp_path / "other app"
    first.mkdir()
    other.mkdir()
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; sys.path.insert(0, sys.argv[1]); import desktop_integration as d; "
         "from pathlib import Path; print(d.acquire_single_instance(Path(sys.argv[2])), flush=True); "
         "sys.stdin.read()",
         str(ROOT), str(first)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "True"
        assert desktop_integration.acquire_single_instance(first) is False
        # Another installation works with other files and may run.
        assert desktop_integration.acquire_single_instance(other) is True
    finally:
        holder.stdin.close()
        holder.wait(timeout=20)
    # The lock disappears with its process; nothing stale remains.
    assert desktop_integration.acquire_single_instance(first) is True


def test_second_start_reports_and_does_not_open_a_window(monkeypatch):
    reported = []
    monkeypatch.setattr(desktop_integration, "acquire_single_instance", lambda _dir: False)
    monkeypatch.setattr(desktop_integration, "report_already_running", lambda: reported.append(True))
    monkeypatch.setitem(sys.modules, "gui_main", types.SimpleNamespace(
        main=lambda: pytest.fail("the GUI must not start twice")
    ))
    assert start_gui.run() == 0
    assert reported == [True]


def test_start_failure_without_console_goes_to_gui_log_and_message_box(tmp_path, monkeypatch):
    messages = []
    log = tmp_path / ".launcher-logs" / "gui.log"
    monkeypatch.setattr(start_gui, "GUI_LOG", log)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    monkeypatch.setattr(desktop_integration, "acquire_single_instance", lambda _dir: True)
    monkeypatch.setattr(
        desktop_integration, "show_message",
        lambda title, text, error=False: messages.append((text, error)),
    )
    monkeypatch.setitem(sys.modules, "gui_main", None)  # import fails
    console = sys.stderr
    try:
        assert start_gui.run() == 1
    finally:
        if sys.stderr is not console:
            sys.stderr.close()
    assert "Traceback" in log.read_text(encoding="utf-8")
    assert messages and messages[0][1] is True
    assert "gui.log" in messages[0][0]


@WINDOWS
def test_window_icon_is_applied_from_the_program_folder(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"Tk display unavailable: {error}")
    try:
        root.withdraw()
        assert desktop_integration.apply_window_icon(root, ROOT) is True
        assert desktop_integration.apply_window_icon(root, tmp_path) is False
    finally:
        root.destroy()


@WINDOWS
@pytest.mark.parametrize("width,height", [(1280, 649), (1366, 697), (1200, 760)])
def test_review_controls_stay_reachable_on_small_screens(tmp_path, width, height):
    import gui_main
    from review_queue import ReviewStore
    from student_registry import StudentRegistry
    from student_registry_service import StudentRegistryService

    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"Tk display unavailable: {error}")
    root.attributes("-alpha", 0.0)
    csv_path = tmp_path / "Schuler.csv"
    csv_path.write_text("Nummer;Nachname;Vorname\n1;Muster;Max\n", encoding="utf-8")
    gui = gui_main.PDFRenamerGUI.__new__(gui_main.PDFRenamerGUI)
    gui.root = root
    gui.compact_layout = height < 760
    gui.processing = False
    gui._student_task_running = False
    gui.subjects = ["Deutsch"]
    gui.selected_folder = tk.StringVar(master=root)
    gui.dry_run_mode = tk.BooleanVar(master=root)
    gui.selected_month = tk.IntVar(master=root, value=1)
    gui.selected_year = tk.IntVar(master=root, value=2031)
    gui.progress_var = tk.DoubleVar(master=root)
    gui.student_registry = StudentRegistry(csv_path)
    gui.student_admin = StudentRegistryService(gui.student_registry)
    gui.review_store = ReviewStore(tmp_path / "manual_review.json")
    try:
        gui.setup_ui()
        # Long status and reason texts take three lines each.
        long_text = "Name nicht eindeutig: mehrere ähnliche Schüler im Verzeichnis gefunden. " * 2
        gui.status_label.config(text=long_text)
        gui.reason_label.config(text=long_text)
        root.geometry(f"{width}x{height}+0+0")
        gui.notebook.select(1)
        root.update()
        bottom = root.winfo_rooty() + root.winfo_height()
        frame = gui.notebook.nametowidget(gui.notebook.select())
        controls = []
        stack = [frame]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            if widget.winfo_class() in ("TButton", "TEntry", "TCombobox", "TCheckbutton", "Listbox"):
                controls.append(widget)
        assert len(controls) >= 12
        for widget in controls:
            assert widget.winfo_ismapped(), widget
            assert widget.winfo_rooty() + widget.winfo_height() <= bottom, widget
        # The result list keeps room for several students.
        assert gui.student_listbox.winfo_height() >= 60
    finally:
        root.destroy()
