"""Tkinter interface for the shared PDF document processing service."""

from __future__ import annotations

from datetime import datetime
import json
import queue
from pathlib import Path
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from alias_manager import AliasManager
from file_opener import FileOpener
from file_renamer import FileRenamer
from form_parser import FormParser
from gui_processing import BatchRunner, BatchSettings, WorkerEventKind
from pdf_extractor import OCRFallbackExtractor
from processing_service import DocumentProcessingService, PlannedAction
from review_actions import (
    ReviewActionRequest,
    ReviewActionService,
    ReviewActionStatus,
    ReviewDecision,
)
from review_queue import ReviewStore
from student_registry_service import ImportPreview, StudentRegistryService
from text_parser import TextParser
from validator import Validator


class PDFRenamerGUI:
    """Main application window.

    The worker thread performs only service and filesystem work.  Every
    Tkinter operation is dispatched from ``_drain_worker_events`` on the main
    thread.
    """

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PDF Datei Umbenenner - Automatisches Umbenennen")
        self.root.geometry("1200x760")
        self.root.resizable(True, True)

        now = datetime.now()
        self.selected_folder = tk.StringVar()
        self.dry_run_mode = tk.BooleanVar(value=False)
        self.selected_month = tk.IntVar(value=now.month)
        self.selected_year = tk.IntVar(value=now.year)
        self.progress_var = tk.DoubleVar(value=0)
        self.processing = False
        self._student_task_running = False
        self._close_requested = False
        self._active_batch_folder = None
        self._batch_queue_failed = False
        self.stop_event = threading.Event()
        self.worker_events: queue.Queue = queue.Queue()

        project_folder = Path(__file__).parent
        self.extractor = OCRFallbackExtractor()
        self.parser = FormParser()
        self.validator = Validator()
        self.renamer = FileRenamer()
        self.review_store = ReviewStore()
        self.student_admin = StudentRegistryService.from_directory(project_folder)
        self.student_registry = self.student_admin.registry
        self.processing_service = DocumentProcessingService(
            extractor=self.extractor,
            parser=self.parser,
            student_registry=self.student_registry,
            validator=self.validator,
            review_store=self.review_store,
            renamer=self.renamer,
        )
        self.alias_manager = AliasManager(create_if_missing=False)
        TextParser.set_alias_manager(self.alias_manager)
        self.review_actions = ReviewActionService(
            self.student_registry,
            self.alias_manager,
            self.review_store,
            self.renamer,
        )
        self.batch_runner = BatchRunner(self.processing_service)

        self.subjects: list[str] = []
        self.load_subjects_config()
        self.stats = self._empty_stats()
        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(50, self._drain_worker_events)
        if self.alias_manager.load_error:
            self.root.after(0, self._show_alias_load_error)

    @staticmethod
    def _empty_stats() -> dict[str, int]:
        return {"total": 0, "success": 0, "warning": 0, "manual": 0, "error": 0}

    def load_subjects_config(self):
        config_path = Path(__file__).parent / "subjects_config.json"
        try:
            with config_path.open("r", encoding="utf-8") as handle:
                self.subjects = json.load(handle).get("subjects", [])
        except (FileNotFoundError, json.JSONDecodeError, OSError) as error:
            self.subjects = []
            print(f"Warnung: Konfigurationsdatei konnte nicht geladen werden: {error}")

    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)

        ttk.Label(
            main_frame, text="PDF Datei Umbenenner", font=("Arial", 16, "bold")
        ).grid(row=0, column=0, pady=(0, 15))
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.setup_processing_tab()
        self.setup_review_tab()
        self.setup_students_tab()

    def setup_processing_tab(self):
        frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(frame, text="Verarbeitung")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(4, weight=1)

        folder_frame = ttk.LabelFrame(frame, text="Ordner-Auswahl", padding="10")
        folder_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        folder_frame.columnconfigure(1, weight=1)
        ttk.Label(folder_frame, text="Eingabeordner:").grid(row=0, column=0, sticky=tk.W)
        self.folder_entry = ttk.Entry(
            folder_frame, textvariable=self.selected_folder, width=50
        )
        self.folder_entry.grid(
            row=0, column=1, padx=10, sticky=(tk.W, tk.E)
        )
        self.browse_button = ttk.Button(
            folder_frame, text="Durchsuchen...", command=self.browse_folder
        )
        self.browse_button.grid(row=0, column=2)

        settings = ttk.LabelFrame(frame, text="Einstellungen", padding="10")
        settings.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        self.dry_run_check = ttk.Checkbutton(
            settings,
            text="Testmodus (keine Änderungen)",
            variable=self.dry_run_mode,
        )
        self.dry_run_check.grid(
            row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 8)
        )
        ttk.Label(settings, text="Monat:").grid(row=1, column=0, sticky=tk.W)
        self.month_combo = ttk.Combobox(
            settings,
            textvariable=self.selected_month,
            values=list(range(1, 13)),
            state="readonly",
            width=10,
        )
        self.month_combo.grid(row=1, column=1, sticky=tk.W, padx=(8, 25))
        ttk.Label(settings, text="Jahr:").grid(row=1, column=2, sticky=tk.W)
        self.year_entry = ttk.Entry(
            settings, textvariable=self.selected_year, width=10
        )
        self.year_entry.grid(
            row=1, column=3, sticky=tk.W, padx=(8, 0)
        )

        progress = ttk.LabelFrame(frame, text="Fortschritt", padding="10")
        progress.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        progress.columnconfigure(0, weight=1)
        ttk.Progressbar(progress, variable=self.progress_var, maximum=100).grid(
            row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 5)
        )
        self.progress_label = ttk.Label(progress, text="Bereit zum Starten")
        self.progress_label.grid(row=1, column=0)

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, pady=8)
        self.start_button = ttk.Button(
            buttons,
            text="Verarbeitung starten",
            command=self.start_processing,
            style="Accent.TButton",
        )
        self.start_button.pack(side=tk.LEFT, padx=(0, 10))
        self.stop_button = ttk.Button(
            buttons, text="Stoppen", command=self.stop_processing, state=tk.DISABLED
        )
        self.stop_button.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(buttons, text="Log löschen", command=self.clear_log).pack(side=tk.LEFT)

        log_frame = ttk.LabelFrame(frame, text="Verarbeitungslog", padding="10")
        log_frame.grid(row=4, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=14, width=80)
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        stats_frame = ttk.LabelFrame(frame, text="Statistik", padding="10")
        stats_frame.grid(row=5, column=0, sticky=(tk.W, tk.E))
        self.stats_text = tk.Text(stats_frame, height=6, width=80, state=tk.DISABLED)
        self.stats_text.pack(fill=tk.X, expand=True)

    def setup_review_tab(self):
        frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(frame, text="Prüfung")
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=2)
        frame.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(frame, text="Dateien zur Prüfung", padding="10")
        left.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(0, 5))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        scroll_y = ttk.Scrollbar(left, orient=tk.VERTICAL)
        scroll_x = ttk.Scrollbar(left, orient=tk.HORIZONTAL)
        self.review_tree = ttk.Treeview(
            left,
            columns=("filename", "name", "subject", "reason"),
            show="headings",
            yscrollcommand=scroll_y.set,
            xscrollcommand=scroll_x.set,
        )
        scroll_y.config(command=self.review_tree.yview)
        scroll_x.config(command=self.review_tree.xview)
        for key, title, width in (
            ("filename", "Dateiname", 200),
            ("name", "Gefundener Name", 150),
            ("subject", "Gefundenes Fach", 120),
            ("reason", "Grund", 250),
        ):
            self.review_tree.heading(key, text=title)
            self.review_tree.column(key, width=width, minwidth=90)
        self.review_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scroll_y.grid(row=0, column=1, sticky=(tk.N, tk.S))
        scroll_x.grid(row=1, column=0, sticky=(tk.W, tk.E))
        ttk.Button(left, text="Liste aktualisieren", command=self.load_review_queue).grid(
            row=2, column=0, columnspan=2, pady=(10, 0), sticky=tk.W
        )
        self.review_tree.bind("<<TreeviewSelect>>", self.on_review_item_selected)
        self.review_tree.bind("<Double-Button-1>", self.on_review_item_double_click)

        right = ttk.LabelFrame(frame, text="Bestätigung", padding="10")
        right.grid(row=0, column=1, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(5, 0))
        right.columnconfigure(0, weight=1)
        right.columnconfigure(1, weight=1)
        right.rowconfigure(16, weight=1)

        self.edit_surname = tk.StringVar()
        self.edit_name = tk.StringVar()
        self.edit_subject = tk.StringVar()
        self.selected_student = tk.StringVar()
        self.save_student_alias = tk.BooleanVar(value=True)
        self.student_choice_numbers: dict[str, int] = {}
        self.current_review_item = None

        self.file_label = self._detail_row(right, 0, "PDF:")
        self.form_type_label = self._detail_row(right, 1, "Formulartyp:")
        self.raw_name_label = self._detail_row(right, 2, "Erkannter Name:")
        self.reason_label = self._detail_row(right, 3, "Prüfgrund:", wrap=310)

        ttk.Label(right, text="Nachname:").grid(row=4, column=0, sticky=tk.W)
        ttk.Label(right, text="Vorname:").grid(row=4, column=1, sticky=tk.W)
        ttk.Entry(right, textvariable=self.edit_surname).grid(
            row=5, column=0, sticky=(tk.W, tk.E), padx=(0, 4), pady=(0, 6)
        )
        ttk.Entry(right, textvariable=self.edit_name).grid(
            row=5, column=1, sticky=(tk.W, tk.E), padx=(4, 0), pady=(0, 6)
        )
        ttk.Label(right, text="Fach:").grid(row=6, column=0, sticky=tk.W)
        ttk.Combobox(
            right, textvariable=self.edit_subject, values=self.subjects, state="normal"
        ).grid(row=7, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 3))
        self.suggestions_frame = ttk.Frame(right)
        self.suggestions_frame.grid(
            row=8, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 5)
        )

        ttk.Label(right, text="Ähnliche / bestehende Schüler:").grid(
            row=9, column=0, columnspan=2, sticky=tk.W
        )
        self.student_combo = ttk.Combobox(
            right, textvariable=self.selected_student, state="readonly"
        )
        self.student_combo.grid(row=10, column=0, columnspan=2, sticky=(tk.W, tk.E))
        self.student_combo.bind("<<ComboboxSelected>>", self._on_student_choice)
        ttk.Checkbutton(
            right,
            text="Erkannten Namen als Alias speichern",
            variable=self.save_student_alias,
        ).grid(row=11, column=0, columnspan=2, sticky=tk.W, pady=(2, 6))

        ttk.Button(
            right,
            text="Bestehenden Schüler auswählen und anwenden",
            command=self.apply_existing_student,
        ).grid(row=12, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 4))
        ttk.Button(
            right,
            text="Als neuen Schüler hinzufügen und anwenden",
            command=self.apply_correction,
            style="Accent.TButton",
        ).grid(row=13, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 4))
        ttk.Button(
            right,
            text="Nur umbenennen (Schüler nicht speichern)",
            command=self.rename_without_student,
        ).grid(row=14, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 4))
        ttk.Button(
            right,
            text="Aus Liste entfernen (ohne Umbenennung)",
            command=self.remove_from_queue,
        ).grid(row=15, column=0, columnspan=2, sticky=(tk.W, tk.E))

        preview = ttk.LabelFrame(right, text="Textauszug", padding="5")
        preview.grid(
            row=16,
            column=0,
            columnspan=2,
            sticky=(tk.W, tk.E, tk.N, tk.S),
            pady=(8, 0),
        )
        self.preview_text = tk.Text(
            preview, height=4, width=40, wrap=tk.WORD, state=tk.DISABLED
        )
        preview_scroll = ttk.Scrollbar(preview, command=self.preview_text.yview)
        self.preview_text.config(yscrollcommand=preview_scroll.set)
        preview_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.preview_text.pack(fill=tk.BOTH, expand=True)

    def setup_students_tab(self):
        frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(frame, text="Schüler")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        filters = ttk.LabelFrame(frame, text="Suche und Filter", padding="8")
        filters.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 8))
        filters.columnconfigure(1, weight=1)
        self.student_search = tk.StringVar()
        self.student_active_filter = tk.StringVar(value="Alle")
        ttk.Label(filters, text="Suche:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(filters, textvariable=self.student_search).grid(
            row=0, column=1, sticky=(tk.W, tk.E), padx=(8, 16)
        )
        ttk.Label(filters, text="Status:").grid(row=0, column=2, sticky=tk.W)
        active_combo = ttk.Combobox(
            filters,
            textvariable=self.student_active_filter,
            values=("Alle", "Aktiv", "Inaktiv"),
            state="readonly",
            width=12,
        )
        active_combo.grid(row=0, column=3, sticky=tk.W, padx=(8, 0))
        active_combo.bind("<<ComboboxSelected>>", self.load_students_table)
        self.student_search.trace_add("write", lambda *_: self.load_students_table())

        table = ttk.Frame(frame)
        table.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        scroll_y = ttk.Scrollbar(table, orient=tk.VERTICAL)
        self.student_tree = ttk.Treeview(
            table,
            columns=("number", "last_name", "first_name", "active"),
            show="headings",
            yscrollcommand=scroll_y.set,
            selectmode="browse",
        )
        scroll_y.config(command=self.student_tree.yview)
        for key, title, width, anchor in (
            ("number", "Nummer", 90, tk.E),
            ("last_name", "Nachname", 260, tk.W),
            ("first_name", "Vorname", 260, tk.W),
            ("active", "Status", 120, tk.CENTER),
        ):
            self.student_tree.heading(key, text=title)
            self.student_tree.column(key, width=width, minwidth=70, anchor=anchor)
        self.student_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scroll_y.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.student_tree.bind("<Double-Button-1>", lambda _event: self.edit_student())

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(8, 4))
        self.student_mutation_buttons = []
        for text, command in (
            ("Neu", self.add_student),
            ("Bearbeiten", self.edit_student),
            ("Aktivieren / Deaktivieren", self.toggle_student_active),
            ("Aliase", self.show_student_aliases),
            ("Duplikate suchen", self.show_student_duplicates),
            ("CSV importieren…", self.import_students_csv),
            ("Aktualisieren", self.reload_students),
        ):
            button = ttk.Button(buttons, text=text, command=command)
            button.pack(
                side=tk.LEFT, padx=(0, 6)
            )
            if text in {
                "Neu",
                "Bearbeiten",
                "Aktivieren / Deaktivieren",
                "Aliase",
                "CSV importieren…",
                "Duplikate suchen",
                "Aktualisieren",
            }:
                self.student_mutation_buttons.append(button)

        self.student_registry_status = ttk.Label(frame, text="")
        self.student_registry_status.grid(row=3, column=0, sticky=tk.W)
        self.load_students_table()

    def _student_filter_value(self):
        return {"Aktiv": True, "Inaktiv": False}.get(self.student_active_filter.get())

    def _ensure_idle(self) -> bool:
        if getattr(self, "_student_task_running", False):
            messagebox.showwarning("Schüler", "Bitte warten Sie, bis die Schülerliste geprüft oder gespeichert wurde.")
            return False
        if not self.processing:
            return True
        messagebox.showwarning(
            "Verarbeitung läuft",
            "Diese Änderung ist erst nach dem Batch-Ende möglich.",
        )
        return False

    def _run_student_task(self, title, work, on_success):
        """Run registry I/O/comparisons off Tk's thread; deliver results via after.

        A modal progress window and the busy guard serialize registry changes
        with review actions and PDF batches. The worker never calls Tkinter.
        """
        if not self._ensure_idle():
            return
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)
        ttk.Label(dialog, text=f"{title} …", padding=16).pack()
        progress = ttk.Progressbar(dialog, mode="indeterminate", length=320)
        progress.pack(padx=16, pady=(0, 16))
        dialog.grab_set()
        progress.start(20)
        self._student_task_running = True
        self._set_batch_controls(self.processing)
        results = queue.Queue()

        def run():
            try:
                results.put((work(), None))
            except Exception as error:
                results.put((None, error))

        def finish(value, error):
            progress.stop()
            dialog.destroy()
            self._student_task_running = False
            self._set_batch_controls(self.processing)
            if error is not None:
                messagebox.showerror(title, str(error), parent=self.root)
            else:
                on_success(value)

        def poll():
            try:
                value, error = results.get_nowait()
            except queue.Empty:
                self.root.after(50, poll)
            else:
                finish(value, error)

        try:
            self.student_task_thread = threading.Thread(target=run, daemon=True)
            self.student_task_thread.start()
        except Exception as error:
            finish(None, error)
            return
        self.root.after(50, poll)

    def reload_students(self):
        def loaded(available):
            self._after_student_change()
            if not available:
                messagebox.showerror("Schülerverzeichnis", self.student_registry.load_error)

        self._run_student_task("Schülerliste aktualisieren", self.student_registry.reload, loaded)

    def load_students_table(self, _event=None):
        if not hasattr(self, "student_tree"):
            return
        selected = self.student_tree.selection()
        selected_number = selected[0] if selected else None
        self.student_tree.delete(*self.student_tree.get_children())
        students = self.student_admin.search(
            self.student_search.get(), self._student_filter_value()
        )
        for student in students:
            self.student_tree.insert(
                "",
                tk.END,
                iid=str(student.number),
                values=(
                    student.number,
                    student.last_name,
                    student.first_name,
                    "Aktiv" if student.active else "Inaktiv",
                ),
            )
        if selected_number and self.student_tree.exists(selected_number):
            self.student_tree.selection_set(selected_number)
        diagnostic = self.student_registry.load_error
        status = diagnostic or (
            f"{len(students)} von {len(self.student_registry.students)} Einträgen | "
            f"Datei: {self.student_admin.paths.csv_path}"
        )
        self.student_registry_status.config(text=status)

    def _selected_student_number(self):
        selected = self.student_tree.selection()
        if not selected:
            messagebox.showwarning("Schüler", "Bitte wählen Sie einen Schüler aus.")
            return None
        return int(selected[0])

    def _show_student_editor(self, title, student=None):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.grab_set()
        last_name = tk.StringVar(value=student.last_name if student else "")
        first_name = tk.StringVar(value=student.first_name if student else "")
        active = tk.BooleanVar(value=student.active if student else True)
        result = []
        body = ttk.Frame(dialog, padding="12")
        body.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="Nachname:").grid(row=0, column=0, sticky=tk.W, pady=4)
        last_entry = ttk.Entry(body, textvariable=last_name, width=34)
        last_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(8, 0), pady=4)
        ttk.Label(body, text="Vorname:").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Entry(body, textvariable=first_name).grid(
            row=1, column=1, sticky=(tk.W, tk.E), padx=(8, 0), pady=4
        )
        ttk.Checkbutton(body, text="Aktiv", variable=active).grid(
            row=2, column=0, columnspan=2, sticky=tk.W, pady=(4, 10)
        )

        def accept():
            if not last_name.get().strip() or not first_name.get().strip():
                messagebox.showwarning(
                    title, "Nachname und Vorname sind erforderlich.", parent=dialog
                )
                return
            result.append((last_name.get(), first_name.get(), active.get()))
            dialog.destroy()

        actions = ttk.Frame(body)
        actions.grid(row=3, column=0, columnspan=2, sticky=tk.E)
        ttk.Button(actions, text="Abbrechen", command=dialog.destroy).pack(
            side=tk.RIGHT, padx=(6, 0)
        )
        ttk.Button(actions, text="Speichern", command=accept).pack(side=tk.RIGHT)
        dialog.bind("<Return>", lambda _event: accept())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        last_entry.focus_set()
        self.root.wait_window(dialog)
        return result[0] if result else None

    def add_student(self):
        if not self._ensure_idle():
            return
        values = self._show_student_editor("Neuen Schüler anlegen")
        if values is None:
            return
        if not self._ensure_idle():
            return
        try:
            student = self.student_registry.add_student(
                values[0], values[1], active=values[2]
            )
        except Exception as error:
            messagebox.showerror("Schüler konnte nicht gespeichert werden", str(error))
            return
        self._after_student_change(student.number)

    def edit_student(self):
        if not self._ensure_idle():
            return
        number = self._selected_student_number()
        if number is None:
            return
        student = self.student_registry.select_existing(number)
        values = self._show_student_editor("Schüler bearbeiten", student)
        if values is None:
            return
        if not self._ensure_idle():
            return
        try:
            self.student_registry.update_student(
                number,
                last_name=values[0],
                first_name=values[1],
                active=values[2],
            )
        except Exception as error:
            messagebox.showerror("Schüler konnte nicht gespeichert werden", str(error))
            return
        self._after_student_change(number)

    def toggle_student_active(self):
        if not self._ensure_idle():
            return
        number = self._selected_student_number()
        if number is None:
            return
        student = self.student_registry.select_existing(number)
        action = "aktivieren" if not student.active else "deaktivieren"
        if not messagebox.askyesno(
            "Status ändern", f"{student.full_name} wirklich {action}?"
        ):
            return
        try:
            self.student_registry.update_student(number, active=not student.active)
        except Exception as error:
            messagebox.showerror("Status konnte nicht geändert werden", str(error))
            return
        self._after_student_change(number)

    def _after_student_change(self, selected_number=None):
        self.load_students_table()
        if selected_number is not None and self.student_tree.exists(str(selected_number)):
            self.student_tree.selection_set(str(selected_number))
            self.student_tree.see(str(selected_number))
        current = getattr(self, "current_review_item", None)
        if current is not None:
            self._update_student_choices(current)

    def show_student_aliases(self):
        if not self._ensure_idle():
            return
        number = self._selected_student_number()
        if number is None:
            return
        student = self.student_registry.select_existing(number)
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Aliase – {student.full_name}")
        dialog.transient(self.root)
        dialog.geometry("560x320")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)
        alias_tree = ttk.Treeview(dialog, columns=("alias",), show="headings")
        alias_tree.heading("alias", text="Bestätigter Alias")
        alias_tree.column("alias", width=500)
        alias_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=10)

        def refresh_aliases():
            alias_tree.delete(*alias_tree.get_children())
            for position, alias in enumerate(self.student_admin.aliases_for(number)):
                alias_tree.insert("", tk.END, iid=str(position), values=(alias.name,))

        def remove_selected():
            if not self._ensure_idle():
                return
            selected = alias_tree.selection()
            if not selected:
                messagebox.showwarning("Alias", "Bitte wählen Sie einen Alias aus.", parent=dialog)
                return
            alias_name = alias_tree.item(selected[0], "values")[0]
            if not messagebox.askyesno(
                "Alias löschen",
                f"Fehlerhaften Alias „{alias_name}“ wirklich löschen?",
                parent=dialog,
            ):
                return
            try:
                self.student_admin.remove_alias(alias_name)
            except Exception as error:
                messagebox.showerror("Alias konnte nicht gelöscht werden", str(error), parent=dialog)
                return
            refresh_aliases()
            self._after_student_change(number)

        actions = ttk.Frame(dialog)
        actions.grid(row=1, column=0, sticky=tk.E, padx=10, pady=(0, 10))
        ttk.Button(actions, text="Alias löschen", command=remove_selected).pack(side=tk.LEFT)
        ttk.Button(actions, text="Schließen", command=dialog.destroy).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        refresh_aliases()

    def show_student_duplicates(self):
        self._run_student_task(
            "Duplikate suchen",
            self.student_admin.find_potential_duplicates,
            self._show_student_duplicates,
        )

    def _show_student_duplicates(self, duplicates):
        if not duplicates:
            messagebox.showinfo("Duplikate", "Keine potenziellen Duplikate gefunden.")
            return
        lines = [
            f"{item.first.number}: {item.first.full_name}  ↔  "
            f"{item.second.number}: {item.second.full_name}  "
            f"({item.score:.0%}, {item.reason})"
            for item in duplicates
        ]
        dialog = tk.Toplevel(self.root)
        dialog.title("Potenzielle Duplikate")
        dialog.transient(self.root)
        text = scrolledtext.ScrolledText(dialog, width=90, height=24, wrap=tk.WORD)
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        text.insert("1.0", "\n".join(lines))
        text.config(state=tk.DISABLED)
        ttk.Button(dialog, text="Schließen", command=dialog.destroy).pack(pady=(0, 10))

    def import_students_csv(self):
        if not self._ensure_idle():
            return
        source = filedialog.askopenfilename(
            title="Neue Schülerliste auswählen",
            filetypes=(("CSV-Dateien", "*.csv"), ("Alle Dateien", "*.*")),
        )
        if not source:
            return
        self._run_student_task(
            "CSV prüfen", lambda: self.student_admin.preview_import(source),
            self._show_import_preview,
        )

    def _show_import_preview(self, preview: ImportPreview):
        dialog = tk.Toplevel(self.root)
        dialog.title("Importvorschau – keine Änderungen vorgenommen")
        dialog.transient(self.root)
        dialog.geometry("1000x600")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        ttk.Label(dialog, text=f"{preview.source_path.name}\n{preview.summary}", font=("Arial", 10, "bold")).grid(
            row=0, column=0, sticky=tk.W, padx=10, pady=10
        )
        table = ttk.Frame(dialog)
        table.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        scroll = ttk.Scrollbar(table, orient=tk.VERTICAL)
        scroll_x = ttk.Scrollbar(table, orient=tk.HORIZONTAL)
        tree = ttk.Treeview(
            table,
            columns=("category", "number", "last", "first", "active", "detail"),
            show="headings",
            yscrollcommand=scroll.set,
            xscrollcommand=scroll_x.set,
        )
        scroll.config(command=tree.yview)
        scroll_x.config(command=tree.xview)
        for key, title, width in (
            ("category", "Kategorie", 130),
            ("number", "Nummer", 80),
            ("last", "Nachname", 170),
            ("first", "Vorname", 170),
            ("active", "Status", 90),
            ("detail", "Hinweis", 390),
        ):
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=width, stretch=key == "detail")
        tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scroll.grid(row=0, column=1, sticky=(tk.N, tk.S))
        scroll_x.grid(row=1, column=0, sticky=(tk.W, tk.E))

        def add_row(category, student, detail=""):
            tree.insert(
                "",
                tk.END,
                values=(category, student.number, student.last_name, student.first_name,
                        "Aktiv" if student.active else "Inaktiv", detail),
            )

        for student in preview.new:
            add_row("Neu", student, "Wird hinzugefügt")
        for student in preview.unchanged:
            add_row("Unverändert", student)
        for issue in preview.conflicts:
            add_row("Konflikt", issue.student, f"Zeile {issue.row_number}: {issue.reason}")
        for issue in preview.duplicates:
            add_row("Duplikat", issue.student, f"Zeile {issue.row_number}: {issue.reason}")
        for student in preview.missing:
            add_row("Fehlt in neuer Liste", student, "Bleibt erhalten")

        note = (
            "Es werden ausschließlich neue, konfliktfreie Einträge hinzugefügt. "
            "Konflikte und Duplikate werden übersprungen; fehlende historische "
            "Einträge bleiben unverändert. Änderungen an bestehenden Schülern können Sie "
            "unter „Bearbeiten“ prüfen und speichern."
        )
        ttk.Label(dialog, text=note, wraplength=950).grid(
            row=2, column=0, sticky=tk.W, padx=10, pady=(8, 4)
        )
        actions = ttk.Frame(dialog)
        actions.grid(row=3, column=0, sticky=tk.E, padx=10, pady=(4, 10))

        def apply_preview():
            if not self._ensure_idle():
                return
            if not messagebox.askyesno(
                "Import bestätigen",
                f"{len(preview.new)} neue Schüler jetzt importieren?",
                parent=dialog,
            ):
                return
            self._run_student_task(
                "CSV importieren",
                lambda: self.student_admin.apply_import(preview, confirmed=True),
                imported,
            )

        def imported(result):
            if dialog.winfo_exists():
                dialog.destroy()
            self._after_student_change()
            backup = f"\nBackup: {result.backup_path}" if result.backup_path else ""
            messagebox.showinfo(
                "Import abgeschlossen",
                f"{len(result.added)} Schüler hinzugefügt.{backup}",
            )

        ttk.Button(actions, text="Abbrechen", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(
            actions,
            text="Import ausdrücklich bestätigen",
            command=apply_preview,
            state=tk.NORMAL if preview.new else tk.DISABLED,
        ).pack(side=tk.RIGHT, padx=(0, 6))

    @staticmethod
    def _detail_row(parent, row: int, title: str, wrap: int = 280):
        ttk.Label(parent, text=title, font=("Arial", 9, "bold")).grid(
            row=row, column=0, sticky=tk.NW, pady=(0, 3)
        )
        label = ttk.Label(parent, text="—", wraplength=wrap)
        label.grid(row=row, column=1, sticky=tk.W, pady=(0, 3))
        return label

    def browse_folder(self):
        if not self._ensure_idle():
            return
        folder = filedialog.askdirectory(title="Wählen Sie den Ordner mit PDF-Dateien")
        if not folder:
            return
        self.selected_folder.set(folder)
        self.review_store.store_path = Path(folder) / "manual_review.json"
        loaded = self.review_store.load()
        if not loaded:
            self._show_review_load_error()
        self.load_review_queue(reload_store=False)
        self.log_message(f"Ordner ausgewählt: {folder}")

    def _show_review_load_error(self):
        detail = self.review_store.load_error or "Unbekannter Lesefehler."
        self.log_message(f"❌ {detail}")
        messagebox.showerror("Warteschlange nicht geladen", detail)

    def _show_alias_load_error(self):
        detail = self.alias_manager.load_error or "Unbekannter Lesefehler."
        self.log_message(f"❌ {detail}")
        messagebox.showwarning(
            "Fachaliase nicht geladen",
            detail + "\nBekannte Fächer funktionieren weiterhin; neue Aliase werden nicht gespeichert.",
        )

    def log_message(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)

    def clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def update_statistics(self):
        text = f"""Verarbeitungsstatistik:
├─ Insgesamt verarbeitet:         {self.stats['total']}
├─ Erfolgreich umbenannt:         {self.stats['success']}
├─ Mit Warnungen umbenannt:       {self.stats['warning']}
├─ Manuelle Prüfung erforderlich: {self.stats['manual']}
└─ Fehler:                        {self.stats['error']}"""
        self.stats_text.config(state=tk.NORMAL)
        self.stats_text.delete(1.0, tk.END)
        self.stats_text.insert(1.0, text)
        self.stats_text.config(state=tk.DISABLED)

    def load_review_queue(
        self,
        reload_store=True,
        *,
        store_path_override=None,
        allow_during_processing=False,
    ):
        if self.processing and reload_store and not allow_during_processing:
            messagebox.showwarning(
                "Verarbeitung läuft",
                "Die Warteschlange wird nach dem Batch-Ende aktualisiert.",
            )
            return False
        # A reload may switch to another folder containing the same relative
        # filename.  Never keep the old ReviewItem or edited values attached
        # to the newly displayed row.
        self.clear_edit_fields()
        for tree_item in self.review_tree.get_children():
            self.review_tree.delete(tree_item)
        if reload_store:
            if store_path_override is not None:
                self.review_store.store_path = Path(store_path_override)
            else:
                folder = self.selected_folder.get().strip()
                if folder:
                    self.review_store.store_path = Path(folder) / "manual_review.json"
            if not self.review_store.load():
                self._show_review_load_error()
                return False
        for item in self.review_store.get_all():
            self.review_tree.insert(
                "",
                tk.END,
                values=(
                    Path(item.file_path).name,
                    item.raw_name or "—",
                    item.raw_subject or "—",
                    item.reason,
                ),
                tags=(item.file_path,),
            )
        return True

    def on_review_item_selected(self, _event=None):
        selection = self.review_tree.selection()
        if not selection:
            return
        file_path = self.review_tree.item(selection[0], "tags")[0]
        review_item = next(
            (item for item in self.review_store.get_all() if item.file_path == file_path),
            None,
        )
        if review_item is None:
            return
        if self.current_review_item and self.current_review_item.file_path == file_path:
            return
        self.current_review_item = review_item
        self.file_label.config(text=str(self.review_store.store_path.parent / file_path))
        self.form_type_label.config(text=review_item.form_type or "Unbekannt")
        self.raw_name_label.config(text=review_item.raw_name or "—")
        self.reason_label.config(text=review_item.reason or "—")

        parts = (review_item.canonical_name or review_item.raw_name or "").split(maxsplit=1)
        self.edit_surname.set(parts[0] if parts else "")
        self.edit_name.set(parts[1] if len(parts) > 1 else "")
        self.edit_subject.set(review_item.canonical_subject or review_item.raw_subject or "")
        self.save_student_alias.set(bool(review_item.raw_name))
        self.update_suggestion_buttons(review_item.suggestions)
        self._update_student_choices(review_item)

        self.preview_text.config(state=tk.NORMAL)
        self.preview_text.delete(1.0, tk.END)
        self.preview_text.insert(1.0, review_item.preview_text or "")
        self.preview_text.config(state=tk.DISABLED)

    def _update_student_choices(self, item):
        suggestions = set(item.student_suggestions or [])
        ordered = sorted(
            getattr(self.student_registry, "students", ()),
            key=lambda student: (
                student.full_name not in suggestions,
                student.last_name.casefold(),
                student.first_name.casefold(),
                student.number,
            ),
        )
        self.student_choice_numbers = {
            f"Nr. {student.number} — {student.full_name}": student.number
            for student in ordered
        }
        choices = list(self.student_choice_numbers)
        self.student_combo.config(values=choices)
        selected = next(
            (
                display
                for display, number in self.student_choice_numbers.items()
                if number == item.canonical_student_number
            ),
            "",
        )
        self.selected_student.set(selected)
        if selected:
            # The registry owns field boundaries, including compound surnames.
            self._on_student_choice()

    def _on_student_choice(self, _event=None):
        number = self.student_choice_numbers.get(self.selected_student.get())
        if number is None:
            return
        try:
            student = self.student_registry.select_existing(number)
        except Exception as error:
            self.log_message(f"❌ Schüler konnte nicht ausgewählt werden: {error}")
            return
        self.edit_surname.set(student.last_name)
        self.edit_name.set(student.first_name)

    def on_review_item_double_click(self, _event=None):
        selection = self.review_tree.selection()
        if not selection:
            return
        file_path = self.review_tree.item(selection[0], "tags")[0]
        path = self.review_store.store_path.parent / file_path
        if path.exists():
            FileOpener.open_pdf(path)
        else:
            self.log_message(f"❌ Datei nicht gefunden: {path}")

    def update_suggestion_buttons(self, suggestions):
        for widget in self.suggestions_frame.winfo_children():
            widget.destroy()
        for index, suggestion in enumerate((suggestions or [])[:5]):
            ttk.Button(
                self.suggestions_frame,
                text=suggestion,
                command=lambda value=suggestion: self.edit_subject.set(value),
            ).grid(row=index // 2, column=index % 2, padx=2, pady=2, sticky=tk.W)

    def apply_existing_student(self):
        number = self.student_choice_numbers.get(self.selected_student.get())
        if number is None:
            messagebox.showwarning("Warnung", "Bitte wählen Sie einen Schüler aus.")
            return
        self._apply_review_action(ReviewDecision.USE_EXISTING, number)

    def apply_correction(self):
        """Default unknown-student action: confirm the edited name and add it."""

        self._apply_review_action(ReviewDecision.ADD_NEW)

    def rename_without_student(self):
        if not self.current_review_item:
            messagebox.showwarning("Warnung", "Bitte wählen Sie eine Datei aus.")
            return
        if not messagebox.askyesno(
            "Explizite Bestätigung",
            "PDF umbenennen, ohne den Schüler im Verzeichnis zu speichern?",
        ):
            return
        self._apply_review_action(ReviewDecision.RENAME_ONLY)

    def _apply_review_action(self, decision, student_number=None):
        if not self._ensure_idle():
            return
        if not self.current_review_item:
            messagebox.showwarning("Warnung", "Bitte wählen Sie eine Datei aus.")
            return
        # A displayed queue belongs to its loaded folder, even if the user has
        # since typed a different input folder for the next batch.
        folder = self.review_store.store_path.parent
        subject = self.edit_subject.get().strip()
        if subject and self.subjects and subject not in self.subjects:
            if not messagebox.askyesno(
                "Unbekanntes Fach",
                f"Das Fach '{subject}' ist nicht konfiguriert. Trotzdem verwenden?",
            ):
                return
        try:
            fallback_month = (
                int(self.selected_month.get())
                if self.current_review_item.selected_month is None else None
            )
            fallback_year = (
                int(self.selected_year.get())
                if self.current_review_item.selected_year is None else None
            )
        except (tk.TclError, TypeError, ValueError):
            messagebox.showerror("Fehler", "Monat und Jahr sind ungültig.")
            return
        request = ReviewActionRequest(
            item=self.current_review_item,
            base_folder=Path(folder),
            last_name=self.edit_surname.get(),
            first_name=self.edit_name.get(),
            subject=subject,
            decision=decision,
            student_number=student_number,
            save_student_alias=self.save_student_alias.get(),
            fallback_month=fallback_month,
            fallback_year=fallback_year,
        )
        result = self.review_actions.apply(request)
        icon = "✅" if result.succeeded else "⚠️" if result.status == ReviewActionStatus.PARTIAL else "❌"
        self.log_message(f"{icon} {result.message}")
        if result.registry_saved or result.student_alias_saved:
            self._after_student_change(
                result.student.number if result.student is not None else None
            )
        if result.succeeded:
            if result.subject_alias_saved:
                self.log_message("📚 Fachalias wurde gespeichert.")
            if result.student_alias_saved:
                self.log_message("👤 Namensalias wurde gespeichert.")
            self.stats["success"] += 1
            self.update_statistics()
            self.load_review_queue(reload_store=False)
            self.clear_edit_fields()
        elif result.status == ReviewActionStatus.PARTIAL:
            self.load_review_queue(reload_store=False)
            self.clear_edit_fields()
            messagebox.showwarning("Teilweise ausgeführt", result.message)
        else:
            messagebox.showerror("Fehler", result.message)

    def remove_from_queue(self):
        if not self._ensure_idle():
            return
        if not self.current_review_item:
            messagebox.showwarning("Warnung", "Bitte wählen Sie eine Datei aus.")
            return
        if not messagebox.askyesno(
            "Bestätigung", "Eintrag ohne Umbenennung aus der Liste entfernen?"
        ):
            return
        previous = self.review_store.get_all()
        try:
            removed = self.review_store.remove(self.current_review_item.file_path)
            saved = bool(removed and self.review_store.save())
        except Exception as error:
            self.review_store.items = previous
            messagebox.showerror(
                "Fehler",
                "Warteschlange konnte nicht gespeichert werden: "
                + (str(error) or error.__class__.__name__),
            )
            return
        if not saved:
            self.review_store.items = previous
            messagebox.showerror("Fehler", "Warteschlange konnte nicht gespeichert werden.")
            return
        self.load_review_queue(reload_store=False)
        self.clear_edit_fields()

    def clear_edit_fields(self):
        self.current_review_item = None
        for label in (
            self.file_label,
            self.form_type_label,
            self.raw_name_label,
            self.reason_label,
        ):
            label.config(text="—")
        self.edit_surname.set("")
        self.edit_name.set("")
        self.edit_subject.set("")
        self.selected_student.set("")
        self.student_combo.config(values=[])
        self.student_choice_numbers = {}
        self.preview_text.config(state=tk.NORMAL)
        self.preview_text.delete(1.0, tk.END)
        self.preview_text.config(state=tk.DISABLED)
        for widget in self.suggestions_frame.winfo_children():
            widget.destroy()

    def validate_input(self):
        folder = self.selected_folder.get().strip()
        if not folder:
            messagebox.showerror("Fehler", "Bitte wählen Sie einen Eingabeordner.")
            return False
        path = Path(folder)
        if not path.is_dir():
            messagebox.showerror("Fehler", "Der ausgewählte Ordner existiert nicht.")
            return False
        try:
            month = int(self.selected_month.get())
            year = int(self.selected_year.get())
            BatchSettings(path, month, year, bool(self.dry_run_mode.get()))
        except (tk.TclError, TypeError, ValueError) as error:
            messagebox.showerror("Fehler", f"Ungültiger Monat oder Jahr: {error}")
            return False
        return True

    def start_processing(self):
        if getattr(self, "_student_task_running", False):
            self._ensure_idle()
            return
        if not self.validate_input():
            return
        if self.processing:
            messagebox.showwarning("Warnung", "Verarbeitung läuft bereits.")
            return

        # All Tk variables are read exactly once on the main thread.  The
        # immutable settings object is the only period/folder input to worker.
        settings = BatchSettings(
            Path(self.selected_folder.get().strip()),
            int(self.selected_month.get()),
            int(self.selected_year.get()),
            bool(self.dry_run_mode.get()),
        )
        self.review_store.store_path = settings.folder / "manual_review.json"
        loaded = self.review_store.load()
        # Clear a selection from a previous folder even if starting the worker
        # fails; the visible rows must always belong to the loaded store.
        self.load_review_queue(reload_store=False)
        if not loaded:
            self._show_review_load_error()
            return
        self.stats = self._empty_stats()
        self.update_statistics()
        self.progress_var.set(0)
        self.processing = True
        self._active_batch_folder = settings.folder
        self._batch_queue_failed = False
        self.stop_event.clear()
        self._set_batch_controls(True)
        try:
            self.processing_thread = threading.Thread(
                target=self.batch_runner.run,
                args=(settings, self.stop_event, self.worker_events.put),
                daemon=True,
            )
            self.processing_thread.start()
        except Exception as error:
            self.processing = False
            self._active_batch_folder = None
            self._set_batch_controls(False)
            self.progress_label.config(text="Verarbeitung konnte nicht gestartet werden")
            detail = str(error) or error.__class__.__name__
            self.log_message(f"❌ Worker konnte nicht gestartet werden: {detail}")
            messagebox.showerror(
                "Verarbeitung nicht gestartet",
                f"Hintergrundprozess konnte nicht gestartet werden:\n{detail}",
            )

    def stop_processing(self):
        if not self.processing:
            return
        self.stop_event.set()
        self.stop_button.config(state=tk.DISABLED)
        self.progress_label.config(text="Stoppen nach aktueller Datei …")
        self.log_message("⏹️ Verarbeitung wird zwischen Dateien gestoppt.")

    def _set_batch_controls(self, running: bool):
        registry_busy = getattr(self, "_student_task_running", False)
        self.start_button.config(state=tk.DISABLED if running or registry_busy else tk.NORMAL)
        self.stop_button.config(state=tk.NORMAL if running else tk.DISABLED)
        self.folder_entry.config(state=tk.DISABLED if running else tk.NORMAL)
        self.browse_button.config(state=tk.DISABLED if running else tk.NORMAL)
        self.dry_run_check.config(state=tk.DISABLED if running else tk.NORMAL)
        self.month_combo.config(state=tk.DISABLED if running else "readonly")
        self.year_entry.config(state=tk.DISABLED if running else tk.NORMAL)
        for button in self.student_mutation_buttons:
            button.config(state=tk.DISABLED if running or registry_busy else tk.NORMAL)

    def _on_close(self):
        if getattr(self, "_student_task_running", False):
            self._ensure_idle()
            return
        if not self.processing:
            self.root.destroy()
            return
        if self._close_requested:
            return
        if not messagebox.askyesno(
            "Verarbeitung läuft",
            "Nach der aktuellen Datei sicher stoppen und das Fenster schließen?",
        ):
            return
        self._close_requested = True
        self.stop_event.set()
        self.stop_button.config(state=tk.DISABLED)
        self.progress_label.config(text="Sicheres Beenden nach aktueller Datei …")
        self.log_message("⏹️ Sicheres Beenden angefordert.")
        self.root.after(50, self._finish_close)

    def _finish_close(self):
        if self.processing:
            self.root.after(50, self._finish_close)
            return
        self.root.destroy()

    def _drain_worker_events(self):
        try:
            while True:
                event = self.worker_events.get_nowait()
                self._handle_worker_event(event)
        except queue.Empty:
            pass
        finally:
            self.root.after(50, self._drain_worker_events)

    def _handle_worker_event(self, event):
        kind = event.kind
        if kind == WorkerEventKind.STARTED:
            settings, total = event.payload
            self._batch_queue_failed = False
            self.stats["total"] = total
            self.update_statistics()
            self.log_message("=" * 60)
            self.log_message(f"📂 Eingabeordner: {settings.folder}")
            self.log_message(f"📅 Zeitraum: {settings.month:02d}/{settings.year}")
            if settings.dry_run:
                self.log_message("🧪 MODUS: Testlauf (keine Änderungen)")
            self.log_message(f"📊 Gefunden: {total} PDF-Datei(en)")
        elif kind == WorkerEventKind.PROGRESS:
            index, total, path = event.payload
            self.progress_var.set(((index - 1) / total) * 100 if total else 0)
            self.progress_label.config(text=f"Verarbeite Datei {index} von {total}")
            self.log_message(f"📄 Verarbeite: {path.name}")
        elif kind == WorkerEventKind.RESULT:
            self._present_processing_result(event.payload)
        elif kind == WorkerEventKind.DOCUMENT_ERROR:
            path, error = event.payload
            self.stats["error"] += 1
            self.update_statistics()
            self.log_message(f"❌ Fehler bei {path.name}: {error}")
        elif kind == WorkerEventKind.QUEUE_SAVED:
            count, path = event.payload
            self.log_message(f"📋 {count} Eintrag/Einträge gespeichert: {path}")
        elif kind == WorkerEventKind.QUEUE_ERROR:
            self._batch_queue_failed = True
            self.log_message(f"❌ Warteschlangenfehler: {event.payload}")
        elif kind == WorkerEventKind.COMPLETED:
            if event.payload:
                self.progress_label.config(text="Verarbeitung gestoppt")
                self.log_message("⏹️ Verarbeitung wurde sauber gestoppt.")
            else:
                self.progress_var.set(100)
                self.progress_label.config(text="Verarbeitung abgeschlossen")
                if not self._close_requested:
                    self.show_final_report()
                else:
                    self.log_message("Abschlussbericht wegen Fenster-Schließen unterdrückt.")
            if self._batch_queue_failed:
                self.load_review_queue(reload_store=False)
            else:
                queue_path = (
                    self._active_batch_folder / "manual_review.json"
                    if self._active_batch_folder is not None
                    else None
                )
                self.load_review_queue(
                    reload_store=True,
                    store_path_override=queue_path,
                    allow_during_processing=True,
                )
        elif kind == WorkerEventKind.FATAL:
            self.stats["error"] += 1
            self.update_statistics()
            self.progress_label.config(text="Verarbeitung fehlgeschlagen")
            self.load_review_queue(reload_store=False)
            self.log_message(f"❌ Schwerwiegender Fehler: {event.payload}")
            if not self._close_requested:
                messagebox.showerror("Fehler", f"Verarbeitung fehlgeschlagen:\n{event.payload}")
        elif kind == WorkerEventKind.FINISHED:
            self.processing = False
            self._active_batch_folder = None
            self._set_batch_controls(False)

    def _present_processing_result(self, result):
        if result.error:
            self.stats["error"] += 1
            self.log_message(f"   ❌ {result.error}")
        elif result.planned_action == PlannedAction.MANUAL_REVIEW:
            self.stats["manual"] += 1
            prefix = "[Simulation] " if result.dry_run else ""
            self.log_message(f"   ⚠️ {prefix}{result.review_item.reason}")
            self.log_message(f"   📁 Ziel: {result.planned_path}")
        elif result.warnings:
            self.stats["warning"] += 1
            self.log_message(f"   ✅ {result.pdf_path.name} → {result.planned_name}")
            for warning in result.warnings:
                self.log_message(f"   ⚠️ {warning}")
        else:
            self.stats["success"] += 1
            prefix = "[Simulation] " if result.dry_run else ""
            self.log_message(f"   ✅ {prefix}{result.pdf_path.name} → {result.planned_name}")
        self.update_statistics()

    def show_final_report(self):
        self.log_message("=" * 60)
        self.log_message("📊 ABSCHLUSSBERICHT")
        self.log_message(f"✅ Erfolgreich: {self.stats['success']}")
        self.log_message(f"⚠️ Mit Warnungen: {self.stats['warning']}")
        self.log_message(f"📋 Manuelle Prüfung: {self.stats['manual']}")
        self.log_message(f"❌ Fehler: {self.stats['error']}")
        self.log_message("=" * 60)
        if self.stats["error"]:
            messagebox.showwarning("Mit Fehlern abgeschlossen", "Siehe Log für Details.")
        elif self.stats["manual"]:
            messagebox.showinfo(
                "Teilweise erfolgreich",
                f"{self.stats['manual']} Datei(en) benötigen manuelle Prüfung.",
            )
        else:
            messagebox.showinfo("Erfolg", "Verarbeitung abgeschlossen.")

    def run(self):
        self.log_message("🎯 PDF Datei Umbenenner bereit!")
        self.log_message("📌 Wählen Sie Ordner, Monat und Jahr.")
        self.update_statistics()
        self.root.mainloop()


def main():
    try:
        PDFRenamerGUI().run()
    except Exception as error:
        # start_app.bat redirects stderr to its diagnostic log.
        import traceback

        traceback.print_exc()
        messagebox.showerror(
            "Kritischer Fehler",
            f"Die Anwendung konnte nicht gestartet werden:\n{error}",
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
