"""
Модуль для переименования файлов
"""

import errno
import os
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
        self.last_error: Optional[str] = None
        self.renamed_files: Dict[str, int] = {}  # Счетчик для дубликатов
        self.processed_names: Set[str] = set()   # Уже обработанные имена
        # Absolute, case-insensitive paths reserved by the current batch.  A
        # reservation is made during planning, including dry-runs, so two
        # documents can never receive the same planned target.
        self._reserved_paths: Set[str] = set()
        self._initial_folder_entries: Dict[str, Set[str]] = {}
    
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
    
    def generate_new_filename(
        self,
        surname: str,
        name: str,
        subject: str,
        month: Optional[int] = None,
        year: Optional[int] = None,
        extension: str = ".pdf",
    ) -> str:
        """
        Генерирует новое имя файла с учетом дубликатов
        
        Args:
            surname: Фамилия
            name: Имя
            subject: Дисциплина
            month: Явно выбранный номер месяца (1-12)
            year: Явно выбранный год
            extension: Расширение файла
            
        Returns:
            Новое имя файла в формате: Nachname_Vorname_Fach_Monat_Jahr.pdf
        """
        if month is None or year is None:
            raise ValueError("Monat und Jahr müssen ausdrücklich ausgewählt werden.")

        canonical_name = self.build_filename(
            surname,
            name,
            subject,
            month,
            year,
            extension,
        )
        normalized_extension = extension if extension.startswith(".") else f".{extension}"
        base_name = canonical_name[: -len(normalized_extension)]
        
        # Проверяем на дубликаты
        if base_name in self.renamed_files:
            self.renamed_files[base_name] += 1
            counter = self.renamed_files[base_name]
            new_filename = f"{base_name}_{counter}{normalized_extension}"
        else:
            self.renamed_files[base_name] = 0
            new_filename = canonical_name
        
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

        if (
            not isinstance(month, int)
            or isinstance(month, bool)
            or month not in self.MONTH_NAMES_DE
        ):
            raise ValueError("Monat muss zwischen 1 und 12 liegen.")
        if not isinstance(year, int) or isinstance(year, bool) or year < 1:
            raise ValueError("Jahr muss eine positive Ganzzahl sein.")
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
            raise ValueError("Bestandteile des Dateinamens dürfen nicht leer sein.")
        return "_".join(components) + extension

    @staticmethod
    def _path_key(path: Path) -> str:
        """Return a stable path key matching Windows collision semantics."""

        return str(Path(path).resolve(strict=False)).casefold()

    def reset_reservations(self) -> None:
        """Start a new batch of collision planning."""

        self._reserved_paths.clear()
        self._initial_folder_entries.clear()

    def release_target_path(self, target_path: Path) -> None:
        """Release a failed real-operation reservation for deterministic retry."""

        self._reserved_paths.discard(self._path_key(target_path))

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
        folders = [desired_path.parent]
        if source_path is not None:
            folders.append(Path(source_path).parent)
        for folder in folders:
            folder_key = self._path_key(folder)
            if folder_key not in self._initial_folder_entries:
                # Snapshot both folders before the first move. Names vacated
                # later remain occupied for the rest of the batch, giving
                # dry-runs and real runs the same deterministic plan.
                self._initial_folder_entries[folder_key] = (
                    {self._path_key(entry) for entry in folder.iterdir()}
                    if folder.exists()
                    else set()
                )
        initial_entries = self._initial_folder_entries[self._path_key(desired_path.parent)]
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
            occupied_on_disk = (
                candidate_key in initial_entries
                or candidate.exists()
                or candidate.is_symlink()
            ) and candidate_key != source_key
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
    
    def rename_file(
        self,
        source_path: Path,
        target_path: Path,
        dry_run: bool = False,
    ) -> bool:
        """
        Переименовывает файл
        
        Args:
            source_path: Исходный путь
            target_path: Целевой путь
            dry_run: Если True, только симулирует переименование
            
        Returns:
            True если успешно
        """
        source_path = Path(source_path)
        target_path = Path(target_path)
        self.last_error = None
        try:
            if dry_run:
                print(f"  [Simulation] {source_path.name} -> {target_path.name}")
                return True

            self._move_no_clobber(source_path, target_path)
            return True

        except FileExistsError as error:
            self.last_error = str(error) or error.__class__.__name__
            self.release_target_path(target_path)
            print(f"  Warnung: Datei {target_path.name} existiert bereits")
            return False
        except Exception as error:
            self.last_error = str(error) or error.__class__.__name__
            self.release_target_path(target_path)
            print(f"  Fehler beim Umbenennen: {error}")
            return False

    def move_file(
        self,
        source_path: Path,
        target_path: Path,
        dry_run: bool = False,
    ) -> bool:
        """Move a file to an already planned target without overwriting it."""

        source_path = Path(source_path)
        target_path = Path(target_path)
        self.last_error = None
        try:
            if dry_run:
                print(f"  [Simulation] {source_path} -> {target_path}")
                return True
            target_path.parent.mkdir(parents=True, exist_ok=True)
            self._move_no_clobber(source_path, target_path)
            return True
        except FileExistsError as error:
            self.last_error = str(error) or error.__class__.__name__
            self.release_target_path(target_path)
            print(f"  Warnung: Datei {target_path.name} existiert bereits")
            return False
        except Exception as error:
            self.last_error = str(error) or error.__class__.__name__
            self.release_target_path(target_path)
            print(f"  Fehler beim Verschieben: {error}")
            return False

    @staticmethod
    def _absolute_spelling(path: Path) -> str:
        """Normalize a path without folding case or resolving its final name."""

        return os.path.abspath(os.fspath(path))

    @classmethod
    def _same_spelling(cls, first: Path, second: Path) -> bool:
        return cls._absolute_spelling(first) == cls._absolute_spelling(second)

    @classmethod
    def _is_case_only_same_file(cls, source_path: Path, target_path: Path) -> bool:
        if cls._path_key(source_path) != cls._path_key(target_path):
            return False
        if not target_path.exists():
            return False
        try:
            return os.path.samefile(source_path, target_path)
        except OSError:
            return False

    @staticmethod
    def _copy_no_clobber(source_path: Path, target_path: Path) -> None:
        """Cross-filesystem fallback that creates the destination exclusively."""

        target_created = False
        try:
            with source_path.open("rb") as source_handle:
                with target_path.open("xb") as target_handle:
                    target_created = True
                    shutil.copyfileobj(source_handle, target_handle)
                    target_handle.flush()
                    os.fsync(target_handle.fileno())
            shutil.copystat(source_path, target_path)
            source_path.unlink()
        except Exception as error:
            if target_created:
                try:
                    target_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    raise OSError(
                        f"Verschieben fehlgeschlagen: {error}. "
                        f"Die angelegte Zieldatei konnte nicht entfernt werden: "
                        f"{target_path} ({cleanup_error}). "
                        f"Quelldatei bleibt unter: {source_path}."
                    ) from error
            raise

    @classmethod
    def _move_no_clobber(cls, source_path: Path, target_path: Path) -> None:
        """Move one file while atomically refusing an independently claimed target."""

        if not source_path.is_file():
            raise FileNotFoundError(f"Quelldatei nicht gefunden: {source_path}")
        if cls._same_spelling(source_path, target_path):
            return

        # On a case-insensitive filesystem the differently-cased target lookup
        # resolves to the source itself.  This is the only existing target that
        # may be renamed in place; a distinct target is always a collision.
        if target_path.exists():
            if cls._is_case_only_same_file(source_path, target_path):
                os.rename(source_path, target_path)
                return
            raise FileExistsError(f"Zieldatei existiert bereits: {target_path}")

        target_path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            try:
                # Win32 rename is an atomic fail-if-exists operation.  Prefer
                # it over link/unlink so read-only files retain normal rename
                # behavior and cannot be left behind as duplicate hard links.
                os.rename(source_path, target_path)
                return
            except FileExistsError:
                raise
            except OSError as error:
                if target_path.exists():
                    raise FileExistsError(
                        f"Zieldatei existiert bereits: {target_path}"
                    ) from error
                if error.errno != errno.EXDEV and getattr(error, "winerror", None) != 17:
                    raise
            cls._copy_no_clobber(source_path, target_path)
            return

        linked = False
        try:
            # Hard-link creation is an atomic no-clobber claim on the common
            # same-filesystem path.  Unlinking the old name completes the move.
            os.link(source_path, target_path)
            linked = True
            source_path.unlink()
            return
        except FileExistsError:
            raise
        except OSError as error:
            if linked:
                try:
                    target_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    raise OSError(
                        f"Verschieben fehlgeschlagen: {error}. "
                        f"Die angelegte Dateiverknüpfung konnte nicht entfernt werden: "
                        f"{target_path} ({cleanup_error}). "
                        f"Quelldatei bleibt unter: {source_path}."
                    ) from error
                raise

        # Hard links may be unavailable (cross-volume, network filesystem, or
        # policy).  Exclusive creation preserves no-clobber semantics there.
        cls._copy_no_clobber(source_path, target_path)

    def move_to_folder(
        self,
        source_path: Path,
        target_folder: Path,
        create_folder: bool = True,
    ) -> Path | None:
        """
        Перемещает файл в указанную папку
        
        Args:
            source_path: Исходный путь к файлу
            target_folder: Целевая папка
            create_folder: Создавать ли папку, если не существует
            
        Returns:
            Path к перемещённому файлу или None при ошибке
        """
        target_path = None
        self.last_error = None
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
            
        except Exception as error:
            self.last_error = str(error) or error.__class__.__name__
            if target_path is not None:
                self.release_target_path(target_path)
            print(f"  Fehler beim Verschieben: {error}")
            return None
