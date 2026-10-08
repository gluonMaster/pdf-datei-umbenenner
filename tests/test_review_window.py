"""Review and student tabs on real Tk widgets with temporary, synthetic data."""

from concurrent.futures import Future
from datetime import date
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw
import pytest

import gui_main
from alias_manager import AliasManager
from file_renamer import FileRenamer
from gui_main import PDFRenamerGUI
from review_actions import ReviewActionService
from review_preview import PreviewImages
from review_queue import ReviewItem, ReviewStore
from student_registry import StudentRegistry
from student_registry_service import StudentRegistryService
from text_parser import TextParser
from validator import Validator


@pytest.fixture
def review_gui(tmp_path, monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"Tk display unavailable: {error}")
    root.withdraw()
    csv_path = tmp_path / "Schuler.csv"
    csv_path.write_text(
        "Nummer;Nachname;Vorname\n1;Van Buren;Alex\n2;Muster;Max\n3;Muster;Moritz\n4;Probe;Paula\n",
        encoding="utf-8-sig",
    )
    gui = PDFRenamerGUI.__new__(PDFRenamerGUI)
    gui.root = root
    gui.processing = False
    gui._student_task_running = False
    gui.notebook = ttk.Notebook(root)
    gui.subjects = ["Deutsch", "Mathematik"]
    gui.student_registry = StudentRegistry(csv_path)
    gui.student_admin = StudentRegistryService(gui.student_registry)
    gui.review_store = ReviewStore(tmp_path / "manual_review.json")
    gui.review_actions = ReviewActionService(
        gui.student_registry,
        AliasManager(tmp_path / "subjects_aliases.json", create_if_missing=False),
        gui.review_store,
        FileRenamer(),
    )
    gui.logs = []
    gui.log_message = gui.logs.append
    gui.stats = gui._empty_stats()
    gui.update_statistics = lambda: None
    gui._set_batch_controls = lambda _running: None
    gui.setup_review_tab()
    gui.setup_students_tab()
    dialogs = []
    for name in ("showerror", "showwarning", "showinfo"):
        monkeypatch.setattr(
            gui_main.messagebox, name, lambda *a, _n=name, **k: dialogs.append((_n, a))
        )
    monkeypatch.setattr(gui_main.messagebox, "askyesno", lambda *a, **k: True)
    gui.dialogs = dialogs
    try:
        yield gui
    finally:
        if gui._preview_executor is not None:
            gui._preview_executor.shutdown(wait=True)
        root.destroy()


def pump(gui, milliseconds=300):
    done = tk.BooleanVar(master=gui.root, value=False)
    gui.root.after(milliseconds, lambda: done.set(True))
    gui.root.wait_variable(done)


def _pdf(path):
    image = Image.new("RGB", (850, 1100), "white")
    ImageDraw.Draw(image).rectangle((100, 200, 400, 240), fill="black")
    image.save(path, "PDF", resolution=100.0)


def show(gui, tmp_path, **fields):
    """Queue one entry, display it in the list and select it."""

    name = fields.pop("file_path", "manual_check/scan.pdf")
    source = tmp_path / name
    source.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        _pdf(source)
    defaults = dict(
        raw_name="Mustr Max", raw_subject="Deutsch", reason="Prüfung", suggestions=[],
        preview_text="", selected_month=3, selected_year=2031,
        canonical_subject="Deutsch", student_match_status="unknown",
    )
    defaults.update(fields)
    item = ReviewItem(name, **defaults)
    gui.review_store.add(item)
    assert gui.review_store.save()
    gui.load_review_queue(reload_store=False)
    row = next(
        row for row in gui.review_tree.get_children()
        if gui.review_tree.item(row, "tags")[0] == name
    )
    gui.review_tree.selection_set(row)
    gui.on_review_item_selected()
    return item, source


def listed(gui):
    return list(gui.student_listbox.get(0, tk.END))


def test_clear_leader_is_preselected_with_registry_name_boundaries(review_gui, tmp_path):
    show(review_gui, tmp_path, student_candidates=[
        {"number": 1, "name": "Van Buren Alex", "score": 0.93},
        {"number": 4, "name": "Probe Paula", "score": 0.4},
    ])

    assert review_gui.selected_student_number == 1
    assert (review_gui.edit_surname.get(), review_gui.edit_name.get()) == ("Van Buren", "Alex")
    assert str(review_gui.apply_existing_button.cget("state")) == "normal"
    assert listed(review_gui)[0].startswith("★ Nr. 1")
    assert "ähnlicher Eintrag" in review_gui.status_label.cget("text")


def test_live_search_keyboard_selects_but_never_applies(review_gui, tmp_path):
    item, source = show(review_gui, tmp_path)
    renders = []
    original = review_gui._render_student_results
    review_gui._render_student_results = lambda *a: (renders.append(1), original(*a))

    for text in ("m", "ma", "max", "max mus"):  # quick typing: one delayed search
        review_gui.student_query.set(text)
    assert review_gui.selected_student_number is None
    pump(review_gui)
    assert len(renders) == 1
    assert [entry.split(" — ")[1] for entry in listed(review_gui)] == ["Muster Max"]

    review_gui._on_student_search_down()
    review_gui._on_student_search_return()

    assert review_gui.selected_student_number == 2
    assert review_gui.edit_surname.get() == "Muster"
    assert source.exists() and review_gui.review_store.count() == 1  # nothing applied


def test_changed_query_or_document_drops_the_previous_choice(review_gui, tmp_path):
    show(review_gui, tmp_path, student_candidates=[{"number": 4, "score": 0.9}])
    assert review_gui.selected_student_number == 4

    review_gui.student_query.set("muster")
    assert review_gui.selected_student_number is None
    assert str(review_gui.apply_existing_button.cget("state")) == "disabled"

    review_gui._on_student_search_escape()
    assert review_gui.student_query.get() == ""

    show(review_gui, tmp_path, file_path="manual_check/second.pdf", raw_name="Neu Nina")
    assert review_gui.selected_student_number is None
    assert review_gui.student_query.get() == ""


def test_search_by_number_and_no_result_hint(review_gui, tmp_path):
    show(review_gui, tmp_path)

    review_gui.student_query.set("4")
    pump(review_gui)
    assert listed(review_gui) == ["Nr. 4 — Probe Paula"]
    review_gui.student_query.set("qqq")
    pump(review_gui)
    assert listed(review_gui) == []
    assert "Keine Treffer" in review_gui.student_search_hint.cget("text")


def test_scan_fragments_are_shown_and_stale_results_ignored(review_gui, tmp_path):
    show(review_gui, tmp_path, field_evidence=[
        {"field": "student_name", "bbox": {"x0": 72, "top": 144, "x1": 288, "bottom": 173}},
    ])
    for _ in range(50):
        pump(review_gui, 100)
        if review_gui._preview_photos:
            break
    assert len(review_gui._preview_photos) == 1

    stale = Future()
    stale.set_result(PreviewImages(error="alt"))
    review_gui._poll_preview(stale, object())  # token of a previous document
    assert len(review_gui._preview_photos) == 1


def test_missing_pdf_reports_preview_error_without_blocking(review_gui, tmp_path):
    item, source = show(review_gui, tmp_path)
    review_gui._wait_for_preview()
    source.unlink()  # removed outside the application
    review_gui.clear_edit_fields()
    review_gui._request_preview(item)
    for _ in range(50):
        pump(review_gui, 100)
        texts = [w.cget("text") for w in review_gui.preview_images_frame.winfo_children()]
        if any("Vorschau nicht verfügbar" in text for text in texts):
            break
    assert any("Vorschau nicht verfügbar" in text for text in texts)


def test_new_student_with_birth_date_is_added_and_applied(review_gui, tmp_path):
    item, source = show(
        review_gui, tmp_path, raw_name="Neu Nina", birth_date="2015-09-09",
        new_student_proposal={"last_name": "Neu", "first_name": "Nina", "birth_date": "2015-09-09"},
        field_evidence=[{"field": "birth_date", "uncertainties": []}],
    )
    assert (review_gui.edit_surname.get(), review_gui.edit_name.get()) == ("Neu", "Nina")
    assert review_gui.edit_birth_date.get() == "09.09.2015"

    review_gui.apply_correction()

    student = next(s for s in review_gui.student_registry.students if s.last_name == "Neu")
    assert student.birth_date == date(2015, 9, 9)
    assert not source.exists() and review_gui.review_store.count() == 0
    assert (tmp_path / "Neu_Nina_Deutsch_März_2031.pdf").exists()
    assert review_gui.student_tree.item(str(student.number), "values")[3] == "09.09.2015"


def test_invalid_operator_birth_date_stops_the_action(review_gui, tmp_path):
    item, source = show(review_gui, tmp_path, raw_name="Neu Nina")
    review_gui.edit_birth_date.set("2015-09-09")

    review_gui.apply_correction()

    assert review_gui.dialogs and review_gui.dialogs[-1][0] == "showerror"
    assert source.exists() and review_gui.review_store.count() == 1


def test_subject_proposal_is_adopted_in_one_step(review_gui, tmp_path, monkeypatch):
    config = tmp_path / "subjects_config.json"
    config.write_text(json.dumps({
        "subjects": ["Deutsch", "Mathematik"], "aliases": {"Mathe": "Mathematik"},
    }), encoding="utf-8")
    review_gui.subjects_config_path = config
    monkeypatch.setattr(Validator, "_config_path", config)
    monkeypatch.setattr(Validator, "_subjects_cache", None)
    monkeypatch.setattr(TextParser, "_config_path", config)
    monkeypatch.setattr(TextParser, "_subjects_cache", None)
    show(review_gui, tmp_path, raw_subject="Robotik", canonical_subject=None,
         subject_proposal="Robotik")
    button = next(
        widget for widget in review_gui.suggestions_frame.winfo_children()
        if "Robotik" in widget.cget("text")
    )

    button.invoke()

    assert review_gui.edit_subject.get() == "Robotik"
    assert "Robotik" in review_gui.subjects
    assert "Robotik" in Validator.get_valid_subjects()
    data = json.loads(config.read_text(encoding="utf-8"))
    assert data["aliases"] == {"Mathe": "Mathematik"}


def test_conflicting_subject_list_is_not_learned_as_alias(review_gui, tmp_path):
    item, source = show(
        review_gui, tmp_path, raw_subject="Deutsch, Englisch", canonical_subject=None,
        student_candidates=[{"number": 2, "score": 0.9}],
    )
    review_gui.edit_subject.set("Deutsch")

    review_gui.apply_existing_student()

    assert not source.exists()
    assert not (tmp_path / "subjects_aliases.json").exists()


def test_student_tab_edits_birth_dates(review_gui, monkeypatch):
    monkeypatch.setattr(
        review_gui, "_show_student_editor",
        lambda *a: ("Neu", "Nina", True, date(2015, 9, 9)),
    )
    review_gui.add_student()
    number = int(review_gui.student_tree.selection()[0])
    assert review_gui.student_tree.item(str(number), "values")[3] == "09.09.2015"

    monkeypatch.setattr(
        review_gui, "_show_student_editor", lambda *a: ("Neu", "Nina", True, None)
    )
    review_gui.edit_student()
    assert review_gui.student_registry.select_existing(number).birth_date is None
    csv_text = review_gui.student_registry.csv_path.read_text(encoding="utf-8-sig")
    assert csv_text.splitlines()[0].endswith("Geburtsdatum")
