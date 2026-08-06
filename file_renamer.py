"""
Модуль для переименования файлов
"""

from pathlib import Path
from typing import Dict, Set
import shutil


class FileRenamer:
    """Класс для переименования файлов с учетом дубликатов"""
    
    # Немецкие названия месяцев
    MONTH_NAMES_DE = {
        1: "Januar", 2: "Februar", 3: "März", 4: "April",
        5: "Mai", 6: "Juni", 7: "Juli", 8: "August",
        9: "September", 10: "Oktober", 11: "November", 12: "Dezember"
    }
    
    def __init__(self):
        self.renamed_files: Dict[str, int] = {}  # Счетчик для дубликатов
        self.processed_names: Set[str] = set()   # Уже обработанные имена
    
    @staticmethod
    def sanitize_filename(name: str) -> str:
        """
        Очищает строку для использования в имени файла
        
        Args:
            name: Исходная строка
            
        Returns:
            Безопасная строка для имени файла
        """
        # Заменяем пробелы на подчеркивания
        name = name.replace(' ', '_')
        
        # Удаляем небезопасные символы
        unsafe_chars = '<>:"/\\|?*'
        for char in unsafe_chars:
            name = name.replace(char, '')
        
        return name
    
    def generate_new_filename(self, surname: str, name: str, 
                             subject: str, month: int = None, year: int = None,
                             extension: str = '.pdf') -> str:
        """
        Генерирует новое имя файла с учетом дубликатов
        
        Args:
            surname: Фамилия
            name: Имя
            subject: Дисциплина
            month: Номер месяца (1-12), опционально
            year: Год, опционально
            extension: Расширение файла
            
        Returns:
            Новое имя файла в формате: Nachname_Vorname_Fach[_Monat_Jahr].pdf
        """
        # Очищаем компоненты имени
        surname_clean = self.sanitize_filename(surname)
        name_clean = self.sanitize_filename(name)
        subject_clean = self.sanitize_filename(subject)
        
        # Базовое имя файла
        base_name = f"{surname_clean}_{name_clean}_{subject_clean}"
        
        # Добавляем месяц и год, если указаны
        if month is not None and year is not None:
            if 1 <= month <= 12:
                month_name = self.MONTH_NAMES_DE[month]
                base_name = f"{base_name}_{month_name}_{year}"
        
        # Проверяем на дубликаты
        if base_name in self.renamed_files:
            self.renamed_files[base_name] += 1
            counter = self.renamed_files[base_name]
            new_filename = f"{base_name}_{counter}{extension}"
        else:
            self.renamed_files[base_name] = 0
            new_filename = f"{base_name}{extension}"
        
        self.processed_names.add(new_filename)
        return new_filename
    
    @staticmethod
    def rename_file(source_path: Path, target_path: Path, 
                   dry_run: bool = False) -> bool:
        """
        Переименовывает файл
        
        Args:
            source_path: Исходный путь
            target_path: Целевой путь
            dry_run: Если True, только симулирует переименование
            
        Returns:
            True если успешно
        """
        try:
            if dry_run:
                print(f"  [Simulation] {source_path.name} -> {target_path.name}")
                return True
            
            # Проверяем, что целевой файл не существует
            if target_path.exists():
                print(f"  Warnung: Datei {target_path.name} existiert bereits")
                return False
            
            # Переименовываем
            source_path.rename(target_path)
            return True
            
        except Exception as e:
            print(f"  Fehler beim Umbenennen: {str(e)}")
            return False
    
    @staticmethod
    def move_to_folder(source_path: Path, target_folder: Path, 
                      create_folder: bool = True) -> Path | None:
        """
        Перемещает файл в указанную папку
        
        Args:
            source_path: Исходный путь к файлу
            target_folder: Целевая папка
            create_folder: Создавать ли папку, если не существует
            
        Returns:
            Path к перемещённому файлу или None при ошибке
        """
        try:
            if create_folder:
                target_folder.mkdir(parents=True, exist_ok=True)
            
            target_path = target_folder / source_path.name
            
            # Проверяем на существование
            if target_path.exists():
                # Добавляем счетчик к имени
                counter = 1
                stem = source_path.stem
                suffix = source_path.suffix
                while target_path.exists():
                    new_name = f"{stem}_{counter}{suffix}"
                    target_path = target_folder / new_name
                    counter += 1
            
            shutil.move(str(source_path), str(target_path))
            return target_path  # Возвращаем фактический путь
            
        except Exception as e:
            print(f"  Fehler beim Verschieben: {str(e)}")
            return None
