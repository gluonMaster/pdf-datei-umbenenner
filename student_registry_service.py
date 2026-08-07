"""Tkinter-independent administration and import logic for student records."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import io
from pathlib import Path
from typing import Optional, Tuple

from student_registry import (
    RegistryValidationError,
    Student,
    StudentAlias,
    StudentRegistry,
    _clean_display,
    _parse_active,
    normalize_name,
)


@dataclass(frozen=True)
class StudentRegistryPaths:
    """Central paths used by the matcher, admin service, and GUI."""

    csv_path: Path
    aliases_path: Path

    @classmethod
    def in_directory(cls, directory: str | Path) -> "StudentRegistryPaths":
        base = Path(directory).resolve()
        return cls(base / "Schuler.csv", base / "student_aliases.json")


@dataclass(frozen=True)
class DuplicateCandidate:
    first: Student
    second: Student
    score: float
    reason: str


@dataclass(frozen=True)
class ImportIssue:
    row_number: int
    student: Student
    reason: str


@dataclass(frozen=True)
class ImportPreview:
    """Immutable diff produced before an import can change the registry."""

    source_path: Path
    delimiter: str
    rows: Tuple[Student, ...]
    new: Tuple[Student, ...]
    unchanged: Tuple[Student, ...]
    conflicts: Tuple[ImportIssue, ...]
    duplicates: Tuple[ImportIssue, ...]
    missing: Tuple[Student, ...]
    base_fingerprint: str

    @property
    def new_students(self) -> Tuple[Student, ...]:
        return self.new

    @property
    def unchanged_students(self) -> Tuple[Student, ...]:
        return self.unchanged

    @property
    def missing_students(self) -> Tuple[Student, ...]:
        return self.missing

    @property
    def has_blocking_issues(self) -> bool:
        return bool(self.conflicts or self.duplicates)

    @property
    def summary(self) -> str:
        return (
            f"Neu: {len(self.new)} | Unverändert: {len(self.unchanged)} | "
            f"Konflikte: {len(self.conflicts)} | Duplikate: {len(self.duplicates)} | "
            f"Im neuen Bestand fehlend: {len(self.missing)}"
        )


@dataclass(frozen=True)
class ImportApplyResult:
    added: Tuple[Student, ...]
    backup_path: Optional[Path]
    skipped_conflicts: int
    skipped_duplicates: int


@dataclass(frozen=True)
class _ImportRow:
    row_number: int
    student: Student


class StudentRegistryService:
    """Administrative operations shared by GUI code and unit tests."""

    def __init__(self, registry: StudentRegistry) -> None:
        self.registry = registry

    @classmethod
    def from_directory(cls, directory: str | Path) -> "StudentRegistryService":
        paths = StudentRegistryPaths.in_directory(directory)
        return cls(StudentRegistry(paths.csv_path, paths.aliases_path))

    @property
    def paths(self) -> StudentRegistryPaths:
        return StudentRegistryPaths(
            self.registry.csv_path.resolve(),
            self.registry.aliases_path.resolve(),
        )

    def search(self, query: str = "", active: Optional[bool] = None) -> Tuple[Student, ...]:
        """Filter by number/name with case and extra whitespace ignored."""

        if active is not None and not isinstance(active, bool):
            raise RegistryValidationError("active filter must be true, false, or None.")
        needle = normalize_name(query)
        found = []
        for student in self.registry.students:
            if active is not None and student.active is not active:
                continue
            searchable = normalize_name(
                f"{student.number} {student.last_name} {student.first_name} "
                f"{student.first_name} {student.last_name}"
            )
            if not needle or needle in searchable:
                found.append(student)
        return tuple(sorted(found, key=lambda item: item.number))

    def aliases_for(self, student_number: Optional[int] = None) -> Tuple[StudentAlias, ...]:
        aliases = self.registry.aliases
        if student_number is None:
            return aliases
        number = int(student_number)
        return tuple(alias for alias in aliases if alias.student_number == number)

    def remove_alias(self, alias_name: str) -> bool:
        return self.registry.remove_alias(alias_name)

    def find_potential_duplicates(
        self,
        *,
        threshold: float = 0.86,
    ) -> Tuple[DuplicateCandidate, ...]:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1.")
        students = sorted(self.registry.students, key=lambda item: item.number)
        result = []
        for index, first in enumerate(students):
            first_key = normalize_name(first.full_name)
            for second in students[index + 1 :]:
                second_key = normalize_name(second.full_name)
                score = SequenceMatcher(None, first_key, second_key).ratio()
                if score < threshold:
                    continue
                reason = "Gleicher normalisierter Name" if score == 1.0 else "Ähnliche Namen"
                result.append(
                    DuplicateCandidate(first, second, round(score, 6), reason)
                )
        return tuple(
            sorted(result, key=lambda item: (-item.score, item.first.number, item.second.number))
        )

    def preview_import(self, source_path: str | Path) -> ImportPreview:
        """Parse an external UTF-8 CSV and calculate a non-mutating diff."""

        path = Path(source_path)
        imported_rows, delimiter = self._read_import(path)
        imported_numbers = {row.student.number for row in imported_rows}
        number_counts: dict[int, int] = {}
        name_counts: dict[tuple[str, str], int] = {}
        for row in imported_rows:
            number_counts[row.student.number] = number_counts.get(row.student.number, 0) + 1
            key = self._name_key(row.student)
            name_counts[key] = name_counts.get(key, 0) + 1

        existing_by_number = {student.number: student for student in self.registry.students}
        existing_by_name: dict[tuple[str, str], list[Student]] = {}
        for student in self.registry.students:
            existing_by_name.setdefault(self._name_key(student), []).append(student)
        existing_aliases: dict[str, set[int]] = {}
        for alias in self.registry.aliases:
            existing_aliases.setdefault(normalize_name(alias.name), set()).add(
                alias.student_number
            )

        new = []
        unchanged = []
        conflicts = []
        duplicates = []
        for row in imported_rows:
            student = row.student
            key = self._name_key(student)
            duplicate_reasons = []
            if number_counts[student.number] > 1:
                duplicate_reasons.append(f"Nummer {student.number} kommt mehrfach vor")
            if name_counts[key] > 1:
                duplicate_reasons.append("Name kommt mehrfach vor")
            if duplicate_reasons:
                duplicates.append(ImportIssue(row.row_number, student, "; ".join(duplicate_reasons)))
                continue

            same_number = existing_by_number.get(student.number)
            same_name = existing_by_name.get(key, [])
            alias_keys = {
                normalize_name(student.full_name),
                normalize_name(f"{student.first_name} {student.last_name}"),
            }
            alias_numbers = set().union(
                *(existing_aliases.get(alias_key, set()) for alias_key in alias_keys)
            )
            if same_number is not None:
                if same_number == student:
                    unchanged.append(student)
                else:
                    conflicts.append(
                        ImportIssue(
                            row.row_number,
                            student,
                            f"Nummer {student.number} gehört bereits zu "
                            f"{same_number.full_name} (Aktiv={same_number.active})",
                        )
                    )
            elif same_name:
                numbers = ", ".join(str(item.number) for item in same_name)
                conflicts.append(
                    ImportIssue(
                        row.row_number,
                        student,
                        f"Name existiert bereits unter Nummer {numbers}",
                    )
                )
            elif alias_numbers:
                numbers = ", ".join(
                    str(number)
                    for number in sorted(alias_numbers)
                )
                conflicts.append(
                    ImportIssue(
                        row.row_number,
                        student,
                        f"Name ist bereits Alias von Nummer {numbers}",
                    )
                )
            else:
                new.append(student)

        missing = tuple(
            sorted(
                (
                    student
                    for student in self.registry.students
                    if student.number not in imported_numbers
                ),
                key=lambda item: item.number,
            )
        )
        return ImportPreview(
            source_path=path.resolve(),
            delimiter=delimiter,
            rows=tuple(row.student for row in imported_rows),
            new=tuple(sorted(new, key=lambda item: item.number)),
            unchanged=tuple(sorted(unchanged, key=lambda item: item.number)),
            conflicts=tuple(conflicts),
            duplicates=tuple(duplicates),
            missing=missing,
            base_fingerprint=self._registry_fingerprint(),
        )

    def apply_import(
        self,
        preview: ImportPreview,
        *,
        confirmed: bool = False,
    ) -> ImportApplyResult:
        """Apply only reviewed new rows; conflicts and missing rows stay untouched."""

        if not confirmed:
            raise RegistryValidationError(
                "Import must be explicitly confirmed after reviewing the preview."
            )
        if preview.base_fingerprint != self._registry_fingerprint():
            raise RegistryValidationError(
                "The student registry changed after the preview; create a new preview."
            )
        backup = self.registry.merge_students(preview.new, create_backup=True)
        return ImportApplyResult(
            added=preview.new,
            backup_path=backup,
            skipped_conflicts=len(preview.conflicts),
            skipped_duplicates=len(preview.duplicates),
        )

    def _registry_fingerprint(self) -> str:
        try:
            data = self.registry.csv_path.read_bytes()
        except OSError as exc:
            raise RegistryValidationError(
                f"Student registry cannot be read: {self.registry.csv_path}"
            ) from exc
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _name_key(student: Student) -> tuple[str, str]:
        return normalize_name(student.last_name), normalize_name(student.first_name)

    @staticmethod
    def _read_import(path: Path) -> tuple[Tuple[_ImportRow, ...], str]:
        if not path.is_file():
            raise RegistryValidationError(f"Import CSV does not exist: {path}")
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise RegistryValidationError("Import CSV must use UTF-8 or UTF-8 BOM.") from exc
        except OSError as exc:
            raise RegistryValidationError(f"Import CSV cannot be read: {path}") from exc
        if not text.strip():
            raise RegistryValidationError("Import CSV is empty.")

        delimiter = StudentRegistryService._detect_delimiter(text)
        reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        if reader.fieldnames is None:
            raise RegistryValidationError("Import CSV header is missing.")

        header_map: dict[str, str] = {}
        for raw_header in reader.fieldnames:
            clean_header = " ".join((raw_header or "").split())
            key = clean_header.casefold()
            if not key:
                raise RegistryValidationError("Import CSV contains an empty header.")
            if key in header_map:
                raise RegistryValidationError("Import CSV contains duplicate headers.")
            header_map[key] = raw_header

        required = {"nummer", "nachname"}
        if not required.issubset(header_map):
            raise RegistryValidationError("Import CSV must contain Nummer and Nachname.")
        first_key = "vorname" if "vorname" in header_map else "forname"
        if first_key not in header_map:
            raise RegistryValidationError(
                "Import CSV must contain Vorname or legacy Forname."
            )

        imported = []
        try:
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise RegistryValidationError(
                        f"Row {row_number} has more values than the header."
                    )
                values = ["" if value is None else str(value) for value in row.values()]
                if not any(value.strip() for value in values):
                    continue
                raw_number = row.get(header_map["nummer"], "")
                try:
                    number = int(str(raw_number).strip())
                except (TypeError, ValueError) as exc:
                    raise RegistryValidationError(
                        f"Row {row_number}: Nummer must be an integer."
                    ) from exc
                if number <= 0:
                    raise RegistryValidationError(
                        f"Row {row_number}: Nummer must be positive."
                    )
                last_name = _clean_display(
                    row.get(header_map["nachname"], ""),
                    f"row {row_number} Nachname",
                )
                first_name = _clean_display(
                    row.get(header_map[first_key], ""),
                    f"row {row_number} {first_key}",
                )
                active_value = row.get(header_map["aktiv"]) if "aktiv" in header_map else None
                imported.append(
                    _ImportRow(
                        row_number,
                        Student(
                            number,
                            last_name,
                            first_name,
                            _parse_active(active_value, row_number),
                        ),
                    )
                )
        except csv.Error as exc:
            raise RegistryValidationError(f"Import CSV is malformed: {exc}") from exc
        if not imported:
            raise RegistryValidationError("Import CSV contains no student rows.")
        return tuple(imported), delimiter

    @staticmethod
    def _detect_delimiter(text: str) -> str:
        sample = text[:8192]
        try:
            return csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
        except csv.Error:
            header = sample.splitlines()[0] if sample.splitlines() else ""
            counts = {delimiter: header.count(delimiter) for delimiter in ";,\t|"}
            delimiter = max(counts, key=counts.get)
            if counts[delimiter] == 0:
                raise RegistryValidationError(
                    "Could not detect the CSV delimiter (supported: semicolon, comma, tab, pipe)."
                )
            return delimiter


__all__ = [
    "DuplicateCandidate",
    "ImportApplyResult",
    "ImportIssue",
    "ImportPreview",
    "StudentRegistryPaths",
    "StudentRegistryService",
]
