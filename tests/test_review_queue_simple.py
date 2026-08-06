"""
Простой интеграционный тест для проверки работы review_queue
Запускается без pytest
"""

import tempfile
from pathlib import Path
import sys

# Добавляем родительскую папку в путь для импорта
sys.path.insert(0, str(Path(__file__).parent.parent))

from review_queue import ReviewItem, ReviewStore


def test_basic_functionality():
    """Базовый тест функциональности"""
    print("Тест 1: Создание ReviewItem...")
    item = ReviewItem(
        file_path="test.pdf",
        raw_name="Max Mustermann",
        raw_subject="Mathematik",
        reason="Unbekanntes Fach",
        suggestions=["Mathe", "Physik"],
        preview_text="Name: Max Mustermann\nFach: Mathematik"
    )
    
    assert item.file_path == "test.pdf"
    assert item.raw_name == "Max Mustermann"
    assert len(item.suggestions) == 2
    print("  ✓ ReviewItem создан успешно")
    
    print("\nТест 2: Преобразование в словарь и обратно...")
    item_dict = item.to_dict()
    assert isinstance(item_dict, dict)
    
    item_restored = ReviewItem.from_dict(item_dict)
    assert item_restored.file_path == item.file_path
    assert item_restored.raw_name == item.raw_name
    print("  ✓ Преобразование работает корректно")
    
    print("\nТест 3: Работа с ReviewStore...")
    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "test_review.json"
        store = ReviewStore(store_path)
        
        assert store.count() == 0
        print("  ✓ Хранилище создано")
        
        # Добавляем элементы
        item1 = ReviewItem(
            file_path="file1.pdf",
            raw_name="Anna Schmidt",
            raw_subject="Biologie",
            reason="Name zu kurz",
            suggestions=[],
            preview_text="Test text 1"
        )
        
        item2 = ReviewItem(
            file_path="file2.pdf",
            raw_name="Peter Mueller",
            raw_subject="Chemie",
            reason="Unbekanntes Fach",
            suggestions=["Chemie", "Physik"],
            preview_text="Test text 2"
        )
        
        store.add(item1)
        store.add(item2)
        
        assert store.count() == 2
        print("  ✓ Элементы добавлены (count: 2)")
        
        # Сохраняем
        success = store.save()
        assert success
        assert store_path.exists()
        print(f"  ✓ Файл сохранен: {store_path}")
        
        # Загружаем в новое хранилище
        store2 = ReviewStore(store_path)
        store2.load()
        
        assert store2.count() == 2
        print("  ✓ Данные загружены из файла")
        
        items = store2.get_all()
        assert len(items) == 2
        assert items[0].file_path == "file1.pdf"
        assert items[1].file_path == "file2.pdf"
        print("  ✓ Все элементы восстановлены корректно")
        
        # Проверяем содержимое файла
        import json
        with open(store_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        assert 'review_items' in data
        assert len(data['review_items']) == 2
        print("  ✓ Структура JSON корректна")
        
        # Тестируем удаление
        removed = store2.remove("file1.pdf")
        assert removed is True
        assert store2.count() == 1
        print("  ✓ Удаление элемента работает")
        
        # Очистка
        store2.clear()
        assert store2.count() == 0
        print("  ✓ Очистка работает")
    
    print("\n" + "="*50)
    print("Все тесты пройдены успешно! ✓")
    print("="*50)


if __name__ == '__main__':
    try:
        test_basic_functionality()
    except AssertionError as e:
        print(f"\n✗ Тест провален: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
