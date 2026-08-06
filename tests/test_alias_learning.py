"""
Тесты для автообучения алиасов дисциплин
"""

import pytest
from pathlib import Path
import json

from alias_manager import AliasManager
from text_parser import TextParser


def test_alias_manager_basic(tmpdir):
    """Тест базовой функциональности AliasManager"""
    print("\nТест базовой функциональности AliasManager")
    
    # Создаём временный файл алиасов
    alias_file = Path(tmpdir) / "test_aliases.json"
    manager = AliasManager(alias_file)
    
    # Добавляем алиас
    assert manager.add_alias("Mathmatik", "Mathematik"), "Должен добавить алиас"
    assert manager.count() == 1, "Должен быть 1 алиас"
    
    # Применяем алиас
    assert manager.apply_alias("Mathmatik") == "Mathematik", "Должен применить алиас"
    assert manager.apply_alias("Mathematik") == "Mathematik", "Должен вернуть исходное"
    
    # Сохраняем
    assert manager.save(), "Должен сохранить алиасы"
    
    # Проверяем файл
    assert alias_file.exists(), "Файл алиасов должен существовать"
    
    with open(alias_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
        assert "Mathmatik" in data["aliases"], "Алиас должен быть в файле"
        assert data["aliases"]["Mathmatik"] == "Mathematik"
    
    print("  ✅ Базовая функциональность работает")


def test_alias_manager_case_insensitive(tmpdir):
    """Тест применения алиасов без учета регистра"""
    print("\nТест применения алиасов без учета регистра")
    
    alias_file = Path(tmpdir) / "test_aliases.json"
    manager = AliasManager(alias_file)
    
    manager.add_alias("Physicks", "Physik")
    manager.save()
    
    # Проверяем разные варианты регистра
    assert manager.apply_alias("Physicks") == "Physik"
    assert manager.apply_alias("physicks") == "Physik", "Должен работать без учета регистра"
    assert manager.apply_alias("PHYSICKS") == "Physik", "Должен работать без учета регистра"
    
    print("  ✅ Работа без учета регистра корректна")


def test_alias_manager_persistence(tmpdir):
    """Тест сохранения и загрузки алиасов"""
    print("\nТест сохранения и загрузки алиасов")
    
    alias_file = Path(tmpdir) / "test_aliases.json"
    
    # Создаём и сохраняем алиасы
    manager1 = AliasManager(alias_file)
    manager1.add_alias("Mahtematik", "Mathematik")
    manager1.add_alias("Biologi", "Biologie")
    manager1.save()
    
    # Загружаем в новый экземпляр
    manager2 = AliasManager(alias_file)
    assert manager2.count() == 2, "Должно быть 2 алиаса"
    assert manager2.apply_alias("Mahtematik") == "Mathematik"
    assert manager2.apply_alias("Biologi") == "Biologie"
    
    print("  ✅ Сохранение и загрузка работают")


def test_alias_integration_with_parser(tmpdir):
    """Тест интеграции алиасов с TextParser"""
    print("\nТест интеграции алиасов с TextParser")
    
    # Создаём тестовый файл алиасов
    alias_file = Path(tmpdir) / "subjects_aliases.json"
    alias_file.write_text('{"aliases": {"Mathmatik": "Mathematik"}}', encoding='utf-8')
    
    # Подменяем путь к файлу алиасов в TextParser
    original_manager = TextParser._alias_manager
    TextParser._alias_manager = AliasManager(alias_file)
    
    try:
        # Проверяем, что алиас применяется
        normalized = TextParser.normalize_subject("Mathmatik")
        assert normalized == "Mathematik", f"Ожидали 'Mathematik', получили '{normalized}'"
        
        print("  ✅ Интеграция с TextParser работает")
    finally:
        # Восстанавливаем исходный менеджер
        TextParser._alias_manager = original_manager


def test_auto_learning_workflow(tmpdir):
    """Тест сценария автообучения: распознавание → исправление → повторное распознавание"""
    print("\nТест сценария автообучения")
    
    alias_file = Path(tmpdir) / "subjects_aliases.json"
    manager = AliasManager(alias_file)
    
    # Симулируем первое распознавание с ошибкой
    raw_subject_1 = "Mathmatik"  # Ошибка OCR
    print(f"  Первое распознавание: '{raw_subject_1}'")
    
    # Применяем алиас (пока пустой)
    normalized_1 = manager.apply_alias(raw_subject_1)
    assert normalized_1 == "Mathmatik", "Алиас еще не обучен, должен вернуть исходное"
    print(f"  → Нормализация: '{normalized_1}' (алиас не найден)")
    
    # Оператор вручную исправляет на "Mathematik"
    corrected_subject = "Mathematik"
    print(f"  Оператор исправил на: '{corrected_subject}'")
    
    # Сохраняем алиас (автообучение)
    manager.add_alias(raw_subject_1, corrected_subject)
    manager.save()
    print(f"  📚 Алиас сохранен: '{raw_subject_1}' → '{corrected_subject}'")
    
    # Симулируем второе распознавание той же ошибки
    raw_subject_2 = "Mathmatik"  # Та же ошибка OCR
    print(f"  Второе распознавание: '{raw_subject_2}'")
    
    # Применяем алиас (теперь обучен)
    normalized_2 = manager.apply_alias(raw_subject_2)
    assert normalized_2 == "Mathematik", "Алиас обучен, должен вернуть правильное название"
    print(f"  → Нормализация: '{normalized_2}' ✅ (алиас применен автоматически)")
    
    print("  ✅ Автообучение работает: вторая ошибка исправлена автоматически")


def test_multiple_aliases_same_target(tmpdir):
    """Тест нескольких алиасов для одной дисциплины"""
    print("\nТест нескольких алиасов для одной дисциплины")
    
    alias_file = Path(tmpdir) / "test_aliases.json"
    manager = AliasManager(alias_file)
    
    # Добавляем несколько вариантов ошибок для "Mathematik"
    manager.add_alias("Mathmatik", "Mathematik")
    manager.add_alias("Matematik", "Mathematik")
    manager.add_alias("Mathe", "Mathematik")
    
    assert manager.count() == 3, "Должно быть 3 алиаса"
    
    # Проверяем, что все варианты нормализуются правильно
    assert manager.apply_alias("Mathmatik") == "Mathematik"
    assert manager.apply_alias("Matematik") == "Mathematik"
    assert manager.apply_alias("Mathe") == "Mathematik"
    
    print("  ✅ Несколько алиасов для одной дисциплины работают")


def test_alias_ignore_duplicates(tmpdir):
    """Тест игнорирования дубликатов и идентичных пар"""
    print("\nТест игнорирования дубликатов")
    
    alias_file = Path(tmpdir) / "test_aliases.json"
    manager = AliasManager(alias_file)
    
    # Добавляем алиас
    assert manager.add_alias("Mathmatik", "Mathematik"), "Должен добавить"
    assert manager.count() == 1
    
    # Пытаемся добавить ту же пару
    assert manager.add_alias("Mathmatik", "Mathematik"), "Должен обновить (не ошибка)"
    assert manager.count() == 1, "Не должно быть дубликатов"
    
    # Пытаемся добавить идентичную пару (исходная == целевая)
    assert not manager.add_alias("Mathematik", "Mathematik"), "Не должен добавлять идентичные"
    assert manager.count() == 1
    
    print("  ✅ Дубликаты корректно игнорируются")


def test_remove_alias(tmpdir):
    """Тест удаления алиаса"""
    print("\nТест удаления алиаса")
    
    alias_file = Path(tmpdir) / "test_aliases.json"
    manager = AliasManager(alias_file)
    
    manager.add_alias("Mathmatik", "Mathematik")
    assert manager.count() == 1
    
    # Удаляем алиас
    assert manager.remove_alias("Mathmatik"), "Должен удалить"
    assert manager.count() == 0, "Алиас должен быть удален"
    
    # Пытаемся удалить несуществующий
    assert not manager.remove_alias("Nonexistent"), "Не должен удалять несуществующий"
    
    print("  ✅ Удаление алиасов работает")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
