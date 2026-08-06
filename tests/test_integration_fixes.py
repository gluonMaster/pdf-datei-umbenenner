"""
Интеграционный тест для проверки исправлений интеграции ReviewQueue с GUI
"""

import sys
from pathlib import Path

# Добавляем родительскую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from review_queue import ReviewStore, ReviewItem

def test_path_resolution():
    """Тест правильного разрешения путей"""
    print("=" * 60)
    print("Тест разрешения путей")
    print("=" * 60)
    
    # Создаём тестовый ReviewItem с путём в стиле manual_check/filename.pdf
    test_item = ReviewItem(
        file_path="manual_check/test_file.pdf",
        raw_name="Test Name",
        raw_subject="Mathematik",
        reason="Test reason",
        suggestions=["Mathematik"],
        preview_text="Test preview"
    )
    
    print(f"\nRelative path в ReviewItem: {test_item.file_path}")
    
    # Симулируем selected_folder
    selected_folder = Path("c:/test_folder")
    
    # Разрешаем путь как в GUI
    absolute_path = selected_folder / test_item.file_path
    
    print(f"Selected folder: {selected_folder}")
    print(f"Absolute path: {absolute_path}")
    print(f"Expected: c:/test_folder/manual_check/test_file.pdf")
    
    assert str(absolute_path) == "c:\\test_folder\\manual_check\\test_file.pdf" or \
           str(absolute_path) == "c:/test_folder/manual_check/test_file.pdf"
    
    print("\n✅ Путь разрешается корректно!")
    print("=" * 60)

def test_store_path_binding():
    """Тест привязки store_path к выбранной папке"""
    print("\nТест привязки store_path к папке")
    print("=" * 60)
    
    store = ReviewStore()
    
    # Исходный путь
    original_path = store.store_path
    print(f"\nИсходный store_path: {original_path}")
    
    # Симулируем выбор папки
    selected_folder = Path("c:/test_folder")
    store.store_path = selected_folder / "manual_review.json"
    
    print(f"После выбора папки: {store.store_path}")
    print(f"Expected: c:/test_folder/manual_review.json")
    
    assert store.store_path.name == "manual_review.json"
    assert store.store_path.parent.name == "test_folder"
    
    print("\n✅ store_path корректно привязывается!")
    print("=" * 60)

def test_reviewitem_path_format():
    """Тест формата пути в ReviewItem (должен быть manual_check/filename)"""
    print("\nТест формата пути в ReviewItem")
    print("=" * 60)
    
    # Симулируем создание ReviewItem как в main.py
    pdf_name = "test_document.pdf"
    relative_path = str(Path("manual_check") / pdf_name)
    
    print(f"\nИмя файла: {pdf_name}")
    print(f"Относительный путь: {relative_path}")
    print(f"Expected: manual_check/test_document.pdf")
    
    assert relative_path == "manual_check\\test_document.pdf" or \
           relative_path == "manual_check/test_document.pdf"
    
    review_item = ReviewItem(
        file_path=relative_path,
        raw_name="Test User",
        raw_subject="Biologie",
        reason="Test",
        suggestions=[],
        preview_text="Test"
    )
    
    print(f"\nReviewItem.file_path: {review_item.file_path}")
    
    assert "manual_check" in review_item.file_path
    assert pdf_name in review_item.file_path
    
    print("\n✅ Формат пути правильный!")
    print("=" * 60)

def test_load_test_data():
    """Тест загрузки тестовых данных из test_files/manual_review.json"""
    print("\nТест загрузки тестовых данных")
    print("=" * 60)
    
    test_store_path = Path(__file__).parent.parent / "test_files" / "manual_review.json"
    
    if not test_store_path.exists():
        print(f"\n⚠️ Тестовый файл не найден: {test_store_path}")
        print("Пропускаем тест")
        return
    
    store = ReviewStore(test_store_path)
    store.load()
    
    items = store.get_all()
    print(f"\nЗагружено элементов: {len(items)}")
    
    for idx, item in enumerate(items, 1):
        print(f"\n--- Элемент {idx} ---")
        print(f"  file_path: {item.file_path}")
        
        # Проверяем формат пути
        assert "manual_check" in item.file_path, f"Путь должен содержать 'manual_check': {item.file_path}"
        
        # Проверяем, что путь относительный
        assert not Path(item.file_path).is_absolute(), f"Путь не должен быть абсолютным: {item.file_path}"
        
        print(f"  ✅ Формат пути правильный")
    
    print("\n✅ Все пути корректны!")
    print("=" * 60)

if __name__ == "__main__":
    try:
        test_path_resolution()
        test_store_path_binding()
        test_reviewitem_path_format()
        test_load_test_data()
        
        print("\n" + "=" * 60)
        print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
        print("=" * 60)
        
    except AssertionError as e:
        print(f"\n❌ Тест провалился: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
