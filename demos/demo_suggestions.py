"""
Демонстрация использования подсказок дисциплин
"""

from validator import Validator


def demo_suggestions():
    """Демонстрация работы подсказок"""
    
    print("=" * 60)
    print("ДЕМОНСТРАЦИЯ ПОДСКАЗОК ДИСЦИПЛИН")
    print("=" * 60)
    print()
    
    # Примеры типичных ошибок OCR
    ocr_errors = [
        ("Mathemat", "OCR обрезал последние буквы"),
        ("Phisik", "OCR неправильно распознал 'y' как 'i'"),
        ("Chemle", "OCR спутал 'i' и 'l'"),
        ("Biologi", "OCR обрезал конец слова"),
        ("Deulsch", "OCR спутал 't' и 'l'"),
        ("Geografie", "Правильная дисциплина (без ошибок)"),
    ]
    
    print("📋 Типичные ошибки OCR при распознавании дисциплин:\n")
    
    for ocr_text, description in ocr_errors:
        print(f"Распознано: '{ocr_text}'")
        print(f"  Причина: {description}")
        
        result = Validator.validate_subject(ocr_text)
        
        if result.is_valid():
            print(f"  ✓ Статус: ВАЛИДНО")
        else:
            print(f"  ✗ Статус: ТРЕБУЕТ ПРОВЕРКИ")
            
            if result.suggestions:
                print(f"  💡 Возможно, вы имели в виду:")
                for i, suggestion in enumerate(result.suggestions, 1):
                    print(f"      {i}. {suggestion}")
            else:
                print(f"  ⚠ Подсказок не найдено (слишком далеко от известных дисциплин)")
        
        print()
    
    print("-" * 60)
    print()
    
    # Демонстрация прямого использования метода suggest_subjects
    print("🔍 Прямое использование метода suggest_subjects():\n")
    
    test_inputs = ["Math", "Phys", "Chem", "Bio"]
    
    for input_text in test_inputs:
        suggestions = Validator.suggest_subjects(input_text, limit=3)
        print(f"Ввод: '{input_text}' → Подсказки: {suggestions}")
    
    print()
    print("=" * 60)


def demo_validation_flow():
    """Демонстрация полного процесса валидации"""
    
    print("\n" + "=" * 60)
    print("ДЕМОНСТРАЦИЯ ПОЛНОГО ПРОЦЕССА ВАЛИДАЦИИ")
    print("=" * 60)
    print()
    
    # Симуляция распознанного документа
    documents = [
        {
            "filename": "scan_001.pdf",
            "name": "Mueller Anna",
            "subject": "Mathematik",
            "description": "Корректный документ"
        },
        {
            "filename": "scan_002.pdf",
            "name": "Schmidt Max",
            "subject": "Phisik",  # Ошибка OCR
            "description": "Ошибка в дисциплине"
        },
        {
            "filename": "scan_003.pdf",
            "name": "Weber Lisa",
            "subject": "Biologi",  # Неполное слово
            "description": "Неполное название дисциплины"
        },
    ]
    
    for doc in documents:
        print(f"📄 Файл: {doc['filename']}")
        print(f"   Имя: {doc['name']}")
        print(f"   Дисциплина: {doc['subject']}")
        print(f"   ({doc['description']})")
        print()
        
        result = Validator.validate_document_data(doc["name"], doc["subject"])
        
        if result.is_valid():
            print(f"   ✓ Можно автоматически переименовать")
        else:
            print(f"   ✗ Требуется ручная проверка")
            print(f"   Причина: {result.message}")
            
            if result.suggestions:
                print(f"   💡 Предложенные исправления:")
                for i, suggestion in enumerate(result.suggestions, 1):
                    print(f"       {i}. {suggestion}")
        
        print()
        print("-" * 60)
        print()
    
    print("=" * 60)


if __name__ == "__main__":
    demo_suggestions()
    demo_validation_flow()
    
    print("\n✓ Демонстрация завершена")
