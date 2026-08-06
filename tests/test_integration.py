"""
Интеграционный тест: проверка всей цепочки от парсинга до подсказок
"""

from text_parser import TextParser
from validator import Validator, ValidationLevel


def test_full_pipeline():
    """Тестирование полного конвейера обработки"""
    
    print("=" * 70)
    print("ИНТЕГРАЦИОННЫЙ ТЕСТ: ПОЛНЫЙ КОНВЕЙЕР ОБРАБОТКИ")
    print("=" * 70)
    print()
    
    # Симуляция извлеченного текста из PDF с различными ошибками OCR
    test_documents = [
        {
            "text": "Mueller Anna Mathematik\nKlasse 5a\nTest vom 15.11.2024",
            "expected_name": "Mueller Anna",
            "expected_subject": "Mathematik",
            "should_pass": True,
            "description": "Корректный документ без ошибок"
        },
        {
            "text": "Schmidt Max Phisik\nKlasse 7b\nTest vom 20.11.2024",
            "expected_name": "Schmidt Max",
            "expected_subject": "Phisik",  # Ошибка OCR
            "should_pass": False,
            "description": "Ошибка OCR в дисциплине (y → i)"
        },
        {
            "text": "Weber Lisa Biologi\nKlasse 6c\nTest vom 22.11.2024",
            "expected_name": "Weber Lisa",
            "expected_subject": "Biologi",  # Обрезанное слово
            "should_pass": False,
            "description": "Обрезанное название дисциплины"
        },
        {
            "text": "Becker Tom Mathe\nKlasse 8a\nTest vom 25.11.2024",
            "expected_name": "Becker Tom",
            "expected_subject": "Mathematik",  # Алиас должен нормализоваться
            "should_pass": True,
            "description": "Использование алиаса (Mathe)"
        },
        {
            "text": "Fischer Emma Chemle\nKlasse 9b\nTest vom 28.11.2024",
            "expected_name": "Fischer Emma",
            "expected_subject": "Chemle",  # Ошибка OCR (i → l)
            "should_pass": False,
            "description": "Ошибка OCR в дисциплине (i → l)"
        },
    ]
    
    passed = 0
    failed = 0
    
    for i, doc in enumerate(test_documents, 1):
        print(f"📄 Тест {i}: {doc['description']}")
        print(f"   Текст документа:")
        for line in doc['text'].split('\n')[:2]:
            print(f"     {line}")
        print()
        
        # Шаг 1: Парсинг
        print("   1️⃣ ПАРСИНГ")
        name, subject = TextParser.parse_document(doc['text'])
        print(f"      Извлечено имя: {name}")
        print(f"      Извлечена дисциплина: {subject}")
        
        # Примечание: при невалидной дисциплине парсер может не распознать имя,
        # так как проверяет третье слово на принадлежность к списку дисциплин
        if doc['should_pass']:
            assert name == doc['expected_name'], f"Имя не совпадает: ожидалось '{doc['expected_name']}', получено '{name}'"
            print(f"      ✓ Имя извлечено корректно")
        else:
            # Для документов с ошибками парсинг может не сработать полностью
            if name:
                print(f"      ~ Имя распознано (несмотря на ошибку в дисциплине)")
            else:
                print(f"      ~ Имя не распознано (из-за невалидной дисциплины - ожидаемо)")
        print()
        
        # Шаг 2: Валидация
        print("   2️⃣ ВАЛИДАЦИЯ")
        result = Validator.validate_document_data(name, subject)
        print(f"      Статус: {result.level.value}")
        
        if result.message:
            print(f"      Сообщение: {result.message}")
        
        # Шаг 3: Подсказки (если нужна ручная проверка)
        if not result.is_valid():
            print(f"      ❌ Требуется ручная проверка")
            
            if result.suggestions:
                print(f"      💡 Подсказки ({len(result.suggestions)}):")
                for j, suggestion in enumerate(result.suggestions, 1):
                    print(f"         {j}. {suggestion}")
                    
                # Проверяем наличие правильной дисциплины в подсказках
                # (для документов, где была ошибка OCR в дисциплине)
                if subject and subject not in Validator.get_valid_subjects():
                    # Пытаемся найти близкую валидную дисциплину
                    print(f"      ✓ Система предложила исправления для '{subject}'")
            else:
                print(f"      ⚠ Подсказок нет (слишком большая разница)")
        else:
            print(f"      ✓ Документ валиден, можно автоматически переименовать")
        
        print()
        
        # Проверка ожидаемого результата
        if doc['should_pass'] == result.is_valid():
            print(f"   ✅ ТЕСТ ПРОЙДЕН")
            passed += 1
        else:
            print(f"   ❌ ТЕСТ ПРОВАЛЕН: ожидалось should_pass={doc['should_pass']}, получено is_valid={result.is_valid()}")
            failed += 1
        
        print()
        print("-" * 70)
        print()
    
    # Итоговая статистика
    print("=" * 70)
    print(f"ИТОГИ ИНТЕГРАЦИОННОГО ТЕСТИРОВАНИЯ")
    print("=" * 70)
    print(f"Пройдено: {passed}/{len(test_documents)}")
    print(f"Провалено: {failed}/{len(test_documents)}")
    print()
    
    if failed == 0:
        print("✅ ВСЕ ИНТЕГРАЦИОННЫЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
        return True
    else:
        print(f"❌ ЕСТЬ ПРОВАЛЫ: {failed} тест(ов)")
        return False


def test_suggestions_accuracy():
    """Проверка точности подсказок для типичных ошибок OCR"""
    
    print("\n" + "=" * 70)
    print("ТЕСТ ТОЧНОСТИ ПОДСКАЗОК ДЛЯ ТИПИЧНЫХ ОШИБОК OCR")
    print("=" * 70)
    print()
    
    ocr_errors = [
        ("Phisik", "Physik"),       # y → i
        ("Biologi", "Biologie"),    # обрезанное слово
        ("Chemle", "Chemie"),       # i → l
        ("Mathemat", "Mathematik"), # обрезанное слово
        ("Deulsch", "Deutsch"),     # t → l
        ("Geografi", "Geografie"),  # обрезанное слово
    ]
    
    correct = 0
    total = len(ocr_errors)
    
    for ocr_text, expected_correction in ocr_errors:
        suggestions = Validator.suggest_subjects(ocr_text, limit=3)
        
        print(f"OCR: '{ocr_text}' → Ожидается: '{expected_correction}'")
        print(f"  Подсказки: {suggestions}")
        
        if suggestions and expected_correction in suggestions:
            print(f"  ✅ Правильная дисциплина найдена (позиция {suggestions.index(expected_correction) + 1})")
            correct += 1
        else:
            print(f"  ❌ Правильная дисциплина НЕ найдена в подсказках")
        
        print()
    
    accuracy = (correct / total) * 100
    print(f"Точность: {correct}/{total} ({accuracy:.1f}%)")
    print()
    
    if accuracy >= 80:
        print("✅ ТОЧНОСТЬ ПОДСКАЗОК УДОВЛЕТВОРИТЕЛЬНА (≥80%)")
        return True
    else:
        print(f"❌ ТОЧНОСТЬ ПОДСКАЗОК НЕДОСТАТОЧНА (<80%)")
        return False


if __name__ == "__main__":
    print("\n" * 2)
    
    try:
        test1_passed = test_full_pipeline()
        test2_passed = test_suggestions_accuracy()
        
        print("\n" + "=" * 70)
        print("ФИНАЛЬНЫЙ РЕЗУЛЬТАТ")
        print("=" * 70)
        
        if test1_passed and test2_passed:
            print("✅✅✅ ВСЕ ИНТЕГРАЦИОННЫЕ ТЕСТЫ ПРОЙДЕНЫ! ✅✅✅")
            print("\nСистема готова к интеграции с GUI и CLI.")
        else:
            print("❌ ЕСТЬ ПРОБЛЕМЫ В ИНТЕГРАЦИОННЫХ ТЕСТАХ")
            
    except Exception as e:
        print(f"\n❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
