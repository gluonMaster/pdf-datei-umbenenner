"""
Интеграционный тест: проверка создания manual_review.json при обработке проблемных файлов
"""

import sys
import tempfile
import json
from pathlib import Path

# Добавляем родительскую папку в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from review_queue import ReviewStore
from validator import Validator, ValidationLevel


def test_validation_with_suggestions():
    """Проверяем, что валидатор генерирует подсказки"""
    print("Тест: Валидатор с неизвестной дисциплиной...")
    
    # Тестируем с неизвестной дисциплиной, похожей на "Mathematik"
    result = Validator.validate_subject("Matematik")
    
    print(f"  Уровень валидации: {result.level}")
    print(f"  Сообщение: {result.message}")
    print(f"  Подсказки: {result.suggestions}")
    
    assert result.level == ValidationLevel.MANUAL_CHECK
    assert len(result.suggestions) > 0
    assert "Mathematik" in result.suggestions or "Mathe" in result.suggestions
    
    print("  ✓ Валидатор корректно генерирует подсказки\n")


def test_review_item_with_suggestions():
    """Проверяем, что ReviewItem сохраняет подсказки"""
    print("Тест: ReviewItem содержит подсказки из валидации...")
    
    from review_queue import ReviewItem
    
    # Получаем подсказки от валидатора
    validation_result = Validator.validate_subject("Matematik")
    
    # Создаем ReviewItem с этими подсказками
    item = ReviewItem(
        file_path="test.pdf",
        raw_name="Max Mustermann",
        raw_subject="Matematik",
        reason=validation_result.message,
        suggestions=validation_result.suggestions,
        preview_text="Sample text"
    )
    
    print(f"  Подсказки в ReviewItem: {item.suggestions}")
    
    assert len(item.suggestions) > 0
    print("  ✓ ReviewItem сохраняет подсказки\n")


def test_full_workflow():
    """Полный тест: валидация → ReviewItem → сохранение → загрузка"""
    print("Тест: Полный workflow с подсказками...")
    
    from review_queue import ReviewItem, ReviewStore
    
    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "manual_review.json"
        store = ReviewStore(store_path)
        
        # Симулируем проблемный файл с неизвестной дисциплиной
        validation_result = Validator.validate_subject("Biologia")
        
        item = ReviewItem(
            file_path="problematic.pdf",
            raw_name="Anna Schmidt",
            raw_subject="Biologia",
            reason=validation_result.message,
            suggestions=validation_result.suggestions,
            preview_text="Name: Anna Schmidt\nFach: Biologia\n..."
        )
        
        store.add(item)
        store.save()
        
        print(f"  Файл сохранен: {store_path}")
        
        # Загружаем и проверяем
        store2 = ReviewStore(store_path)
        store2.load()
        
        assert store2.count() == 1
        loaded_item = store2.get_all()[0]
        
        print(f"  Загружено элементов: {store2.count()}")
        print(f"  Файл: {loaded_item.file_path}")
        print(f"  Проблемная дисциплина: {loaded_item.raw_subject}")
        print(f"  Причина: {loaded_item.reason}")
        print(f"  Подсказки: {loaded_item.suggestions}")
        
        assert len(loaded_item.suggestions) > 0
        assert "Biologie" in loaded_item.suggestions or "Bio" in loaded_item.suggestions
        
        # Проверяем формат JSON
        with open(store_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        print(f"\nСодержимое JSON файла:")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        
        assert 'review_items' in data
        assert data['review_items'][0]['suggestions'] == loaded_item.suggestions
        
        print("\n  ✓ Полный workflow работает корректно")


def test_multiple_issues():
    """Тест с несколькими проблемными файлами"""
    print("\nТест: Множественные проблемные файлы...")
    
    from review_queue import ReviewItem, ReviewStore
    
    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "manual_review.json"
        store = ReviewStore(store_path)
        
        # Разные типы проблем
        issues = [
            ("file1.pdf", "M", "Mathematik", "Name zu kurz: M"),
            ("file2.pdf", "Max Mustermann", "Mathematic", "Unbekanntes Fach: Mathematic"),
            ("file3.pdf", "Anna123 Schmidt", None, "Name enthaelt Zahlen: Anna123 Schmidt"),
        ]
        
        for file_path, name, subject, reason in issues:
            # Получаем подсказки если это проблема с дисциплиной
            suggestions = []
            if subject and "Unbekanntes Fach" in reason:
                val_result = Validator.validate_subject(subject)
                suggestions = val_result.suggestions
            
            item = ReviewItem(
                file_path=file_path,
                raw_name=name,
                raw_subject=subject,
                reason=reason,
                suggestions=suggestions,
                preview_text=f"Test text for {file_path}"
            )
            store.add(item)
        
        store.save()
        
        # Загружаем и проверяем
        store2 = ReviewStore(store_path)
        store2.load()
        
        print(f"  Всего проблемных файлов: {store2.count()}")
        
        for idx, item in enumerate(store2.get_all(), 1):
            print(f"\n  Файл {idx}: {item.file_path}")
            print(f"    Причина: {item.reason}")
            if item.suggestions:
                print(f"    Подсказки: {item.suggestions}")
        
        assert store2.count() == 3
        print("\n  ✓ Множественные файлы обрабатываются корректно")


if __name__ == '__main__':
    print("="*60)
    print("Интеграционные тесты review_queue + validator")
    print("="*60)
    print()
    
    try:
        test_validation_with_suggestions()
        test_review_item_with_suggestions()
        test_full_workflow()
        test_multiple_issues()
        
        print("\n" + "="*60)
        print("ВСЕ ИНТЕГРАЦИОННЫЕ ТЕСТЫ ПРОЙДЕНЫ! ✓")
        print("="*60)
        
    except AssertionError as e:
        print(f"\n✗ Тест провален: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
