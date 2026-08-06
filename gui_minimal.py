"""
Простая стабильная версия GUI для компиляции PyInstaller
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
from pathlib import Path
import sys
import time
import os
from datetime import datetime

# Добавляем текущую директорию в путь для импорта модулей
if getattr(sys, 'frozen', False):
    application_path = sys._MEIPASS
else:
    application_path = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, application_path)

from pdf_extractor import PDFExtractor
from form_parser import FormParser
from validator import Validator
from file_renamer import FileRenamer
from processing_service import DocumentProcessingService, PlannedAction
from review_queue import ReviewStore
from student_registry import StudentRegistry


class SimplePDFRenamerGUI:
    """Простая GUI для PDF Umbenenner"""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PDF Datei Umbenenner v1.0")
        self.root.geometry("800x600")
        
        # Переменные
        self.selected_folder = tk.StringVar()
        self.dry_run_mode = tk.BooleanVar(value=False)
        self.selected_month = tk.IntVar(value=1)
        self.progress_var = tk.DoubleVar()
        self.processing = False
        
        # Компоненты
        self.extractor = PDFExtractor()
        self.parser = FormParser()
        self.validator = Validator()
        self.renamer = FileRenamer()
        self.review_store = ReviewStore()
        self.student_registry = StudentRegistry(Path(application_path) / "Schuler.csv")
        self.processing_service = DocumentProcessingService(
            extractor=self.extractor,
            parser=self.parser,
            student_registry=self.student_registry,
            validator=self.validator,
            review_store=self.review_store,
            renamer=self.renamer,
        )
        
        # Статистика
        self.stats = {'total': 0, 'success': 0, 'warning': 0, 'manual': 0, 'error': 0}
        
        self.setup_ui()
        self.center_window()
        
    def center_window(self):
        """Центрируем окно"""
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - (self.root.winfo_width() // 2)
        y = (self.root.winfo_screenheight() // 2) - (self.root.winfo_height() // 2)
        self.root.geometry(f'+{x}+{y}')
        
    def setup_ui(self):
        """Создаем простой интерфейс"""
        
        # Главный фрейм
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Заголовок
        title_label = ttk.Label(main_frame, text="PDF Datei Umbenenner v1.0")
        title_label.pack(pady=(0, 20))
        
        # Выбор папки
        folder_frame = ttk.LabelFrame(main_frame, text="Ordner auswaehlen")
        folder_frame.pack(fill=tk.X, pady=(0, 10))
        
        folder_entry_frame = ttk.Frame(folder_frame)
        folder_entry_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.folder_entry = ttk.Entry(folder_entry_frame, textvariable=self.selected_folder)
        self.folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        
        browse_btn = ttk.Button(folder_entry_frame, text="Durchsuchen...", command=self.browse_folder)
        browse_btn.pack(side=tk.RIGHT)
        
        # Настройки
        settings_frame = ttk.LabelFrame(main_frame, text="Einstellungen")
        settings_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.dry_run_check = ttk.Checkbutton(settings_frame, text="Testmodus (keine Aenderungen)", 
                                           variable=self.dry_run_mode)
        self.dry_run_check.pack(anchor=tk.W, padx=10, pady=10)
        
        # Прогресс
        progress_frame = ttk.LabelFrame(main_frame, text="Fortschritt")
        progress_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, padx=10, pady=(10, 5))
        
        self.progress_label = ttk.Label(progress_frame, text="Bereit zum Starten")
        self.progress_label.pack(padx=10, pady=(0, 10))
        
        # Кнопки
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(pady=10)
        
        self.start_btn = ttk.Button(button_frame, text="Verarbeitung starten", 
                                   command=self.start_processing)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.stop_btn = ttk.Button(button_frame, text="Stoppen", 
                                  command=self.stop_processing, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        clear_btn = ttk.Button(button_frame, text="Log loeschen", command=self.clear_log)
        clear_btn.pack(side=tk.LEFT)
        
        # Лог
        log_frame = ttk.LabelFrame(main_frame, text="Verarbeitungslog")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        self.log_text = scrolledtext.ScrolledText(log_frame)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Статистика
        stats_frame = ttk.LabelFrame(main_frame, text="Statistik")
        stats_frame.pack(fill=tk.X)
        
        self.stats_text = tk.Text(stats_frame, state=tk.DISABLED)
        self.stats_text.pack(fill=tk.X, padx=10, pady=10)
        
    def browse_folder(self):
        """Выбор папки"""
        folder = filedialog.askdirectory(title="Waehlen Sie den Ordner mit PDF-Dateien")
        if folder:
            self.selected_folder.set(folder)
            self.review_store.store_path = Path(folder) / "manual_review.json"
            self.review_store.load()
            self.log_message(f"Ordner ausgewaehlt: {folder}")
    
    def log_message(self, message):
        """Добавляем сообщение в лог"""
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()
    
    def clear_log(self):
        """Очищаем лог"""
        self.log_text.delete(1.0, tk.END)
        self.update_statistics()
        self.log_message("Log geloescht")
    
    def update_statistics(self):
        """Обновляем статистику"""
        self.stats_text.config(state=tk.NORMAL)
        self.stats_text.delete(1.0, tk.END)
        
        stats_text = f"""Verarbeitungsstatistik:
Insgesamt: {self.stats['total']}
Erfolgreich: {self.stats['success']}
Warnungen: {self.stats['warning']}
Manuelle Pruefung: {self.stats['manual']}
Fehler: {self.stats['error']}"""
        
        self.stats_text.insert(1.0, stats_text)
        self.stats_text.config(state=tk.DISABLED)
    
    def validate_input(self):
        """Проверяем ввод"""
        if not self.selected_folder.get():
            messagebox.showerror("Fehler", "Bitte waehlen Sie einen Ordner!")
            return False
        
        folder_path = Path(self.selected_folder.get())
        if not folder_path.exists() or not folder_path.is_dir():
            messagebox.showerror("Fehler", "Der Ordner existiert nicht!")
            return False
        
        return True
    
    def start_processing(self):
        """Запускаем обработку"""
        if not self.validate_input():
            return
        
        if self.processing:
            messagebox.showwarning("Warnung", "Verarbeitung laeuft bereits!")
            return
        
        # Подтверждение для продуктивного режима
        if not self.dry_run_mode.get():
            result = messagebox.askyesno(
                "Produktivmodus",
                "Dateien werden tatsaechlich umbenannt!\\nFortfahren?"
            )
            if not result:
                return
        
        # Меняем состояние UI
        self.processing = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        
        # Сбрасываем статистику
        self.stats = {'total': 0, 'success': 0, 'warning': 0, 'manual': 0, 'error': 0}
        self.update_statistics()
        
        # Запускаем поток
        threading.Thread(target=self.process_files, daemon=True).start()
    
    def stop_processing(self):
        """Останавливаем обработку"""
        self.processing = False
        self.log_message("Verarbeitung wird gestoppt...")
    
    def process_files(self):
        """Обрабатываем файлы"""
        try:
            folder_path = Path(self.selected_folder.get())
            dry_run = self.dry_run_mode.get()
            manual_check_folder = folder_path / "manual_check"
            self.processing_service.manual_review_folder = manual_check_folder
            self.renamer.reset_reservations()
            
            self.log_message("=" * 50)
            self.log_message("PDF Datei Umbenenner gestartet")
            self.log_message("=" * 50)
            self.log_message(f"Ordner: {folder_path}")
            
            if dry_run:
                self.log_message("MODUS: Testlauf")
            else:
                self.log_message("MODUS: Produktiv")
            
            # PDF-Dateien finden
            pdf_files = []
            for file_path in folder_path.iterdir():
                if file_path.is_file() and file_path.suffix.casefold() == ".pdf":
                    pdf_files.append(file_path)
            
            pdf_files = sorted(pdf_files)
            total_files = len(pdf_files)
            self.stats['total'] = total_files
            
            if total_files == 0:
                self.log_message("Keine PDF-Dateien gefunden!")
                return
            
            self.log_message(f"Gefunden: {total_files} PDF-Dateien")
            
            # Обрабатываем файлы
            for i, pdf_path in enumerate(pdf_files):
                if not self.processing:
                    self.log_message("Verarbeitung gestoppt!")
                    break
                
                # Обновляем прогресс
                progress = (i / total_files) * 100
                self.progress_var.set(progress)
                self.progress_label.config(text=f"Datei {i+1} von {total_files}")
                
                try:
                    self.process_single_file(pdf_path, manual_check_folder, dry_run)
                except Exception as e:
                    self.log_message(f"FEHLER bei {pdf_path.name}: {str(e)}")
                    self.stats['error'] += 1
                
                self.update_statistics()
                time.sleep(0.1)
            
            # Завершение
            if self.processing:
                self.progress_var.set(100)
                self.progress_label.config(text="Verarbeitung abgeschlossen!")
                if self.review_store.count() > 0 and not dry_run:
                    self.review_store.save()
                self.show_final_report()
            
        except Exception as e:
            self.log_message(f"SCHWERER FEHLER: {str(e)}")
            messagebox.showerror("Fehler", f"Schwerwiegender Fehler:\\n{str(e)}")
        finally:
            # UI zurücksetzen
            self.processing = False
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)
    
    def process_single_file(self, pdf_path, manual_check_folder, dry_run):
        """Показываем результат центрального сервиса."""
        self.log_message(f"Verarbeite: {pdf_path.name}")

        self.processing_service.manual_review_folder = Path(manual_check_folder)
        result = self.processing_service.process_document(
            pdf_path,
            self.selected_month.get(),
            datetime.now().year,
            dry_run,
        )
        if result.error:
            self.stats['error'] += 1
            self.log_message(f"  FEHLER: {result.error}")
        elif result.planned_action == PlannedAction.MANUAL_REVIEW:
            self.stats['manual'] += 1
            prefix = "[Simulation] " if dry_run else ""
            self.log_message(f"  {prefix}Manuelle Pruefung: {result.review_item.reason}")
            self.log_message(f"  Ziel: {result.planned_path}")
        else:
            if result.warnings:
                self.stats['warning'] += 1
                for warning in result.warnings:
                    self.log_message(f"  WARNUNG: {warning}")
            else:
                self.stats['success'] += 1
            prefix = "[Simulation] " if dry_run else ""
            self.log_message(f"  {prefix}{pdf_path.name} -> {result.planned_name}")
    
    def show_final_report(self):
        """Финальный отчет"""
        self.log_message("=" * 50)
        self.log_message("ABSCHLUSSBERICHT")
        self.log_message("=" * 50)
        
        total = self.stats['total']
        success = self.stats['success']
        warning = self.stats['warning']
        manual = self.stats['manual']
        error = self.stats['error']
        
        self.log_message(f"Insgesamt: {total}")
        self.log_message(f"Erfolgreich: {success}")
        self.log_message(f"Warnungen: {warning}")
        self.log_message(f"Manuelle Pruefung: {manual}")
        self.log_message(f"Fehler: {error}")
        
        if error == 0 and manual == 0:
            messagebox.showinfo("Erfolg", f"Alle {total} Dateien erfolgreich verarbeitet!")
        elif error == 0:
            messagebox.showinfo("Teilweise erfolgreich", 
                              f"{success + warning} von {total} Dateien verarbeitet.\\n"
                              f"{manual} Dateien benoetigen manuelle Pruefung.")
        else:
            messagebox.showwarning("Mit Fehlern", 
                                 f"Verarbeitung mit {error} Fehlern abgeschlossen.")
    
    def run(self):
        """Запускаем GUI"""
        self.log_message("PDF Datei Umbenenner bereit!")
        self.log_message("Waehlen Sie einen Ordner und starten Sie die Verarbeitung.")
        self.update_statistics()
        self.root.mainloop()


def main():
    """Главная функция"""
    try:
        app = SimplePDFRenamerGUI()
        app.run()
    except Exception as e:
        messagebox.showerror("Kritischer Fehler", f"Die Anwendung konnte nicht gestartet werden:\\n{str(e)}")
        sys.exit(1)


if __name__ == '__main__':
    main()
