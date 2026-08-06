"""
Главный скрипт для автоматического переименования PDF файлов
"""

import argparse
from pathlib import Path
from typing import List, Tuple
import sys
from datetime import datetime

from pdf_extractor import PDFExtractor
from text_parser import TextParser
from validator import Validator, ValidationLevel
from file_renamer import FileRenamer
from review_queue import ReviewStore, ReviewItem


class PDFRenamerApp:
    """Главный класс приложения"""
    
    def __init__(self, input_folder: Path, dry_run: bool = False, month: int = None):
        self.input_folder = input_folder
        self.dry_run = dry_run
        self.month = month  # Номер месяца (1-12) или None
        self.year = datetime.now().year  # Текущий год
        self.manual_check_folder = input_folder / "manual_check"
        
        self.extractor = PDFExtractor()
        self.parser = TextParser()
        self.validator = Validator()
        self.renamer = FileRenamer()
        
        # Хранилище для очереди ручной проверки
        self.review_store = ReviewStore(input_folder / "manual_review.json")
        
        # Статистика
        self.stats = {
            'total': 0,
            'success': 0,
            'warning': 0,
            'manual': 0,
            'error': 0
        }
    
    def find_pdf_files(self) -> List[Path]:
        """
        Находит все PDF файлы в папке
        
        Returns:
            Список путей к PDF файлам
        """
        pdf_files = []
        
        for file_path in self.input_folder.iterdir():
            if file_path.is_file() and self.extractor.is_valid_pdf(file_path):
                pdf_files.append(file_path)
        
        return sorted(pdf_files)
    
    def process_file(self, pdf_path: Path) -> Tuple[bool, str]:
        """
        Обрабатывает один PDF файл
        
        Args:
            pdf_path: Путь к PDF файлу
            
        Returns:
            (успех, сообщение)
        """
        print(f"\nVerarbeite: {pdf_path.name}")
        
        # Извлекаем текст
        text = self.extractor.extract_first_lines(pdf_path)
        if not text:
            return False, "Text konnte nicht extrahiert werden"
        
        # Парсим имя и дисциплину
        student_name, subject = self.parser.parse_document(text)
        
        # Валидация
        validation_result = self.validator.validate_document_data(
            student_name, subject
        )
        
        # Обработка в зависимости от уровня валидации
        if validation_result.level == ValidationLevel.MANUAL_CHECK:
            # Требует ручной проверки
            self.stats['manual'] += 1
            
            # Перемещаем в папку для ручной проверки
            moved_path = None
            if not self.dry_run:
                moved_path = self.renamer.move_to_folder(pdf_path, self.manual_check_folder)
                if not moved_path:
                    print(f"  -> Fehler beim Verschieben der Datei")
                    return
            
            # Создаем ReviewItem для очереди ручной проверки
            # Используем фактическое имя после перемещения (учитывает коллизии)
            if moved_path:
                # Путь относительно input_folder: manual_check/actual_filename.pdf
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
            
            # Добавляем в очередь
            self.review_store.add(review_item)
            
            message = f"  -> Manuelle Pruefung erforderlich: {validation_result.message}"
            print(message)
            if not self.dry_run:
                print(f"  -> Verschoben nach: {self.manual_check_folder.name}/{moved_path.name}")
            return False, message
        
        # Генерируем новое имя
        surname, name = student_name.split()
        new_filename = self.renamer.generate_new_filename(
            surname, name, subject, self.month, self.year
        )
        new_path = pdf_path.parent / new_filename
        
        # Информация о переименовании
        print(f"  Name: {student_name}")
        print(f"  Fach: {subject}")
        
        if validation_result.level == ValidationLevel.WARNING:
            self.stats['warning'] += 1
            print(f"  Warnung: {validation_result.message}")
        
        # Переименовываем файл
        success = self.renamer.rename_file(pdf_path, new_path, self.dry_run)
        
        if success:
            if validation_result.level == ValidationLevel.VALID:
                self.stats['success'] += 1
            print(f"  -> Neue Datei: {new_filename}")
            return True, "Erfolgreich umbenannt"
        else:
            self.stats['error'] += 1
            return False, "Fehler beim Umbenennen"
    
    def run(self):
        """Запускает процесс переименования"""
        print("=" * 60)
        print("PDF Datei Umbenenner")
        print("=" * 60)
        print(f"Eingabeordner: {self.input_folder}")
        
        if self.dry_run:
            print("MODUS: Simulation (keine Aenderungen)")
        
        print()
        
        # Находим все PDF файлы
        pdf_files = self.find_pdf_files()
        self.stats['total'] = len(pdf_files)
        
        if not pdf_files:
            print("Keine PDF-Dateien gefunden!")
            return
        
        print(f"Gefunden: {len(pdf_files)} PDF-Datei(en)")
        print()
        
        # Обрабатываем каждый файл
        for pdf_path in pdf_files:
            try:
                self.process_file(pdf_path)
            except Exception as e:
                print(f"  Fehler: {str(e)}")
                self.stats['error'] += 1
        
        # Сохраняем очередь ручной проверки
        if self.review_store.count() > 0 and not self.dry_run:
            if self.review_store.save():
                print(f"\nWarteschlange fuer manuelle Pruefung gespeichert: {self.review_store.store_path.name}")
                print(f"Anzahl der Eintraege: {self.review_store.count()}")
        
        # Выводим статистику
        self.print_statistics()
    
    def print_statistics(self):
        """Выводит итоговую статистику"""
        print("\n" + "=" * 60)
        print("STATISTIK")
        print("=" * 60)
        print(f"Insgesamt verarbeitet:        {self.stats['total']}")
        print(f"Erfolgreich umbenannt:        {self.stats['success']}")
        print(f"Mit Warnungen umbenannt:      {self.stats['warning']}")
        print(f"Manuelle Pruefung erforderlich: {self.stats['manual']}")
        print(f"Fehler:                       {self.stats['error']}")
        print("=" * 60)
        
        if self.stats['manual'] > 0:
            print(f"\nDateien fuer manuelle Pruefung: {self.manual_check_folder}")


def main():
    """Точка входа в программу"""
    parser = argparse.ArgumentParser(
        description='Automatisches Umbenennen von gescannten PDF-Formularen'
    )
    
    parser.add_argument(
        'folder',
        type=str,
        nargs='?',  # Делаем аргумент опциональным
        help='Pfad zum Ordner mit PDF-Dateien (optional - wenn не указан, запускается GUI)'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Simulation ohne tatsaechliche Aenderungen'
    )
    
    parser.add_argument(
        '--gui',
        action='store_true',
        help='GUI-Modus starten'
    )
    
    parser.add_argument(
        '--month',
        type=int,
        choices=range(1, 13),
        help='Monat (1-12) fuer Dateinamen'
    )
    
    args = parser.parse_args()
    
    # Если указан --gui или папка не указана, запускаем GUI
    if args.gui or not args.folder:
        try:
            from gui_main import PDFRenamerGUI
            app = PDFRenamerGUI()
            app.run()
        except ImportError:
            print("Fehler: GUI-Komponenten nicht verfügbar!")
            print("Stellen Sie sicher, dass tkinter installiert ist.")
            sys.exit(1)
        return
    
    # Проверяем существование папки для командной строки
    input_folder = Path(args.folder)
    if not input_folder.exists() or not input_folder.is_dir():
        print(f"Fehler: Ordner '{args.folder}' existiert nicht!")
        sys.exit(1)
    
    # Запускаем приложение командной строки
    app = PDFRenamerApp(input_folder, dry_run=args.dry_run, month=args.month)
    app.run()


if __name__ == '__main__':
    main()
