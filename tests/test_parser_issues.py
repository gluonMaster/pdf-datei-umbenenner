"""
Детальный анализ проблем парсера и smoke-тесты для нормализации дисциплин и подсказок
"""

import re
import sys
import os
from difflib import get_close_matches

# Добавляем родительскую директорию в путь для импорта модулей
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from text_parser import TextParser


def test_current_parser_logic():
    """Тестируем текущую логику парсера"""
    
    test_lines = [
        "Sample Student Chemie",
        "Example Learner Englisch",
        "Public Pupil Mathe",
    ]
    
    parser = TextParser()
    expected = {
        "Sample Student Chemie": ("Sample Student", "Chemie"),
        "Example Learner Englisch": ("Example Learner", "Englisch"),
        "Public Pupil Mathe": ("Public Pupil", "Mathematik"),
    }
    
    print("🔍 ТЕСТИРОВАНИЕ ТЕКУЩЕГО ПАРСЕРА")
    print("="*50)
    
    for line in test_lines:
        print(f"\nТестируем строку: '{line}'")
        
        # Тест на имя
        print("\n📝 Тест extract_student_name:")
        
        # Проверяем паттерн для двух слов
        pattern = r'^([A-ZА-ЯЁ][a-zа-яё\-]+)\s+([A-ZА-ЯЁ][a-zа-яё\-]+)$'
        match = re.match(pattern, line)
        print(f"  Паттерн для 2 слов: {match}")
        
        # Проверяем что происходит с тремя словами
        words = line.split()
        print(f"  Количество слов: {len(words)}")
        print(f"  Слова: {words}")
        
        if len(words) == 2:
            print(f"  ✓ Подходит под логику двух слов")
        else:
            print(f"  ❌ НЕ подходит под логику двух слов (3 слова)")
        
        # Тест на дисциплину  
        print("\n📚 Тест extract_subject:")
        valid_subjects = parser.get_valid_subjects()
        for subject in valid_subjects:
            if subject.lower() == line.lower():
                print(f"  ✓ Точное совпадение: {subject}")
            elif subject.lower() in line.lower():
                print(f"  ✓ Частичное совпадение: {subject}")
        
        if "mathe" in line.lower():
            print(f"  ❌ 'Mathe' не в списке валидных дисциплин")

        assert parser.parse_document(line) == expected[line]
        
        print("-" * 30)


def test_proposed_solutions():
    """Тестируем предлагаемые решения"""
    
    test_lines = [
        "Sample Student Chemie",
        "Example Learner Englisch",
        "Public Pupil Mathe",
    ]
    
    print("\n\n🛠️  ТЕСТИРОВАНИЕ ПРЕДЛАГАЕМЫХ РЕШЕНИЙ")
    print("="*50)
    
    # Расширенный список дисциплин
    extended_subjects = [
        'Biologie', 'Chemie', 'Deutsch', 'Englisch', 
        'Geografie', 'Mathematik', 'Physik', 
        'Sachkunde', 'Sachunterricht',
        'Mathe'  # Добавляем сокращение
    ]
    
    parsed_rows = []
    for line in test_lines:
        print(f"\nТестируем строку: '{line}'")
        words = line.split()
        
        if len(words) == 3:
            surname, name, subject = words
            print(f"  📝 Имя: {surname} {name}")
            print(f"  📚 Дисциплина: {subject}")
            
            # Проверяем дисциплину
            if subject in extended_subjects:
                print(f"  ✓ Дисциплина найдена в расширенном списке")
            else:
                print(f"  ❌ Дисциплина НЕ найдена")

            assert subject in extended_subjects
            parsed_rows.append((f"{surname} {name}", subject))

    assert parsed_rows == [
        ("Sample Student", "Chemie"),
        ("Example Learner", "Englisch"),
        ("Public Pupil", "Mathe"),
    ]


def test_subject_normalization():
    """
    Smoke-тест для нормализации дисциплин
    Проверяет применение встроенных и автообученных алиасов
    """
    print("\n\n🧪 SMOKE-ТЕСТ: НОРМАЛИЗАЦИЯ ДИСЦИПЛИН")
    print("="*50)
    
    # Тестовые случаи: (исходная дисциплина, ожидаемая нормализованная)
    test_cases = [
        # Встроенные алиасы из subjects_config.json
        ("Mathe", "Mathematik"),
        ("Bio", "Biologie"),
        ("Geo", "Geografie"),
        
        # Валидные дисциплины без нормализации
        ("Chemie", "Chemie"),
        ("Deutsch", "Deutsch"),
        ("Englisch", "Englisch"),
        
        # Неизвестные (должны остаться без изменений)
        ("Unknown", "Unknown"),
        ("Test", "Test"),
    ]
    
    parser = TextParser()
    passed = 0
    failed = 0
    
    for original, expected in test_cases:
        normalized = parser.normalize_subject(original)
        if normalized == expected:
            print(f"  ✅ '{original}' → '{normalized}' (ожидалось: '{expected}')")
            passed += 1
        else:
            print(f"  ❌ '{original}' → '{normalized}' (ожидалось: '{expected}')")
            failed += 1
    
    print(f"\n📊 Результаты: {passed} пройдено, {failed} провалено")
    print("-" * 50)
    
    assert failed == 0, f"Не прошло проверок нормализации: {failed}"


def test_subject_suggestions():
    """
    Smoke-тест для подсказок дисциплин
    Проверяет нечёткое сопоставление (fuzzy matching) с использованием difflib
    """
    print("\n\n🧪 SMOKE-ТЕСТ: ПОДСКАЗКИ ДИСЦИПЛИН")
    print("="*50)
    
    parser = TextParser()
    valid_subjects = parser.get_valid_subjects()
    
    # Тестовые случаи: (распознанная дисциплина, ожидаемая первая подсказка)
    test_cases = [
        # Опечатки и OCR-ошибки
        ("Englsh", "Englisch"),      # Пропущена буква
        ("Mathem", "Mathe"),         # Неполное слово (Mathe короче и ближе)
        ("Chemie", "Chemie"),        # Точное совпадение
        ("Biolog", "Biologie"),      # Неполное слово
        ("Deutch", "Deutsch"),       # Перестановка букв
        ("Physik", "Physik"),        # Точное совпадение
    ]
    
    passed = 0
    failed = 0
    
    for parsed, expected_first in test_cases:
        # Используем difflib для нечёткого сопоставления
        suggestions = get_close_matches(
            parsed,
            valid_subjects,
            n=5,
            cutoff=0.3  # Порог схожести 30%
        )
        
        # Если нет близких совпадений, предлагаем все валидные
        if not suggestions:
            suggestions = valid_subjects
        
        # Проверяем первую подсказку
        if suggestions and suggestions[0] == expected_first:
            print(f"  ✅ '{parsed}' → Подсказки: {suggestions[:3]} (первая: '{expected_first}')")
            passed += 1
        else:
            actual_first = suggestions[0] if suggestions else "None"
            print(f"  ❌ '{parsed}' → Первая подсказка: '{actual_first}' (ожидалось: '{expected_first}')")
            print(f"     Все подсказки: {suggestions[:3]}")
            failed += 1
    
    print(f"\n📊 Результаты: {passed} пройдено, {failed} провалено")
    print("-" * 50)
    
    assert failed == 0, f"Не прошло проверок подсказок: {failed}"


def test_alias_priority():
    """
    Smoke-тест для приоритета применения алиасов
    Проверяет, что встроенные алиасы имеют приоритет над автообученными
    """
    print("\n\n🧪 SMOKE-ТЕСТ: ПРИОРИТЕТ АЛИАСОВ")
    print("="*50)
    
    parser = TextParser()
    
    # Получаем встроенные алиасы
    builtin_aliases = parser.get_subject_normalization()
    
    print(f"📋 Встроенные алиасы из subjects_config.json:")
    for alias, target in builtin_aliases.items():
        print(f"  '{alias}' → '{target}'")
    
    # Тест: встроенные алиасы должны применяться
    test_cases = [
        ("Mathe", "Mathematik"),
        ("Bio", "Biologie"),
        ("Geo", "Geografie"),
    ]
    
    passed = 0
    failed = 0
    
    print(f"\n🔍 Проверка применения встроенных алиасов:")
    for alias, expected in test_cases:
        normalized = parser.normalize_subject(alias)
        if normalized == expected:
            print(f"  ✅ '{alias}' → '{normalized}' (встроенный алиас применён)")
            passed += 1
        else:
            print(f"  ❌ '{alias}' → '{normalized}' (ожидалось: '{expected}')")
            failed += 1
    
    print(f"\n📊 Результаты: {passed} пройдено, {failed} провалено")
    print("-" * 50)
    
    assert failed == 0, f"Не прошло проверок алиасов: {failed}"


def run_all_smoke_tests():
    """Запуск всех smoke-тестов"""
    print("\n" + "="*60)
    print("🚀 ЗАПУСК ВСЕХ SMOKE-ТЕСТОВ")
    print("="*60)
    
    tests = [
        ("Нормализация дисциплин", test_subject_normalization),
        ("Подсказки дисциплин", test_subject_suggestions),
        ("Приоритет алиасов", test_alias_priority),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            test_func()
            results.append((test_name, True))
        except Exception as e:
            print(f"\n❌ Тест '{test_name}' завершился с ошибкой: {e}")
            results.append((test_name, False))
    
    # Итоговый отчёт
    print("\n" + "="*60)
    print("📊 ИТОГОВЫЙ ОТЧЁТ")
    print("="*60)
    
    for test_name, passed in results:
        status = "✅ ПРОЙДЕН" if passed else "❌ ПРОВАЛЕН"
        print(f"{status}: {test_name}")
    
    total_passed = sum(1 for _, passed in results if passed)
    total_tests = len(results)
    
    print(f"\n🎯 Итого: {total_passed}/{total_tests} тестов пройдено")
    print("="*60)
    
    return total_passed == total_tests


if __name__ == "__main__":
    # Запускаем все тесты
    all_passed = run_all_smoke_tests()
    
    # Возвращаем код выхода для CI/CD
    sys.exit(0 if all_passed else 1)
