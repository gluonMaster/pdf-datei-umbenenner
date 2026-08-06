"""
Тестовый скрипт для проверки функциональности вкладки "Prüfung"
"""

import sys
from pathlib import Path

# Добавляем родительскую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from review_queue import ReviewStore, ReviewItem

def test_review_queue():
    """Тест базовой функциональности ReviewStore"""
    print("=" * 60)
    print("Тест ReviewStore")
    print("=" * 60)
    
    # Создать тестовый store
    store = ReviewStore(Path(__file__).parent.parent / "manual_review.json")
    
    # Загрузить данные
    store.load()
    
    items = store.get_all()
    print(f"\nЗагружено элементов: {len(items)}")
    
    for idx, item in enumerate(items, 1):
        print(f"\n--- Элемент {idx} ---")
        print(f"Файл: {Path(item.file_path).name}")
        print(f"Имя: {item.raw_name}")
        print(f"Дисциплина: {item.raw_subject}")
        print(f"Причина: {item.reason}")
        print(f"Подсказки: {', '.join(item.suggestions) if item.suggestions else 'нет'}")
        print(f"Превью: {item.preview_text[:50]}...")
    
    print("\n" + "=" * 60)
    print("Тест завершен успешно!")
    print("=" * 60)

if __name__ == "__main__":
    test_review_queue()
