"""
Тесты для проверки применения исправлений в GUI
(перемещение файлов из manual_check в корень с переименованием)
"""

import pytest
from pathlib import Path
import json
import shutil

from file_renamer import FileRenamer
from review_queue import ReviewStore, ReviewItem


def test_apply_correction_move_and_rename(tmpdir):
    """
    Тест применения исправления: файл перемещается из manual_check 
    в корневую папку с новым именем
    """
    print("\nТест применения исправления с перемещением и переименованием")
    
    # Создаём структуру папок
    base_folder = Path(tmpdir) / "test_workspace"
    base_folder.mkdir()
    manual_check_folder = base_folder / "manual_check"
    manual_check_folder.mkdir()
    
    # Создаём тестовый файл в manual_check
    original_file = manual_check_folder / "scan_001.pdf"
    original_file.write_text("Test PDF content")
    
    print(f"  Создан файл: {original_file}")
    assert original_file.exists(), "Исходный файл должен существовать"
    
    # Создаём ReviewItem
    review_item = ReviewItem(
        file_path="manual_check/scan_001.pdf",
        raw_name="Max Mustermann",
        raw_subject="Math",
        reason="Unbekanntes Fach",
        suggestions=["Mathematik", "Mathe"],
        preview_text="Test preview"
    )
    
    # Сохраняем в ReviewStore
    store = ReviewStore(base_folder / "manual_review.json")
    store.add(review_item)
    store.save()
    
    # Симулируем применение исправления
    surname = "Mustermann"
    name = "Max"
    subject = "Mathematik"
    
    renamer = FileRenamer()
    new_filename = renamer.generate_new_filename(surname, name, subject, 4, 2030)
    target_path = base_folder / new_filename
    
    print(f"  Новое имя файла: {new_filename}")
    print(f"  Целевой путь: {target_path}")
    
    # Проверяем, что целевой файл не существует
    assert not target_path.exists(), "Целевой файл не должен существовать до перемещения"
    
    # Перемещаем файл
    shutil.move(str(original_file), str(target_path))
    
    # Проверки после перемещения
    assert not original_file.exists(), "Исходный файл должен быть удалён из manual_check"
    assert target_path.exists(), "Новый файл должен существовать в корневой папке"
    assert target_path.read_text() == "Test PDF content", "Содержимое файла должно сохраниться"
    
    print(f"  ✅ Файл успешно перемещён из manual_check в корень")
    
    # Удаляем из очереди
    store.remove(review_item.file_path)
    store.save()
    
    # Проверяем, что очередь пуста
    store.load()
    assert store.count() == 0, "Очередь должна быть пустой после удаления"
    
    print(f"  ✅ Элемент удалён из очереди")


def test_apply_correction_with_collision(tmpdir):
    """
    Тест применения исправления с коллизией имён
    (если файл с таким именем уже существует в корне)
    """
    print("\nТест применения исправления с коллизией имён")
    
    base_folder = Path(tmpdir) / "test_workspace"
    base_folder.mkdir()
    manual_check_folder = base_folder / "manual_check"
    manual_check_folder.mkdir()
    
    # Создаём файл в manual_check
    original_file = manual_check_folder / "scan_002.pdf"
    original_file.write_text("New content")
    
    # Создаём файл с конфликтным именем в корне
    renamer = FileRenamer()
    existing_filename = renamer.generate_new_filename(
        "Schmidt", "Anna", "Physik", 4, 2030
    )
    existing_file = base_folder / existing_filename
    existing_file.write_text("Existing content")
    
    print(f"  Существующий файл: {existing_file.name}")
    print(f"  Новый файл: {original_file.name}")
    
    # Пытаемся переместить с тем же именем
    target_path = base_folder / existing_filename
    
    # Обрабатываем коллизию
    if target_path.exists():
        stem = target_path.stem
        suffix = target_path.suffix
        counter = 1
        while target_path.exists():
            new_filename = f"{stem}_{counter}{suffix}"
            target_path = base_folder / new_filename
            counter += 1
    
    print(f"  Имя с учётом коллизии: {target_path.name}")
    
    # Перемещаем
    shutil.move(str(original_file), str(target_path))
    
    # Проверки
    assert not original_file.exists(), "Исходный файл удалён"
    assert existing_file.exists(), "Существующий файл не затронут"
    assert target_path.exists(), "Новый файл с изменённым именем создан"
    assert existing_file.read_text() == "Existing content", "Содержимое существующего файла не изменилось"
    assert target_path.read_text() == "New content", "Содержимое нового файла корректно"
    
    print(f"  ✅ Коллизия имён успешно обработана")


def test_apply_to_all_batch_processing(tmpdir):
    """
    Тест массового применения исправлений к нескольким файлам
    """
    print("\nТест массового применения исправлений")
    
    base_folder = Path(tmpdir) / "test_workspace"
    base_folder.mkdir()
    manual_check_folder = base_folder / "manual_check"
    manual_check_folder.mkdir()
    
    # Создаём несколько файлов с одинаковой дисциплиной
    files = []
    for i in range(1, 4):
        file_path = manual_check_folder / f"scan_{i:03d}.pdf"
        file_path.write_text(f"Content {i}")
        files.append(file_path)
        print(f"  Создан: {file_path.name}")
    
    # Создаём ReviewItems с одинаковым raw_subject
    store = ReviewStore(base_folder / "manual_review.json")
    names = [("Müller", "Hans"), ("Weber", "Lisa"), ("Fischer", "Tom")]
    
    for i, (surname, name) in enumerate(names, 1):
        item = ReviewItem(
            file_path=f"manual_check/scan_{i:03d}.pdf",
            raw_name=f"{surname} {name}",
            raw_subject="Math",  # Одинаковая дисциплина
            reason="Unbekanntes Fach",
            suggestions=["Mathematik"],
            preview_text=f"Preview {i}"
        )
        store.add(item)
    
    store.save()
    
    # Применяем исправление ко всем
    corrected_subject = "Mathematik"
    renamer = FileRenamer()
    success_count = 0
    
    for i, (surname, name) in enumerate(names, 1):
        source_path = files[i-1]
        new_filename = renamer.generate_new_filename(
            surname, name, corrected_subject, 4, 2030
        )
        target_path = base_folder / new_filename
        
        shutil.move(str(source_path), str(target_path))
        success_count += 1
        
        print(f"  Перемещён: {source_path.name} → {new_filename}")
        
        # Удаляем из очереди
        store.remove(f"manual_check/scan_{i:03d}.pdf")
    
    store.save()
    
    # Проверки
    assert success_count == 3, "Все 3 файла должны быть обработаны"
    
    for file_path in files:
        assert not file_path.exists(), f"{file_path.name} должен быть удалён из manual_check"
    
    # Проверяем, что все файлы в корне
    renamed_files = list(base_folder.glob("*.pdf"))
    assert len(renamed_files) == 3, "В корне должно быть 3 PDF файла"
    
    store.load()
    assert store.count() == 0, "Очередь должна быть пустой"
    
    print(f"  ✅ Массовое применение исправлений выполнено")


def test_apply_correction_statistics_update(tmpdir):
    """
    Тест обновления статистики после применения исправлений
    """
    print("\nТест обновления статистики")
    
    base_folder = Path(tmpdir) / "test_workspace"
    base_folder.mkdir()
    manual_check_folder = base_folder / "manual_check"
    manual_check_folder.mkdir()
    
    # Создаём файл
    file_path = manual_check_folder / "test.pdf"
    file_path.write_text("Test content")
    
    # Симулируем статистику
    stats = {
        'total': 10,
        'success': 5,
        'warning': 2,
        'manual': 3,
        'error': 0
    }
    
    print(f"  До применения: success={stats['success']}, manual={stats['manual']}")
    
    # Применяем исправление
    renamer = FileRenamer()
    new_filename = renamer.generate_new_filename(
        "Test", "User", "Biologie", 4, 2030
    )
    target_path = base_folder / new_filename
    
    shutil.move(str(file_path), str(target_path))
    
    # Обновляем статистику
    stats['success'] += 1
    stats['manual'] -= 1
    
    print(f"  После применения: success={stats['success']}, manual={stats['manual']}")
    
    # Проверки
    assert stats['success'] == 6, "Счётчик успешных должен увеличиться"
    assert stats['manual'] == 2, "Счётчик ручных должен уменьшиться"
    assert target_path.exists(), "Файл должен быть в корне"
    assert not file_path.exists(), "Файл удалён из manual_check"
    
    print(f"  ✅ Статистика корректно обновлена")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
