"""
Grafische Benutzeroberfläche für PDF Datei Umbenenner
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
from pathlib import Path
import sys
import time
import json
from datetime import datetime

from pdf_extractor import PDFExtractor
from text_parser import TextParser
from validator import Validator, ValidationLevel
from file_renamer import FileRenamer
from review_queue import ReviewStore, ReviewItem
from alias_manager import AliasManager
from file_opener import FileOpener


class PDFRenamerGUI:
    """Grafische Benutzeroberfläche für den PDF Umbenenner"""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PDF Datei Umbenenner - Automatisches Umbenennen")
        self.root.geometry("1200x700")
        self.root.resizable(True, True)
        
        # Variablen
        self.selected_folder = tk.StringVar()
        self.dry_run_mode = tk.BooleanVar(value=False)
        self.selected_month = tk.IntVar(value=1)  # По умолчанию январь
        self.progress_var = tk.DoubleVar()
        self.processing = False
        
        # Komponenten
        self.extractor = PDFExtractor()
        self.parser = TextParser()
        self.validator = Validator()
        self.renamer = FileRenamer()
        self.review_store = ReviewStore()
        self.alias_manager = AliasManager()  # Менеджер алиасов для автообучения
        
        # Загрузить список дисциплин из конфига
        self.subjects = []
        self.load_subjects_config()
        
        # Statistik
        self.stats = {
            'total': 0,
            'success': 0,
            'warning': 0,
            'manual': 0,
            'error': 0
        }
        
        self.setup_ui()
        
    def load_subjects_config(self):
        """Загружает список дисциплин из subjects_config.json"""
        config_path = Path(__file__).parent / "subjects_config.json"
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                self.subjects = config.get('subjects', [])
        except (FileNotFoundError, json.JSONDecodeError) as e:
            self.subjects = []
            print(f"Warnung: Konfigurationsdatei konnte nicht geladen werden: {e}")
        
    def setup_ui(self):
        """Erstellt die Benutzeroberfläche"""
        
        # Hauptframe
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Konfiguriere Grid-Gewichte
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)
        
        # Titel
        title_label = ttk.Label(main_frame, text="PDF Datei Umbenenner", 
                               font=("Arial", 16, "bold"))
        title_label.grid(row=0, column=0, pady=(0, 20))
        
        # Notebook (Tabs)
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Erstelle Tabs
        self.setup_processing_tab()
        self.setup_review_tab()
    
    def setup_processing_tab(self):
        """Erstellt die Verarbeitungs-Tab"""
        processing_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(processing_frame, text="Verarbeitung")
        
        processing_frame.columnconfigure(1, weight=1)
        processing_frame.rowconfigure(4, weight=1)
        
        # Ordner-Auswahl Sektion
        folder_frame = ttk.LabelFrame(processing_frame, text="Ordner-Auswahl", padding="10")
        folder_frame.grid(row=0, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))
        folder_frame.columnconfigure(1, weight=1)
        
        ttk.Label(folder_frame, text="Eingabeordner:").grid(row=0, column=0, sticky=tk.W)
        
        folder_entry = ttk.Entry(folder_frame, textvariable=self.selected_folder, width=50)
        folder_entry.grid(row=0, column=1, padx=(10, 10), sticky=(tk.W, tk.E))
        
        browse_button = ttk.Button(folder_frame, text="Durchsuchen...", 
                                  command=self.browse_folder)
        browse_button.grid(row=0, column=2)
        
        # Einstellungen Sektion
        settings_frame = ttk.LabelFrame(processing_frame, text="Einstellungen", padding="10")
        settings_frame.grid(row=1, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))
        settings_frame.columnconfigure(1, weight=1)
        
        dry_run_checkbox = ttk.Checkbutton(settings_frame, text="Testmodus (keine Änderungen)", 
                                          variable=self.dry_run_mode)
        dry_run_checkbox.grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))
        
        # Выбор месяца
        ttk.Label(settings_frame, text="Monat:").grid(row=1, column=0, sticky=tk.W, padx=(0, 10))
        month_combo = ttk.Combobox(settings_frame, textvariable=self.selected_month, 
                                   values=list(range(1, 13)), state="readonly", width=10)
        month_combo.grid(row=1, column=1, sticky=tk.W)
        
        # Fortschritt Sektion
        progress_frame = ttk.LabelFrame(processing_frame, text="Fortschritt", padding="10")
        progress_frame.grid(row=2, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))
        progress_frame.columnconfigure(0, weight=1)
        
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, 
                                           maximum=100, length=400)
        self.progress_bar.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 5))
        
        self.progress_label = ttk.Label(progress_frame, text="Bereit zum Starten")
        self.progress_label.grid(row=1, column=0)
        
        # Buttons
        button_frame = ttk.Frame(processing_frame)
        button_frame.grid(row=3, column=0, columnspan=3, pady=10)
        
        self.start_button = ttk.Button(button_frame, text="Verarbeitung starten", 
                                      command=self.start_processing, style="Accent.TButton")
        self.start_button.pack(side=tk.LEFT, padx=(0, 10))
        
        self.stop_button = ttk.Button(button_frame, text="Stoppen", 
                                     command=self.stop_processing, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=(0, 10))
        
        clear_button = ttk.Button(button_frame, text="Log löschen", 
                                 command=self.clear_log)
        clear_button.pack(side=tk.LEFT)
        
        # Log Sektion
        log_frame = ttk.LabelFrame(processing_frame, text="Verarbeitungslog", padding="10")
        log_frame.grid(row=4, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        processing_frame.rowconfigure(4, weight=1)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, width=80)
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Statistik Sektion
        stats_frame = ttk.LabelFrame(processing_frame, text="Statistik", padding="10")
        stats_frame.grid(row=5, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.stats_text = tk.Text(stats_frame, height=6, width=80, state=tk.DISABLED)
        self.stats_text.grid(row=0, column=0, sticky=(tk.W, tk.E))
    
    def setup_review_tab(self):
        """Erstellt die Prüfungs-Tab für manuelle Korrekturen"""
        review_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(review_frame, text="Prüfung")
        
        # Konfiguriere Grid-Gewichte
        review_frame.columnconfigure(0, weight=3)
        review_frame.columnconfigure(1, weight=2)
        review_frame.rowconfigure(0, weight=1)
        
        # Linke Seite: Treeview mit Problemdateien
        left_frame = ttk.LabelFrame(review_frame, text="Dateien zur Prüfung", padding="10")
        left_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(0, 5))
        left_frame.columnconfigure(0, weight=1)
        left_frame.rowconfigure(0, weight=1)
        
        # Treeview mit Scrollbars
        tree_scroll_y = ttk.Scrollbar(left_frame, orient=tk.VERTICAL)
        tree_scroll_x = ttk.Scrollbar(left_frame, orient=tk.HORIZONTAL)
        
        self.review_tree = ttk.Treeview(
            left_frame,
            columns=("filename", "name", "subject", "reason"),
            show="headings",
            yscrollcommand=tree_scroll_y.set,
            xscrollcommand=tree_scroll_x.set
        )
        
        tree_scroll_y.config(command=self.review_tree.yview)
        tree_scroll_x.config(command=self.review_tree.xview)
        
        # Spalten konfigurieren
        self.review_tree.heading("filename", text="Dateiname")
        self.review_tree.heading("name", text="Gefundener Name")
        self.review_tree.heading("subject", text="Gefundenes Fach")
        self.review_tree.heading("reason", text="Grund")
        
        self.review_tree.column("filename", width=200, minwidth=150)
        self.review_tree.column("name", width=150, minwidth=100)
        self.review_tree.column("subject", width=120, minwidth=80)
        self.review_tree.column("reason", width=250, minwidth=150)
        
        # Grid-Layout für Treeview
        self.review_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        tree_scroll_y.grid(row=0, column=1, sticky=(tk.N, tk.S))
        tree_scroll_x.grid(row=1, column=0, sticky=(tk.W, tk.E))
        
        # Button zum Aktualisieren der Liste
        refresh_button = ttk.Button(left_frame, text="Liste aktualisieren", 
                                    command=self.load_review_queue)
        refresh_button.grid(row=2, column=0, columnspan=2, pady=(10, 0), sticky=tk.W)
        
        # Bind Selection Event
        self.review_tree.bind('<<TreeviewSelect>>', self.on_review_item_selected)
        
        # Bind Double-Click Event для открытия PDF
        self.review_tree.bind('<Double-Button-1>', self.on_review_item_double_click)
        
        # Rechte Seite: Bearbeitungspanel
        right_frame = ttk.LabelFrame(review_frame, text="Korrektur", padding="10")
        right_frame.grid(row=0, column=1, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(5, 0))
        
        # Variablen für Eingabefelder
        self.edit_surname = tk.StringVar()
        self.edit_name = tk.StringVar()
        self.edit_subject = tk.StringVar()
        self.current_review_item = None
        
        # Dateiname Anzeige
        ttk.Label(right_frame, text="Datei:", font=("Arial", 9, "bold")).grid(row=0, column=0, sticky=tk.W, pady=(0, 5))
        self.file_label = ttk.Label(right_frame, text="", wraplength=300, foreground="blue")
        self.file_label.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(0, 15))
        
        # Nachname
        ttk.Label(right_frame, text="Nachname:").grid(row=2, column=0, sticky=tk.W, pady=(5, 0))
        surname_entry = ttk.Entry(right_frame, textvariable=self.edit_surname, width=30)
        surname_entry.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # Vorname
        ttk.Label(right_frame, text="Vorname:").grid(row=4, column=0, sticky=tk.W, pady=(5, 0))
        name_entry = ttk.Entry(right_frame, textvariable=self.edit_name, width=30)
        name_entry.grid(row=5, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # Fach
        ttk.Label(right_frame, text="Fach:").grid(row=6, column=0, sticky=tk.W, pady=(5, 0))
        subject_combo = ttk.Combobox(right_frame, textvariable=self.edit_subject, 
                                    values=self.subjects, state="normal", width=28)
        subject_combo.grid(row=7, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # Schnellvorschläge für Fach
        ttk.Label(right_frame, text="Schnellauswahl:", font=("Arial", 8)).grid(row=8, column=0, sticky=tk.W, pady=(5, 0))
        
        # Frame für Vorschlagsbuttons
        self.suggestions_frame = ttk.Frame(right_frame)
        self.suggestions_frame.grid(row=9, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(5, 15))
        
        # Aktionsbuttons
        ttk.Separator(right_frame, orient=tk.HORIZONTAL).grid(row=10, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(10, 10))
        
        apply_button = ttk.Button(right_frame, text="Anwenden", 
                                 command=self.apply_correction, style="Accent.TButton")
        apply_button.grid(row=11, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 5))
        
        apply_all_button = ttk.Button(right_frame, text="Auf alle mit gleichem Grund anwenden", 
                                     command=self.apply_to_all)
        apply_all_button.grid(row=12, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 5))
        
        delete_button = ttk.Button(right_frame, text="Aus Liste entfernen (ohne Umbenennung)", 
                                  command=self.remove_from_queue)
        delete_button.grid(row=13, column=0, columnspan=2, sticky=(tk.W, tk.E))
        
        # Platzhalter für Vorschautext (optional für spätere Erweiterungen)
        preview_frame = ttk.LabelFrame(right_frame, text="Textauszug", padding="5")
        preview_frame.grid(row=14, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(15, 0))
        right_frame.rowconfigure(14, weight=1)
        
        self.preview_text = tk.Text(preview_frame, height=8, width=40, wrap=tk.WORD, state=tk.DISABLED)
        self.preview_text.pack(fill=tk.BOTH, expand=True)
        
        preview_scroll = ttk.Scrollbar(preview_frame, command=self.preview_text.yview)
        self.preview_text.config(yscrollcommand=preview_scroll.set)
        preview_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
    def browse_folder(self):
        """Öffnet den Ordner-Auswahl Dialog"""
        folder = filedialog.askdirectory(title="Wählen Sie den Ordner mit PDF-Dateien")
        if folder:
            self.selected_folder.set(folder)
            # Привязываем ReviewStore к выбранной папке
            self.review_store.store_path = Path(folder) / "manual_review.json"
            self.log_message(f"Ordner ausgewählt: {folder}")
    
    def log_message(self, message):
        """Fügt eine Nachricht zum Log hinzu"""
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()
    
    def clear_log(self):
        """Löscht das Log"""
        self.log_text.delete(1.0, tk.END)
        self.update_statistics()
    
    def update_statistics(self):
        """Aktualisiert die Statistik-Anzeige"""
        self.stats_text.config(state=tk.NORMAL)
        self.stats_text.delete(1.0, tk.END)
        
        stats_text = f"""Verarbeitungsstatistik:
├─ Insgesamt verarbeitet:         {self.stats['total']}
├─ Erfolgreich umbenannt:         {self.stats['success']}
├─ Mit Warnungen umbenannt:       {self.stats['warning']}
├─ Manuelle Prüfung erforderlich: {self.stats['manual']}
└─ Fehler:                        {self.stats['error']}"""
        
        self.stats_text.insert(1.0, stats_text)
        self.stats_text.config(state=tk.DISABLED)
    
    def load_review_queue(self):
        """Загружает очередь проверки из файла и отображает в Treeview"""
        # Очистить текущее содержимое
        for item in self.review_tree.get_children():
            self.review_tree.delete(item)
        
        # Загрузить данные из файла
        self.review_store.load()
        items = self.review_store.get_all()
        
        if not items:
            messagebox.showinfo("Information", "Keine Dateien zur Prüfung vorhanden.")
            return
        
        # Заполнить Treeview
        for item in items:
            filename = Path(item.file_path).name
            name_display = item.raw_name if item.raw_name else "—"
            subject_display = item.raw_subject if item.raw_subject else "—"
            
            self.review_tree.insert("", tk.END, values=(
                filename,
                name_display,
                subject_display,
                item.reason
            ), tags=(item.file_path,))
        
        messagebox.showinfo("Erfolg", f"{len(items)} Datei(en) zur Prüfung geladen.")
    
    def on_review_item_selected(self, event):
        """Обработчик выбора элемента в Treeview"""
        selection = self.review_tree.selection()
        if not selection:
            return
        
        item_id = selection[0]
        values = self.review_tree.item(item_id, "values")
        file_path = self.review_tree.item(item_id, "tags")[0]
        
        # Найти соответствующий ReviewItem
        items = self.review_store.get_all()
        review_item = None
        for item in items:
            if item.file_path == file_path:
                review_item = item
                break
        
        if not review_item:
            return
        
        # Проверка: если это тот же самый элемент, не обновляем поля
        # (чтобы не потерять введённые пользователем данные)
        if self.current_review_item and self.current_review_item.file_path == review_item.file_path:
            return
        
        self.current_review_item = review_item
        
        # Обновить поля редактирования
        self.file_label.config(text=Path(file_path).name)
        
        # Разделить имя на фамилию и имя, если возможно
        if review_item.raw_name:
            name_parts = review_item.raw_name.strip().split(maxsplit=1)
            if len(name_parts) >= 2:
                self.edit_surname.set(name_parts[0])
                self.edit_name.set(name_parts[1])
            elif len(name_parts) == 1:
                self.edit_surname.set(name_parts[0])
                self.edit_name.set("")
        else:
            self.edit_surname.set("")
            self.edit_name.set("")
        
        self.edit_subject.set(review_item.raw_subject if review_item.raw_subject else "")
        
        # Обновить кнопки подсказок
        self.update_suggestion_buttons(review_item.suggestions)
        
        # Показать превью текста
        self.preview_text.config(state=tk.NORMAL)
        self.preview_text.delete(1.0, tk.END)
        self.preview_text.insert(1.0, review_item.preview_text)
        self.preview_text.config(state=tk.DISABLED)
    
    def on_review_item_double_click(self, event):
        """Обработчик двойного клика по элементу в Treeview - открывает PDF файл"""
        selection = self.review_tree.selection()
        if not selection:
            return
        
        item_id = selection[0]
        file_path = self.review_tree.item(item_id, "tags")[0]
        
        # Проверить, что выбрана папка
        if not self.selected_folder.get():
            return
        
        # Сформировать абсолютный путь к файлу
        base_folder = Path(self.selected_folder.get())
        full_path = base_folder / file_path
        
        # Открыть файл
        if full_path.exists():
            FileOpener.open_pdf(full_path)
        else:
            self.log_message(f"❌ Datei nicht gefunden: {full_path}")
    
    def update_suggestion_buttons(self, suggestions):
        """Обновляет кнопки быстрых подсказок"""
        # Удалить старые кнопки
        for widget in self.suggestions_frame.winfo_children():
            widget.destroy()
        
        # Создать новые кнопки для подсказок
        if suggestions:
            for idx, suggestion in enumerate(suggestions[:5]):  # Максимум 5 подсказок
                btn = ttk.Button(
                    self.suggestions_frame,
                    text=suggestion,
                    command=lambda s=suggestion: self.edit_subject.set(s),
                    width=15
                )
                btn.grid(row=idx // 2, column=idx % 2, padx=2, pady=2, sticky=tk.W)
    
    def apply_correction(self):
        """Применяет исправление к выбранному файлу"""
        if not self.current_review_item:
            messagebox.showwarning("Warnung", "Bitte wählen Sie eine Datei aus der Liste.")
            return
        
        # Получить исправленные данные
        surname = self.edit_surname.get().strip()
        name = self.edit_name.get().strip()
        subject = self.edit_subject.get().strip()
        
        if not surname or not name or not subject:
            messagebox.showerror("Fehler", "Bitte füllen Sie alle Felder aus.")
            return
        
        # Проверить, что дисциплина валидна
        if subject not in self.subjects:
            response = messagebox.askyesno(
                "Warnung",
                f"Das Fach '{subject}' ist nicht in der Liste.\nTrotzdem fortfahren?"
            )
            if not response:
                return
        
        # Проверить, что выбрана папка
        if not self.selected_folder.get():
            messagebox.showerror("Fehler", "Bitte wählen Sie zuerst einen Eingabeordner!")
            return
        
        # Сформировать абсолютный путь к файлу
        # file_path в ReviewItem хранится как "manual_check/filename.pdf"
        base_folder = Path(self.selected_folder.get())
        file_path = base_folder / self.current_review_item.file_path
        
        if not file_path.exists():
            messagebox.showerror("Fehler", f"Datei nicht gefunden:\n{file_path}")
            return
        
        # Получаем месяц и текущий год
        month = self.selected_month.get()
        year = datetime.now().year
        
        # Генерируем новое имя файла
        new_filename = self.renamer.generate_new_filename(surname, name, subject, month, year)
        # Целевая папка — корневая (base_folder), а не manual_check
        target_path = base_folder / new_filename
        
        # Обработка коллизий: если файл существует, добавляем счетчик
        if target_path.exists():
            stem = target_path.stem
            suffix = target_path.suffix
            counter = 1
            while target_path.exists():
                new_filename = f"{stem}_{counter}{suffix}"
                target_path = base_folder / new_filename
                counter += 1
        
        # Переместить и переименовать файл из manual_check в корневую папку
        try:
            import shutil
            shutil.move(str(file_path), str(target_path))
            
            # Автообучение: сохраняем пару raw_subject → corrected_subject
            raw_subject = self.current_review_item.raw_subject
            if raw_subject and raw_subject != subject:
                # Добавляем алиас только если названия различаются
                if self.alias_manager.add_alias(raw_subject, subject):
                    self.alias_manager.save()
                    self.log_message(f"   📚 Alias gespeichert: '{raw_subject}' → '{subject}'")
            
            # Логирование успешного перемещения
            timestamp = time.strftime("%H:%M:%S")
            log_message = (f"[{timestamp}] ✅ Korrektur angewendet:\n"
                          f"  Datei: {file_path.name}\n"
                          f"  → Neuer Name: {new_filename}\n"
                          f"  → Verschoben nach: {base_folder.name}/")
            self.log_message(log_message)
            
            # Удалить из очереди
            self.review_store.remove(self.current_review_item.file_path)
            self.review_store.save()
            
            # Обновить статистику
            self.stats['success'] += 1
            self.update_statistics()
            
            # Обновить отображение
            self.load_review_queue()
            
            # Очистить поля
            self.clear_edit_fields()
            
        except Exception as e:
            # Только логируем ошибку, без всплывающего окна
            self.log_message(f"❌ Fehler beim Umbenennen/Verschieben: {str(e)}")
    
    def apply_to_all(self):
        """Применяет исправление ко всем файлам с таким же основанием"""
        if not self.current_review_item:
            messagebox.showwarning("Warnung", "Bitte wählen Sie eine Datei aus der Liste.")
            return
        
        # Получить исправленные данные
        surname = self.edit_surname.get().strip()
        name = self.edit_name.get().strip()
        subject = self.edit_subject.get().strip()
        
        if not surname or not name or not subject:
            messagebox.showerror("Fehler", "Bitte füllen Sie alle Felder aus.")
            return
        
        # Найти все файлы с таким же raw_subject
        current_subject = self.current_review_item.raw_subject
        items_to_process = [
            item for item in self.review_store.get_all()
            if item.raw_subject == current_subject
        ]
        
        if len(items_to_process) <= 1:
            messagebox.showinfo("Information", "Keine weiteren Dateien mit gleichem Fach gefunden.")
            return
        
        # Подтверждение
        response = messagebox.askyesno(
            "Bestätigung",
            f"Möchten Sie {len(items_to_process)} Datei(en) mit dem Fach '{subject}' umbenennen?"
        )
        
        if not response:
            return
        
        # Проверить, что выбрана папка
        if not self.selected_folder.get():
            messagebox.showerror("Fehler", "Bitte wählen Sie zuerst einen Eingabeordner!")
            return
        
        # Применить ко всем
        base_folder = Path(self.selected_folder.get())
        success_count = 0
        error_count = 0
        
        import shutil
        
        for item in items_to_process:
            # Сформировать абсолютный путь к файлу в manual_check
            file_path = base_folder / item.file_path
            
            if not file_path.exists():
                error_count += 1
                self.log_message(f"❌ Datei nicht gefunden: {file_path.name}")
                continue
            
            # Использовать имя из текущего элемента, если оно есть
            if item.raw_name:
                name_parts = item.raw_name.strip().split(maxsplit=1)
                item_surname = name_parts[0] if len(name_parts) >= 1 else surname
                item_name = name_parts[1] if len(name_parts) >= 2 else name
            else:
                item_surname = surname
                item_name = name
            
            # Получаем месяц и текущий год
            month = self.selected_month.get()
            year = datetime.now().year
            
            # Генерируем новое имя файла
            new_filename = self.renamer.generate_new_filename(item_surname, item_name, subject, month, year)
            target_path = base_folder / new_filename
            
            # Обработка коллизий
            if target_path.exists():
                stem = target_path.stem
                suffix = target_path.suffix
                counter = 1
                while target_path.exists():
                    new_filename = f"{stem}_{counter}{suffix}"
                    target_path = base_folder / new_filename
                    counter += 1
            
            # Переместить и переименовать файл
            try:
                shutil.move(str(file_path), str(target_path))
                self.review_store.remove(item.file_path)
                success_count += 1
                
                # Автообучение: сохраняем алиас для каждого файла
                if item.raw_subject and item.raw_subject != subject:
                    self.alias_manager.add_alias(item.raw_subject, subject)
                
                timestamp = time.strftime("%H:%M:%S")
                self.log_message(f"[{timestamp}] ✅ {file_path.name} → {new_filename}")
            except Exception as e:
                error_count += 1
                self.log_message(f"❌ Fehler bei {file_path.name}: {str(e)}")
        
        # Сохранить алиасы после массового применения
        if success_count > 0:
            self.alias_manager.save()
            self.log_message(f"📚 Aliase aktualisiert ({success_count} neue Zuordnungen)")
        
        # Сохранить изменения
        self.review_store.save()
        
        # Обновить статистику
        self.stats['success'] += success_count
        self.update_statistics()
        
        # Обновить отображение
        self.load_review_queue()
        self.clear_edit_fields()
        
        messagebox.showinfo(
            "Fertig",
            f"Erfolgreich: {success_count}\nFehler: {error_count}"
        )
    
    def remove_from_queue(self):
        """Удаляет файл из очереди без переименования"""
        if not self.current_review_item:
            messagebox.showwarning("Warnung", "Bitte wählen Sie eine Datei aus der Liste.")
            return
        
        response = messagebox.askyesno(
            "Bestätigung",
            "Möchten Sie diese Datei aus der Liste entfernen (ohne Umbenennung)?"
        )
        
        if not response:
            return
        
        # Удалить из очереди
        self.review_store.remove(self.current_review_item.file_path)
        self.review_store.save()
        
        # Обновить отображение
        self.load_review_queue()
        self.clear_edit_fields()
        
        messagebox.showinfo("Erfolg", "Datei aus der Liste entfernt.")
    
    def clear_edit_fields(self):
        """Очищает поля редактирования"""
        self.current_review_item = None
        self.file_label.config(text="")
        self.edit_surname.set("")
        self.edit_name.set("")
        self.edit_subject.set("")
        self.preview_text.config(state=tk.NORMAL)
        self.preview_text.delete(1.0, tk.END)
        self.preview_text.config(state=tk.DISABLED)
        
        # Удалить кнопки подсказок
        for widget in self.suggestions_frame.winfo_children():
            widget.destroy()
    
    def validate_input(self):
        """Überprüft die Eingaben"""
        if not self.selected_folder.get():
            messagebox.showerror("Fehler", "Bitte wählen Sie einen Eingabeordner!")
            return False
        
        folder_path = Path(self.selected_folder.get())
        if not folder_path.exists() or not folder_path.is_dir():
            messagebox.showerror("Fehler", "Der ausgewählte Ordner existiert nicht!")
            return False
        
        return True
    
    def start_processing(self):
        """Startet die Verarbeitung in einem separaten Thread"""
        if not self.validate_input():
            return
        
        if self.processing:
            messagebox.showwarning("Warnung", "Verarbeitung läuft bereits!")
            return
        
        # UI-Status ändern
        self.processing = True
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        
        # Reset Statistik
        self.stats = {'total': 0, 'success': 0, 'warning': 0, 'manual': 0, 'error': 0}
        self.update_statistics()
        
        # Очистить очередь проверки перед новой обработкой
        self.review_store.clear()
        
        # Thread starten
        self.processing_thread = threading.Thread(target=self.process_files, daemon=True)
        self.processing_thread.start()
    
    def stop_processing(self):
        """Stoppt die Verarbeitung"""
        self.processing = False
        self.log_message("⏹️ Verarbeitung wird gestoppt...")
    
    def process_files(self):
        """Verarbeitet die PDF-Dateien"""
        try:
            folder_path = Path(self.selected_folder.get())
            dry_run = self.dry_run_mode.get()
            manual_check_folder = folder_path / "manual_check"
            
            self.log_message("=" * 60)
            self.log_message("📁 PDF Datei Umbenenner gestartet")
            self.log_message("=" * 60)
            self.log_message(f"📂 Eingabeordner: {folder_path}")
            
            if dry_run:
                self.log_message("🧪 MODUS: Testlauf (keine Änderungen)")
            else:
                self.log_message("🔧 MODUS: Produktiv (Dateien werden umbenannt)")
            
            self.log_message("")
            
            # PDF-Dateien finden
            pdf_files = []
            for file_path in folder_path.iterdir():
                if file_path.is_file() and self.extractor.is_valid_pdf(file_path):
                    pdf_files.append(file_path)
            
            pdf_files = sorted(pdf_files)
            total_files = len(pdf_files)
            self.stats['total'] = total_files
            
            if total_files == 0:
                self.log_message("❌ Keine PDF-Dateien gefunden!")
                return
            
            self.log_message(f"📊 Gefunden: {total_files} PDF-Datei(en)")
            self.log_message("")
            
            # Dateien verarbeiten
            for i, pdf_path in enumerate(pdf_files):
                if not self.processing:
                    self.log_message("⏹️ Verarbeitung vom Benutzer gestoppt!")
                    break
                
                # Fortschritt aktualisieren
                progress = (i / total_files) * 100
                self.progress_var.set(progress)
                self.progress_label.config(text=f"Verarbeite Datei {i+1} von {total_files}")
                
                try:
                    self.process_single_file(pdf_path, manual_check_folder, dry_run)
                except Exception as e:
                    self.log_message(f"❌ Fehler bei {pdf_path.name}: {str(e)}")
                    self.stats['error'] += 1
                
                self.update_statistics()
            
            # Abschluss
            if self.processing:
                self.progress_var.set(100)
                self.progress_label.config(text="Verarbeitung abgeschlossen!")
                
                # Сохранить очередь проверки, если есть элементы
                if self.review_store.count() > 0 and not dry_run:
                    if self.review_store.save():
                        self.log_message(f"\n📋 Warteschlange gespeichert: {self.review_store.store_path.name}")
                        self.log_message(f"   {self.review_store.count()} Datei(en) zur manuellen Prüfung")
                
                self.show_final_report()
            
        except Exception as e:
            self.log_message(f"❌ Schwerwiegender Fehler: {str(e)}")
            messagebox.showerror("Fehler", f"Ein schwerwiegender Fehler ist aufgetreten:\n{str(e)}")
        finally:
            # UI-Status zurücksetzen
            self.processing = False
            self.start_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)
    
    def process_single_file(self, pdf_path, manual_check_folder, dry_run):
        """Verarbeitet eine einzelne PDF-Datei"""
        self.log_message(f"📄 Verarbeite: {pdf_path.name}")
        
        # Text extrahieren
        text = self.extractor.extract_first_lines(pdf_path)
        if not text:
            self.log_message(f"   ❌ Text konnte nicht extrahiert werden")
            self.stats['error'] += 1
            return
        
        # Namen und Fach parsen
        student_name, subject = self.parser.parse_document(text)
        
        # Validierung
        validation_result = self.validator.validate_document_data(student_name, subject)
        
        if validation_result.level == ValidationLevel.MANUAL_CHECK:
            # Manuelle Prüfung erforderlich
            self.stats['manual'] += 1
            
            # Переместить файл в папку manual_check
            moved_path = None
            if not dry_run:
                moved_path = self.renamer.move_to_folder(pdf_path, manual_check_folder)
                if moved_path:
                    self.log_message(f"   📁 Verschoben nach: {manual_check_folder.name}/{moved_path.name}")
                else:
                    self.log_message(f"   ❌ Fehler beim Verschieben")
                    self.stats['error'] += 1
                    return
            else:
                self.log_message(f"   📁 [Simulation] Würde verschoben nach: {manual_check_folder.name}/")
            
            # Создать ReviewItem для очереди
            # Используем фактическое имя после перемещения (учитывает коллизии)
            if moved_path:
                # Путь относительно selected_folder: manual_check/actual_filename.pdf
                relative_path = str(Path("manual_check") / moved_path.name)
            else:
                # Dry run: используем исходное имя
                relative_path = str(Path("manual_check") / pdf_path.name)
            
            review_item = ReviewItem(
                file_path=relative_path,
                raw_name=student_name,
                raw_subject=subject,
                reason=validation_result.message,
                suggestions=validation_result.suggestions,
                preview_text=text[:500]  # Первые 500 символов как контекст
            )
            
            # Добавить в очередь
            self.review_store.add(review_item)
            
            self.log_message(f"   ⚠️ Manuelle Prüfung: {validation_result.message}")
            return
        
        # Datei umbenennen
        if student_name and subject:
            surname, name = student_name.split()
            
            # Получаем месяц и текущий год
            month = self.selected_month.get()
            year = datetime.now().year
            
            new_filename = self.renamer.generate_new_filename(surname, name, subject, month, year)
            new_path = pdf_path.parent / new_filename
            
            self.log_message(f"   👤 Name: {student_name}")
            self.log_message(f"   📚 Fach: {subject}")
            
            if validation_result.level == ValidationLevel.WARNING:
                self.stats['warning'] += 1
                self.log_message(f"   ⚠️ Warnung: {validation_result.message}")
            
            # Umbenennen
            success = self.renamer.rename_file(pdf_path, new_path, dry_run)
            
            if success:
                if validation_result.level == ValidationLevel.VALID:
                    self.stats['success'] += 1
                
                if dry_run:
                    self.log_message(f"   ✅ [Simulation] {pdf_path.name} → {new_filename}")
                else:
                    self.log_message(f"   ✅ Umbenannt: {pdf_path.name} → {new_filename}")
            else:
                self.stats['error'] += 1
                self.log_message(f"   ❌ Fehler beim Umbenennen")
        else:
            self.stats['error'] += 1
            self.log_message(f"   ❌ Name oder Fach konnte nicht ermittelt werden")
        
        self.log_message("")  # Leerzeile für bessere Lesbarkeit
    
    def show_final_report(self):
        """Zeigt den Abschlussbericht"""
        self.log_message("=" * 60)
        self.log_message("📊 ABSCHLUSSBERICHT")
        self.log_message("=" * 60)
        
        total = self.stats['total']
        success = self.stats['success']
        warning = self.stats['warning']
        manual = self.stats['manual']
        error = self.stats['error']
        
        self.log_message(f"📈 Insgesamt verarbeitet:         {total}")
        self.log_message(f"✅ Erfolgreich umbenannt:         {success}")
        self.log_message(f"⚠️ Mit Warnungen umbenannt:       {warning}")
        self.log_message(f"📋 Manuelle Prüfung erforderlich: {manual}")
        self.log_message(f"❌ Fehler:                        {error}")
        self.log_message("=" * 60)
        
        if manual > 0:
            manual_folder = Path(self.selected_folder.get()) / "manual_check"
            self.log_message(f"📁 Dateien für manuelle Prüfung: {manual_folder}")
        
        # Erfolgsmeldung
        if error == 0 and manual == 0:
            messagebox.showinfo("Erfolg", 
                              f"Alle {total} Dateien wurden erfolgreich verarbeitet!")
        elif error == 0:
            messagebox.showinfo("Teilweise erfolgreich", 
                              f"{success + warning} von {total} Dateien verarbeitet.\n"
                              f"{manual} Dateien benötigen manuelle Prüfung.")
        else:
            messagebox.showwarning("Mit Fehlern abgeschlossen", 
                                 f"Verarbeitung mit {error} Fehlern abgeschlossen.\n"
                                 f"Siehe Log für Details.")
    
    def run(self):
        """Startet die GUI"""
        # Willkommensnachricht
        self.log_message("🎯 PDF Datei Umbenenner bereit!")
        self.log_message("📌 Wählen Sie einen Ordner und starten Sie die Verarbeitung.")
        self.log_message("")
        
        self.update_statistics()
        self.root.mainloop()


def main():
    """Haupteinstiegspunkt für die GUI-Anwendung"""
    try:
        app = PDFRenamerGUI()
        app.run()
    except Exception as e:
        messagebox.showerror("Kritischer Fehler", 
                           f"Die Anwendung konnte nicht gestartet werden:\n{str(e)}")
        sys.exit(1)


if __name__ == '__main__':
    main()