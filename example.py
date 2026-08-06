"""
Пример программного использования модулей
"""

from pathlib import Path
from pdf_extractor import PDFExtractor
from text_parser import TextParser
from validator import Validator, ValidationLevel
from file_renamer import FileRenamer


def example_single_file():
    """Пример обработки одного файла"""
    
    print("Пример 1: Обработка одного файла")
    print("-" * 50)
    
    # Путь к файлу
    pdf_path = Path("example.pdf")
    
    # Инициализация компонентов
    extractor = PDFExtractor()
    parser = TextParser()
    validator = Validator()
    renamer = FileRenamer()
    
    # Извлечение текста
    text = extractor.extract_first_lines(pdf_path)
    print(f"Извлеченный текст:\n{text}\n")
    
    # Парсинг
    name, subject = parser.parse_document(text)
    print(f"Имя: {name}")
    print(f"Дисциплина: {subject}\n")
    
    # Валидация
    result = validator.validate_document_data(name, subject)
    print(f"Уровень валидации: {result.level.value}")
    if result.message:
        print(f"Сообщение: {result.message}")
    
    # Генерация нового имени
    if result.is_valid() and name and subject:
        surname, first_name = name.split()
        new_name = renamer.generate_new_filename(surname, first_name, subject)
        print(f"\nНовое имя файла: {new_name}")


def example_batch_processing():
    """Пример пакетной обработки"""
    
    print("\n\nПример 2: Пакетная обработка")
    print("-" * 50)
    
    # Папка с файлами
    folder = Path("scans")
    
    extractor = PDFExtractor()
    parser = TextParser()
    validator = Validator()
    renamer = FileRenamer()
    
    # Статистика
    stats = {'success': 0, 'warning': 0, 'manual': 0}
    
    # Обрабатываем все PDF
    for pdf_file in folder.glob("*.pdf"):
        print(f"\nОбработка: {pdf_file.name}")
        
        # Проверка валидности PDF
        if not extractor.is_valid_pdf(pdf_file):
            print("  Невалидный PDF")
            continue
        
        # Извлечение и парсинг
        text = extractor.extract_first_lines(pdf_file)
        if not text:
            print("  Не удалось извлечь текст")
            continue
        
        name, subject = parser.parse_document(text)
        result = validator.validate_document_data(name, subject)
        
        # Обработка результата
        if result.level == ValidationLevel.VALID:
            print(f"  ✓ {name} - {subject}")
            stats['success'] += 1
        elif result.level == ValidationLevel.WARNING:
            print(f"  ⚠ {name} - {subject}")
            print(f"    {result.message}")
            stats['warning'] += 1
        else:
            print(f"  ✗ Требуется ручная проверка")
            print(f"    {result.message}")
            stats['manual'] += 1
    
    # Итоговая статистика
    print("\n" + "=" * 50)
    print("Статистика:")
    print(f"  Успешно: {stats['success']}")
    print(f"  С предупреждениями: {stats['warning']}")
    print(f"  Требуют проверки: {stats['manual']}")


def example_custom_validation():
    """Пример кастомной валидации"""
    
    print("\n\nПример 3: Кастомная валидация")
    print("-" * 50)
    
    validator = Validator()
    
    # Тестовые случаи
    test_cases = [
        ("Mueller Hans", "Deutsch"),
        ("Smith123 John", "Mathematik"),  # С цифрами
        ("O'Connor Mary", "Englisch"),     # С апострофом
        ("Иванов Петр", "Deutsch"),        # Кириллица
        ("Ab C", "Physik"),                # Слишком короткое
        (None, "Chemie"),                  # Отсутствует имя
    ]
    
    for name, subject in test_cases:
        result = validator.validate_document_data(name, subject)
        
        status = "✓" if result.is_valid() else "✗"
        print(f"{status} {name} / {subject}")
        
        if result.message:
            print(f"  -> {result.message}")


if __name__ == "__main__":
    # Раскомментируйте нужный пример:
    
    # example_single_file()
    # example_batch_processing()
    example_custom_validation()
