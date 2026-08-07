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
from pdf_extractor import PDFExtractor
from processing_service import DocumentProcessingService, PlannedAction
from review_actions import (
    ReviewActionRequest,
    ReviewActionService,
    ReviewActionStatus,
    ReviewDecision,
)
from review_queue import ReviewStore
from student_registry import StudentRegistry
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
        self.stop_event = threading.Event()
        self.worker_events: queue.Queue = queue.Queue()

        project_folder = Path(__file__).parent
        self.extractor = PDFExtractor()
        self.parser = FormParser()
        self.validator = Validator()
        self.renamer = FileRenamer()
        self.review_store = ReviewStore()
        self.student_registry = StudentRegistry(project_folder / "Schuler.csv")
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
        self.root.after(50, self._drain_worker_events)

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

    def setup_processing_tab(self):
        frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(frame, text="Verarbeitung")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(4, weight=1)

        folder_frame = ttk.LabelFrame(frame, text="Ordner-Auswahl", padding="10")
        folder_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        folder_frame.columnconfigure(1, weight=1)
        ttk.Label(folder_frame, text="Eingabeordner:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(folder_frame, textvariable=self.selected_folder, width=50).grid(
            row=0, column=1, padx=10, sticky=(tk.W, tk.E)
        )
        ttk.Button(folder_frame, text="Durchsuchen...", command=self.browse_folder).grid(
            row=0, column=2
        )

        settings = ttk.LabelFrame(frame, text="Einstellungen", padding="10")
        settings.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        ttk.Checkbutton(
            settings,
            text="Testmodus (keine Änderungen)",
            variable=self.dry_run_mode,
        ).grid(row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 8))
        ttk.Label(settings, text="Monat:").grid(row=1, column=0, sticky=tk.W)
        ttk.Combobox(
            settings,
            textvariable=self.selected_month,
            values=list(range(1, 13)),
            state="readonly",
            width=10,
        ).grid(row=1, column=1, sticky=tk.W, padx=(8, 25))
        ttk.Label(settings, text="Jahr:").grid(row=1, column=2, sticky=tk.W)
        ttk.Entry(settings, textvariable=self.selected_year, width=10).grid(
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

    @staticmethod
    def _detail_row(parent, row: int, title: str, wrap: int = 280):
        ttk.Label(parent, text=title, font=("Arial", 9, "bold")).grid(
            row=row, column=0, sticky=tk.NW, pady=(0, 3)
        )
        label = ttk.Label(parent, text="—", wraplength=wrap)
        label.grid(row=row, column=1, sticky=tk.W, pady=(0, 3))
        return label

    def browse_folder(self):
        folder = filedialog.askdirectory(title="Wählen Sie den Ordner mit PDF-Dateien")
        if not folder:
            return
        self.selected_folder.set(folder)
        self.review_store.store_path = Path(folder) / "manual_review.json"
        self.review_store.load()
        self.load_review_queue(reload_store=False)
        self.log_message(f"Ordner ausgewählt: {folder}")

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

    def load_review_queue(self, reload_store=True):
        if reload_store:
            folder = self.selected_folder.get().strip()
            if folder:
                self.review_store.store_path = Path(folder) / "manual_review.json"
            self.review_store.load()
        for tree_item in self.review_tree.get_children():
            self.review_tree.delete(tree_item)
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
        self.file_label.config(text=Path(file_path).name)
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
        folder = self.selected_folder.get().strip()
        if not selection or not folder:
            return
        path = Path(folder) / self.review_tree.item(selection[0], "tags")[0]
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
        if self.processing:
            messagebox.showwarning("Warnung", "Bitte warten Sie bis zum Batch-Ende.")
            return
        if not self.current_review_item:
            messagebox.showwarning("Warnung", "Bitte wählen Sie eine Datei aus.")
            return
        folder = self.selected_folder.get().strip()
        if not folder:
            messagebox.showerror("Fehler", "Bitte wählen Sie einen Eingabeordner.")
            return
        subject = self.edit_subject.get().strip()
        if subject and self.subjects and subject not in self.subjects:
            if not messagebox.askyesno(
                "Unbekanntes Fach",
                f"Das Fach '{subject}' ist nicht konfiguriert. Trotzdem verwenden?",
            ):
                return
        try:
            fallback_month = int(self.selected_month.get())
            fallback_year = int(self.selected_year.get())
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
        if result.succeeded:
            if result.subject_alias_saved:
                self.log_message("📚 Fachalias wurde gespeichert.")
            if result.student_alias_saved:
                self.log_message("👤 Namensalias wurde gespeichert.")
            self.stats["success"] += 1
            self.update_statistics()
            self.load_review_queue(reload_store=True)
            self.clear_edit_fields()
        elif result.status == ReviewActionStatus.PARTIAL:
            messagebox.showwarning("Teilweise ausgeführt", result.message)
        else:
            messagebox.showerror("Fehler", result.message)

    def remove_from_queue(self):
        if not self.current_review_item:
            messagebox.showwarning("Warnung", "Bitte wählen Sie eine Datei aus.")
            return
        if not messagebox.askyesno(
            "Bestätigung", "Eintrag ohne Umbenennung aus der Liste entfernen?"
        ):
            return
        previous = self.review_store.get_all()
        removed = self.review_store.remove(self.current_review_item.file_path)
        if not removed or not self.review_store.save():
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
        self.review_store.load()
        self.stats = self._empty_stats()
        self.update_statistics()
        self.progress_var.set(0)
        self.processing = True
        self.stop_event.clear()
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.processing_thread = threading.Thread(
            target=self.batch_runner.run,
            args=(settings, self.stop_event, self.worker_events.put),
            daemon=True,
        )
        self.processing_thread.start()

    def stop_processing(self):
        if not self.processing:
            return
        self.stop_event.set()
        self.stop_button.config(state=tk.DISABLED)
        self.progress_label.config(text="Stoppen nach aktueller Datei …")
        self.log_message("⏹️ Verarbeitung wird zwischen Dateien gestoppt.")

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
            self.log_message(f"❌ Warteschlangenfehler: {event.payload}")
        elif kind == WorkerEventKind.COMPLETED:
            if event.payload:
                self.progress_label.config(text="Verarbeitung gestoppt")
                self.log_message("⏹️ Verarbeitung wurde sauber gestoppt.")
            else:
                self.progress_var.set(100)
                self.progress_label.config(text="Verarbeitung abgeschlossen")
                self.show_final_report()
            self.load_review_queue(reload_store=True)
        elif kind == WorkerEventKind.FATAL:
            self.stats["error"] += 1
            self.update_statistics()
            self.log_message(f"❌ Schwerwiegender Fehler: {event.payload}")
            messagebox.showerror("Fehler", f"Verarbeitung fehlgeschlagen:\n{event.payload}")
        elif kind == WorkerEventKind.FINISHED:
            self.processing = False
            self.start_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)

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
        messagebox.showerror(
            "Kritischer Fehler",
            f"Die Anwendung konnte nicht gestartet werden:\n{error}",
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
