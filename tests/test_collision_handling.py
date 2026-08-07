"""
Тест для проверки корректной обработки коллизий имён файлов
"""

import sys
import tempfile
import shutil
from pathlib import Path

# Добавляем родительскую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from file_renamer import FileRenamer
from review_queue import ReviewStore, ReviewItem


def test_collision_handling():
    """Тест обработки коллизий имён при перемещении"""
    print("=" * 60)
    print("Тест обработки коллизий имён файлов")
    print("=" * 60)
    
    # Создать временную директорию
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Создать исходные файлы с одинаковыми именами
        source_files = []
        for i in range(3):
            file_path = tmpdir / f"test_{i}.pdf"
            file_path.write_text(f"Test content {i}")
            source_files.append(file_path)
        
        # Переименовать все в одно и то же имя
        for i, source in enumerate(source_files):
            new_name = tmpdir / "duplicate.pdf"
            if i > 0:
                # Начиная со второго файла будет коллизия
                source.rename(tmpdir / f"source_{i}.pdf")
                source = tmpdir / f"source_{i}.pdf"
            else:
                source.rename(new_name)
                source = new_name
            source_files[i] = source
        
        # Теперь создать целевую папку и переместить файлы
        target_folder = tmpdir / "manual_check"
        target_folder.mkdir()
        
        renamer = FileRenamer()
        moved_paths = []
        
        # Создать первый файл и переместить
        test_file_1 = tmpdir / "collision_test.pdf"
        test_file_1.write_text("File 1")
        
        moved_1 = renamer.move_to_folder(test_file_1, target_folder)
        print(f"\n1. Перемещён: collision_test.pdf → {moved_1.name if moved_1 else 'ОШИБКА'}")
        moved_paths.append(moved_1)
        
        # Создать второй файл с ТЕМ ЖЕ ИМЕНЕМ и переместить (будет коллизия)
        test_file_2 = tmpdir / "collision_test.pdf"
        test_file_2.write_text("File 2")
        
        moved_2 = renamer.move_to_folder(test_file_2, target_folder)
        print(f"2. Перемещён: collision_test.pdf → {moved_2.name if moved_2 else 'ОШИБКА'}")
        moved_paths.append(moved_2)
        
        # Создать третий файл с ТЕМ ЖЕ ИМЕНЕМ (ещё одна коллизия)
        test_file_3 = tmpdir / "collision_test.pdf"
        test_file_3.write_text("File 3")
        
        moved_3 = renamer.move_to_folder(test_file_3, target_folder)
        print(f"3. Перемещён: collision_test.pdf → {moved_3.name if moved_3 else 'ОШИБКА'}")
        moved_paths.append(moved_3)
        
        # Проверить, что файлы существуют
        print("\nПроверка существования файлов:")
        for i, moved_path in enumerate(moved_paths, 1):
            if moved_path and moved_path.exists():
                print(f"  ✅ Файл {i}: {moved_path.name} существует")
                
                # Проверить содержимое
                content = moved_path.read_text()
                print(f"     Содержимое: {content}")
            else:
                print(f"  ❌ Файл {i}: НЕ найден")
        
        # Создать ReviewItem с фактическими путями
        print("\nСоздание ReviewItem с фактическими путями:")
        review_items = []
        for i, moved_path in enumerate(moved_paths, 1):
            if moved_path:
                # Относительный путь с префиксом manual_check/
                relative_path = str(Path("manual_check") / moved_path.name)
                
                review_item = ReviewItem(
                    file_path=relative_path,
                    raw_name=f"Test Student {i}",
                    raw_subject="Mathematics",
                    reason="Test collision",
                    suggestions=[],
                    preview_text=f"Preview {i}"
                )
                review_items.append(review_item)
                print(f"  ✅ ReviewItem {i}: file_path = {relative_path}")
        
        # Проверить, что пути в ReviewItem соответствуют реальным файлам
        print("\nПроверка соответствия путей:")
        base_folder = tmpdir
        for i, item in enumerate(review_items, 1):
            # Разрешить путь
            full_path = base_folder / item.file_path
            if full_path.exists():
                print(f"  ✅ ReviewItem {i}: путь корректен → {full_path.name}")
            else:
                print(f"  ❌ ReviewItem {i}: файл НЕ найден → {item.file_path}")
        
        print("\n" + "=" * 60)
        if all(p and p.exists() for p in moved_paths):
            print("✅ ВСЕ ФАЙЛЫ КОРРЕКТНО ОБРАБОТАНЫ С КОЛЛИЗИЯМИ")
            
            # Проверить уникальность имён
            names = [p.name for p in moved_paths if p]
            if len(names) == len(set(names)):
                print("✅ ВСЕ ИМЕНА ФАЙЛОВ УНИКАЛЬНЫ")
            else:
                print("❌ ОБНАРУЖЕНЫ ДУБЛИКАТЫ ИМЁН")
        else:
            print("❌ НЕКОТОРЫЕ ФАЙЛЫ НЕ БЫЛИ ПЕРЕМЕЩЕНЫ")

        assert all(path is not None and path.is_file() for path in moved_paths)
        assert [path.name for path in moved_paths] == [
            "collision_test.pdf",
            "collision_test_1.pdf",
            "collision_test_2.pdf",
        ]
        assert [path.read_text() for path in moved_paths] == [
            "File 1",
            "File 2",
            "File 3",
        ]
        print("=" * 60)


def test_move_to_folder_return_value():
    """Тест возвращаемого значения move_to_folder()"""
    print("\n" + "=" * 60)
    print("Тест возвращаемого значения move_to_folder()")
    print("=" * 60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Создать тестовый файл
        test_file = tmpdir / "test_return.pdf"
        test_file.write_text("Test content")
        
        # Создать целевую папку
        target = tmpdir / "target"
        
        renamer = FileRenamer()
        result = renamer.move_to_folder(test_file, target)
        
        print(f"\nИсходный файл: {test_file.name}")
        print(f"Целевая папка: {target.name}")
        print(f"Возвращённое значение: {result}")
        print(f"Тип: {type(result)}")
        
        if result:
            print(f"Путь к перемещённому файлу: {result}")
            print(f"Файл существует: {result.exists()}")
            
            if result.exists():
                print("✅ move_to_folder() корректно возвращает Path")
            else:
                print("❌ Файл не найден по возвращённому пути")
        else:
            print("❌ move_to_folder() вернул None (ошибка)")

        assert isinstance(result, Path)
        assert result.is_file()
        assert result.read_text() == "Test content"
        assert not test_file.exists()
    
    print("=" * 60)


if __name__ == "__main__":
    test_move_to_folder_return_value()
    test_collision_handling()
    
    print("\n" + "=" * 60)
    print("🎉 ВСЕ ТЕСТЫ КОЛЛИЗИЙ ЗАВЕРШЕНЫ")
    print("=" * 60)
