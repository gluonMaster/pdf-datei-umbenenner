"""
Тестовый скрипт для проверки функциональности подсказок дисциплин
"""

from validator import Validator, ValidationLevel


def test_suggest_subjects():
    """Проверка метода suggest_subjects()"""
    print("=== Тест метода suggest_subjects() ===\n")
    
    test_cases = [
        ("Mathemat", "Частичное совпадение"),
        ("Phisik", "Опечатка в Physik"),
        ("Chemie", "Точное совпадение (не должно выдавать подсказки в валидации)"),
        ("Biologi", "Частичное совпадение"),
        ("Geschichte", "Полностью неизвестная дисциплина"),
        ("Englis", "Частичное совпадение"),
        ("Math", "Короткое сокращение"),
        ("Geogr", "Частичное совпадение"),
    ]
    
    for subject, description in test_cases:
        suggestions = Validator.suggest_subjects(subject, limit=5)
        print(f"Ввод: '{subject}' ({description})")
        if suggestions:
            print(f"  Подсказки: {', '.join(suggestions)}")
        else:
            print(f"  Подсказки: нет близких совпадений")
        print()


def test_validate_subject_with_suggestions():
    """Проверка валидации с подсказками"""
    print("=== Тест валидации дисциплин с подсказками ===\n")
    
    test_cases = [
        ("Mathematik", True, "Валидная дисциплина"),
        ("Phisik", False, "Опечатка в Physik"),
        ("Geschichte", False, "Неизвестная дисциплина"),
        ("Mathe", True, "Валидный алиас"),
        ("Chemie", True, "Валидная дисциплина"),
        ("Chemi", False, "Частичное совпадение"),
        ("Biologi", False, "Частичное совпадение"),
    ]
    
    for subject, should_be_valid, description in test_cases:
        result = Validator.validate_subject(subject)
        
        print(f"Дисциплина: '{subject}' ({description})")
        print(f"  Статус: {result.level.value}")
        print(f"  Сообщение: {result.message}")
        
        if result.suggestions:
            print(f"  Подсказки ({len(result.suggestions)}): {', '.join(result.suggestions)}")
        else:
            print(f"  Подсказки: нет")
        
        # Проверка ожидаемого результата
        if should_be_valid:
            assert result.is_valid(), f"'{subject}' должен быть валидным!"
        else:
            assert not result.is_valid(), f"'{subject}' не должен быть валидным!"
            assert result.level == ValidationLevel.MANUAL_CHECK, "Должен требовать ручной проверки"
        
        print()


def test_validate_document_data_with_suggestions():
    """Проверка валидации документа с подсказками"""
    print("=== Тест валидации полного документа ===\n")
    
    test_cases = [
        ("Mueller Anna", "Mathematik", True, "Валидные данные"),
        ("Schmidt Max", "Phisik", False, "Опечатка в дисциплине"),
        ("Weber Lisa", "Geschichte", False, "Неизвестная дисциплина"),
        ("Becker Tom", "Mathe", True, "Валидный алиас"),
    ]
    
    for name, subject, should_be_valid, description in test_cases:
        result = Validator.validate_document_data(name, subject)
        
        print(f"Документ: '{name}' — '{subject}' ({description})")
        print(f"  Статус: {result.level.value}")
        print(f"  Сообщение: {result.message}")
        
        if result.suggestions:
            print(f"  Подсказки: {', '.join(result.suggestions)}")
        else:
            print(f"  Подсказки: нет")
        
        if should_be_valid:
            assert result.is_valid(), f"Документ должен быть валидным!"
        else:
            assert not result.is_valid(), f"Документ не должен быть валидным!"
        
        print()


def test_cutoff_threshold():
    """Проверка порога схожести (cutoff)"""
    print("=== Тест порога схожести ===\n")
    
    # Очень отдаленные совпадения не должны попадать в подсказки
    distant_matches = [
        "xyz",
        "abc",
        "test",
        "12345",
    ]
    
    for subject in distant_matches:
        suggestions = Validator.suggest_subjects(subject, limit=5)
        print(f"Ввод: '{subject}'")
        print(f"  Подсказки: {suggestions if suggestions else 'нет (cutoff не пройден)'}")
        print()


if __name__ == "__main__":
    try:
        test_suggest_subjects()
        test_validate_subject_with_suggestions()
        test_validate_document_data_with_suggestions()
        test_cutoff_threshold()
        
        print("✓✓✓ Все тесты пройдены успешно! ✓✓✓")
    except AssertionError as e:
        print(f"\n✗ Ошибка теста: {e}")
        import traceback
        traceback.print_exc()
    except Exception as e:
        print(f"\n✗ Неожиданная ошибка: {e}")
        import traceback
        traceback.print_exc()
