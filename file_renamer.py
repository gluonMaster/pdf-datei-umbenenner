"""
Модуль для переименования файлов
"""

import shutil
from pathlib import Path
from typing import Dict, Optional, Set


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
        # Absolute, case-insensitive paths reserved by the current batch.  A
        # reservation is made during planning, including dry-runs, so two
        # documents can never receive the same planned target.
        self._reserved_paths: Set[str] = set()
    
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

    def build_filename(
        self,
        surname: str,
        name: str,
        subject: str,
        month: int,
        year: int,
        extension: str = ".pdf",
    ) -> str:
        """Build a canonical filename without making a collision decision."""

        if month not in self.MONTH_NAMES_DE:
            raise ValueError("month must be between 1 and 12")
        if not isinstance(year, int) or isinstance(year, bool) or year < 1:
            raise ValueError("year must be a positive integer")
        if not extension.startswith("."):
            extension = f".{extension}"

        components = (
            self.sanitize_filename(surname),
            self.sanitize_filename(name),
            self.sanitize_filename(subject),
            self.MONTH_NAMES_DE[month],
            str(year),
        )
        if any(not component for component in components):
            raise ValueError("filename components must not be empty")
        return "_".join(components) + extension

    @staticmethod
    def _path_key(path: Path) -> str:
        """Return a stable path key matching Windows collision semantics."""

        return str(Path(path).resolve(strict=False)).casefold()

    def reset_reservations(self) -> None:
        """Start a new batch of collision planning."""

        self._reserved_paths.clear()

    def reserve_target_path(
        self,
        desired_path: Path,
        *,
        source_path: Optional[Path] = None,
    ) -> Path:
        """Reserve the first free deterministic target path.

        Both files already present on disk and targets planned earlier in this
        process are considered.  Suffixes are tried in the order ``_1``,
        ``_2``, ... and gaps are reused deterministically.
        """

        desired_path = Path(desired_path)
        source_key = self._path_key(source_path) if source_path is not None else None
        counter = 0
        while True:
            candidate = (
                desired_path
                if counter == 0
                else desired_path.with_name(
                    f"{desired_path.stem}_{counter}{desired_path.suffix}"
                )
            )
            candidate_key = self._path_key(candidate)
            occupied_on_disk = candidate.exists() and candidate_key != source_key
            if candidate_key not in self._reserved_paths and not occupied_on_disk:
                self._reserved_paths.add(candidate_key)
                self.processed_names.add(candidate.name)
                return candidate
            counter += 1

    def plan_new_path(
        self,
        target_folder: Path,
        surname: str,
        name: str,
        subject: str,
        month: int,
        year: int,
        *,
        extension: str = ".pdf",
        source_path: Optional[Path] = None,
    ) -> Path:
        """Build and reserve a collision-free canonical target path."""

        filename = self.build_filename(
            surname,
            name,
            subject,
            month,
            year,
            extension,
        )
        return self.reserve_target_path(
            Path(target_folder) / filename,
            source_path=source_path,
        )
    
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

            if FileRenamer._path_key(source_path) == FileRenamer._path_key(target_path):
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
    def move_file(
        source_path: Path,
        target_path: Path,
        dry_run: bool = False,
    ) -> bool:
        """Move a file to an already planned target without overwriting it."""

        try:
            if dry_run:
                print(f"  [Simulation] {source_path} -> {target_path}")
                return True
            if FileRenamer._path_key(source_path) == FileRenamer._path_key(target_path):
                return True
            if target_path.exists():
                print(f"  Warnung: Datei {target_path.name} existiert bereits")
                return False
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_path), str(target_path))
            return True
        except Exception as error:
            print(f"  Fehler beim Verschieben: {error}")
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
            
            target_path = self.reserve_target_path(
                target_folder / source_path.name,
                source_path=source_path,
            )
            if not self.move_file(source_path, target_path):
                return None
            return target_path  # Возвращаем фактический путь
            
        except Exception as e:
            print(f"  Fehler beim Verschieben: {str(e)}")
            return None
