"""Safe canonical student registry backed by a semicolon-separated CSV file.

The registry is deliberately independent from the PDF parser and Tkinter.  A
downstream pipeline can pass the parser's raw name to :meth:`StudentRegistry.match`
and route every non-exact result to manual review.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unicodedata
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple


class MatchStatus(str, Enum):
    """Outcome of matching a parsed name against the registry."""

    EXACT = "exact"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"
    ERROR = "error"


@dataclass(frozen=True)
class Student:
    """One canonical student row with a stable numeric identifier."""

    number: int
    last_name: str
    first_name: str
    active: bool = True

    @property
    def full_name(self) -> str:
        """Canonical display order used by the existing rename pipeline."""

        return f"{self.last_name} {self.first_name}"

    # German compatibility names make the CSV-to-model mapping explicit.
    @property
    def nummer(self) -> int:
        return self.number

    @property
    def nachname(self) -> str:
        return self.last_name

    @property
    def vorname(self) -> str:
        return self.first_name

    @property
    def forname(self) -> str:
        return self.first_name


@dataclass(frozen=True)
class StudentSuggestion:
    """A fuzzy candidate and its similarity score (0.0 to 1.0)."""

    student: Student
    score: float


@dataclass(frozen=True)
class StudentMatch:
    """Small integration result consumed after PDF form parsing."""

    status: MatchStatus
    student: Optional[Student]
    suggestions: Tuple[Student, ...]
    reason: str
    ranked_suggestions: Tuple[StudentSuggestion, ...] = ()

    @property
    def canonical_student(self) -> Optional[Student]:
        return self.student

    @property
    def requires_manual_review(self) -> bool:
        return self.status != MatchStatus.EXACT


class StudentRegistryError(Exception):
    """Base class for controlled registry failures."""


class RegistryUnavailableError(StudentRegistryError):
    """Raised when a write is requested for an unavailable registry."""


class RegistryValidationError(StudentRegistryError, ValueError):
    """Raised before writing invalid or conflicting data."""


class RegistryWriteError(StudentRegistryError, OSError):
    """Raised when an atomic file replacement cannot be completed."""


@dataclass(frozen=True)
class _CsvSchema:
    first_name_header: str
    has_active: bool


@dataclass(frozen=True)
class _Alias:
    name: str
    student_number: int


def normalize_name(value: str) -> str:
    """Normalize for comparison without removing punctuation or diacritics.

    NFC makes canonically equivalent Unicode strings equal, ``casefold``
    handles case, and ``split`` collapses surrounding/repeated whitespace.
    Hyphens, apostrophes, and diacritical marks remain significant.
    """

    if not isinstance(value, str):
        raise TypeError("A student name must be text.")
    normalized = unicodedata.normalize("NFC", value)
    return " ".join(normalized.split()).casefold()


def _clean_display(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise RegistryValidationError(f"{field_name} must be text.")
    cleaned = " ".join(unicodedata.normalize("NFC", value).split())
    if not cleaned:
        raise RegistryValidationError(f"{field_name} must not be empty.")
    return cleaned


def _parse_active(value: object, row_number: int) -> bool:
    if value is None or str(value).strip() == "":
        return True
    key = str(value).strip().casefold()
    if key in {"1", "true", "yes", "ja", "active", "aktiv"}:
        return True
    if key in {"0", "false", "no", "nein", "inactive", "inaktiv"}:
        return False
    raise RegistryValidationError(
        f"Row {row_number}: Aktiv must be a boolean value, got {value!r}."
    )


class StudentRegistry:
    """Load, match, and explicitly update a canonical student registry.

    Loading problems are retained as diagnostics rather than raised from the
    constructor.  Consequently, one damaged registry produces ``error`` match
    results which a caller can send to manual review without stopping a batch.
    Mutating methods remain explicit and raise controlled exceptions.
    """

    def __init__(
        self,
        csv_path: os.PathLike[str] | str,
        aliases_path: os.PathLike[str] | str | None = None,
        *,
        suggestion_limit: int = 5,
        fuzzy_threshold: float = 0.62,
        ambiguity_threshold: float = 0.78,
        ambiguity_delta: float = 0.04,
    ) -> None:
        self.csv_path = Path(csv_path)
        self.aliases_path = (
            Path(aliases_path)
            if aliases_path is not None
            else self.csv_path.with_name("student_aliases.json")
        )
        if suggestion_limit < 1:
            raise ValueError("suggestion_limit must be at least 1.")
        if not 0.0 <= fuzzy_threshold <= 1.0:
            raise ValueError("fuzzy_threshold must be between 0 and 1.")
        if not 0.0 <= ambiguity_threshold <= 1.0:
            raise ValueError("ambiguity_threshold must be between 0 and 1.")
        if ambiguity_delta < 0.0:
            raise ValueError("ambiguity_delta must not be negative.")

        self.suggestion_limit = suggestion_limit
        self.fuzzy_threshold = fuzzy_threshold
        self.ambiguity_threshold = ambiguity_threshold
        self.ambiguity_delta = ambiguity_delta
        self._students: Tuple[Student, ...] = ()
        self._students_by_number: Dict[int, Student] = {}
        self._aliases: Dict[str, _Alias] = {}
        self._schema: Optional[_CsvSchema] = None
        self._load_error: Optional[str] = None
        self.reload()

    @property
    def students(self) -> Tuple[Student, ...]:
        return self._students

    @property
    def is_available(self) -> bool:
        return self._load_error is None

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error

    @property
    def diagnostic(self) -> Optional[str]:
        return self._load_error

    def reload(self) -> bool:
        """Reload both files, returning whether the complete registry is valid."""

        try:
            students, schema = self._read_students(self.csv_path)
            by_number = {student.number: student for student in students}
            aliases = self._read_aliases(self.aliases_path, by_number)
            self._validate_alias_conflicts(students, aliases)
        except (OSError, UnicodeError, csv.Error, json.JSONDecodeError,
                RegistryValidationError, TypeError, ValueError) as exc:
            self._students = ()
            self._students_by_number = {}
            self._aliases = {}
            self._schema = None
            self._load_error = (
                f"Student registry is unavailable: {exc}. "
                "Send affected documents to manual review."
            )
            return False

        self._students = students
        self._students_by_number = by_number
        self._aliases = aliases
        self._schema = schema
        self._load_error = None
        return True

    def match(self, raw_name: str) -> StudentMatch:
        """Resolve a parser name without mutating or guessing a student."""

        if not self.is_available:
            return StudentMatch(
                MatchStatus.ERROR,
                None,
                (),
                self._load_error or "Student registry is unavailable.",
            )

        try:
            key = normalize_name(raw_name)
        except TypeError as exc:
            return StudentMatch(MatchStatus.UNKNOWN, None, (), str(exc))
        if not key:
            return StudentMatch(
                MatchStatus.UNKNOWN,
                None,
                (),
                "The parsed student name is empty; manual confirmation is required.",
            )

        exact = self._exact_candidates(key)
        if len(exact) == 1:
            student = exact[0]
            return StudentMatch(
                MatchStatus.EXACT,
                student,
                (),
                f"Exact canonical or confirmed-alias match for student {student.number}.",
            )
        if len(exact) > 1:
            ranked = tuple(StudentSuggestion(item, 1.0) for item in exact)
            return StudentMatch(
                MatchStatus.AMBIGUOUS,
                None,
                exact,
                "More than one canonical student has this normalized name.",
                ranked,
            )

        ranked = self._fuzzy_suggestions(key)
        suggestions = tuple(item.student for item in ranked)
        status = MatchStatus.UNKNOWN
        reason = "No exact student or confirmed alias matched; manual confirmation is required."
        if (
            len(ranked) >= 2
            and ranked[0].score >= self.ambiguity_threshold
            and ranked[1].score >= self.ambiguity_threshold
            and ranked[0].score - ranked[1].score <= self.ambiguity_delta
        ):
            status = MatchStatus.AMBIGUOUS
            reason = "Several similarly ranked students require manual selection."
        return StudentMatch(status, None, suggestions, reason, ranked)

    def match_parts(self, last_name: str, first_name: str) -> StudentMatch:
        """Match explicit name fields in the registry's canonical order."""

        try:
            raw_name = f"{_clean_display(last_name, 'last_name')} " \
                       f"{_clean_display(first_name, 'first_name')}"
        except RegistryValidationError as exc:
            return StudentMatch(MatchStatus.UNKNOWN, None, (), str(exc))
        return self.match(raw_name)

    # Readable aliases for callers at different integration boundaries.
    match_name = match
    resolve = match

    def select_existing(self, student_number: int) -> Student:
        """Return a manually selected canonical row without changing files."""

        self._require_available()
        try:
            return self._students_by_number[int(student_number)]
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryValidationError(
                f"Unknown student number: {student_number!r}."
            ) from exc

    def confirm_existing(
        self,
        raw_name: str,
        student_number: int,
        *,
        save_alias: bool = True,
    ) -> Student:
        """Confirm a selection and optionally remember the OCR spelling."""

        student = self.select_existing(student_number)
        if not save_alias:
            return student

        alias_name = _clean_display(raw_name, "raw_name")
        alias_key = normalize_name(alias_name)
        existing = self._aliases.get(alias_key)
        if existing:
            if existing.student_number != student.number:
                raise RegistryValidationError(
                    "This alias is already assigned to another student."
                )
            return student

        canonical_candidates = self._canonical_candidates(alias_key)
        conflicting = [item for item in canonical_candidates if item.number != student.number]
        if conflicting:
            raise RegistryValidationError(
                "The alias is an exact canonical name of another student."
            )
        if canonical_candidates:
            return student  # A redundant alias for the same row need not be written.

        updated = dict(self._aliases)
        updated[alias_key] = _Alias(alias_name, student.number)
        self._write_aliases(updated)
        self._aliases = updated
        return student

    add_alias = confirm_existing

    def add_student(
        self,
        last_name: str,
        first_name: str,
        *,
        active: bool = True,
    ) -> Student:
        """Explicitly add a canonical student using the next stable number."""

        self._require_available()
        clean_last = _clean_display(last_name, "last_name")
        clean_first = _clean_display(first_name, "first_name")
        if not isinstance(active, bool):
            raise RegistryValidationError("active must be true or false.")
        self._ensure_unique_canonical(clean_last, clean_first)

        number = max(self._students_by_number, default=0) + 1
        student = Student(number, clean_last, clean_first, active)
        updated = self._students + (student,)
        schema = self._schema_or_raise()
        schema = self._ensure_active_schema(schema, required=not active)
        self._write_students(updated, schema)
        self._commit_students(updated, schema)
        return student

    def confirm_new(
        self,
        last_name: str,
        first_name: str,
        *,
        active: bool = True,
    ) -> Student:
        """Explicit confirmation alias for :meth:`add_student`."""

        return self.add_student(last_name, first_name, active=active)

    def update_student(
        self,
        student_number: int,
        *,
        last_name: Optional[str] = None,
        first_name: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> Student:
        """Edit a row while preserving its stable number and aliases."""

        current = self.select_existing(student_number)
        clean_last = (
            current.last_name if last_name is None else _clean_display(last_name, "last_name")
        )
        clean_first = (
            current.first_name
            if first_name is None
            else _clean_display(first_name, "first_name")
        )
        new_active = current.active if active is None else active
        if not isinstance(new_active, bool):
            raise RegistryValidationError("active must be true or false.")
        self._ensure_unique_canonical(clean_last, clean_first, except_number=current.number)

        replacement = Student(current.number, clean_last, clean_first, new_active)
        updated = tuple(
            replacement if item.number == current.number else item
            for item in self._students
        )
        schema = self._schema_or_raise()
        schema = self._ensure_active_schema(schema, required=not new_active)
        self._write_students(updated, schema)
        self._commit_students(updated, schema)
        return replacement

    edit_student = update_student

    def create_backup(self) -> Path:
        """Create a non-overwriting backup of the canonical CSV."""

        self._require_available()
        return self._create_backup(self.csv_path)

    def migrate_to_vorname_header(self) -> Optional[Path]:
        """Replace legacy ``Forname`` with ``Vorname``, with a backup first."""

        self._require_available()
        schema = self._schema_or_raise()
        if schema.first_name_header == "Vorname":
            return None
        backup = self._create_backup(self.csv_path)
        migrated = _CsvSchema("Vorname", schema.has_active)
        self._write_students(self._students, migrated)
        self._schema = migrated
        return backup

    @staticmethod
    def _read_students(path: Path) -> Tuple[Tuple[Student, ...], _CsvSchema]:
        if not path.exists():
            raise RegistryValidationError(f"CSV file does not exist: {path}")
        if not path.is_file():
            raise RegistryValidationError(f"CSV path is not a file: {path}")

        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";", strict=True)
            if reader.fieldnames is None:
                raise RegistryValidationError("CSV header is missing.")
            stripped_headers = [header.strip() for header in reader.fieldnames]
            if len(stripped_headers) != len(set(stripped_headers)):
                raise RegistryValidationError("CSV contains duplicate headers.")
            header_map = dict(zip(stripped_headers, reader.fieldnames))
            if "Nummer" not in header_map or "Nachname" not in header_map:
                raise RegistryValidationError(
                    "CSV must contain Nummer and Nachname columns."
                )
            first_headers = [name for name in ("Vorname", "Forname") if name in header_map]
            if not first_headers:
                raise RegistryValidationError(
                    "CSV must contain either Vorname or legacy Forname."
                )
            first_header = "Vorname" if "Vorname" in first_headers else "Forname"
            schema = _CsvSchema(first_header, "Aktiv" in header_map)

            students = []
            numbers = set()
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise RegistryValidationError(
                        f"Row {row_number} has more fields than the header."
                    )
                values = ["" if value is None else str(value) for value in row.values()]
                if not any(value.strip() for value in values):
                    continue
                raw_number = row.get(header_map["Nummer"], "")
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
                if number in numbers:
                    raise RegistryValidationError(
                        f"Duplicate student number {number} in row {row_number}."
                    )
                numbers.add(number)

                last_name = _clean_display(
                    row.get(header_map["Nachname"], ""),
                    f"row {row_number} Nachname",
                )
                first_name = _clean_display(
                    row.get(header_map[first_header], ""),
                    f"row {row_number} {first_header}",
                )
                active_value = (
                    row.get(header_map["Aktiv"])
                    if schema.has_active
                    else None
                )
                students.append(
                    Student(number, last_name, first_name, _parse_active(active_value, row_number))
                )

        if not students:
            raise RegistryValidationError("CSV contains no student rows.")
        return tuple(students), schema

    @staticmethod
    def _read_aliases(
        path: Path,
        students_by_number: Mapping[int, Student],
    ) -> Dict[str, _Alias]:
        if not path.exists():
            return {}
        if not path.is_file():
            raise RegistryValidationError(f"Alias path is not a file: {path}")
        with path.open("r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise RegistryValidationError("Alias JSON root must be an object.")
        version = payload.get("version", 1)
        if version != 1:
            raise RegistryValidationError(f"Unsupported alias format version: {version!r}.")
        raw_aliases = payload.get("aliases", {})
        entries = []
        if isinstance(raw_aliases, dict):
            # Compatibility with the original compact {alias: number} layout.
            entries = [
                {"name": alias_name, "student_number": student_number}
                for alias_name, student_number in raw_aliases.items()
            ]
        elif isinstance(raw_aliases, list):
            entries = raw_aliases
        else:
            raise RegistryValidationError("aliases must be an object or a list.")

        aliases: Dict[str, _Alias] = {}
        for position, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                raise RegistryValidationError(f"Alias entry {position} must be an object.")
            name = _clean_display(entry.get("name"), f"alias entry {position} name")
            try:
                student_number = int(entry.get("student_number"))
            except (TypeError, ValueError) as exc:
                raise RegistryValidationError(
                    f"Alias entry {position} has an invalid student_number."
                ) from exc
            if student_number not in students_by_number:
                raise RegistryValidationError(
                    f"Alias {name!r} refers to unknown student {student_number}."
                )
            key = normalize_name(name)
            existing = aliases.get(key)
            if existing and existing.student_number != student_number:
                raise RegistryValidationError(
                    f"Alias {name!r} is assigned to more than one student."
                )
            aliases[key] = _Alias(name, student_number)
        return aliases

    @staticmethod
    def _validate_alias_conflicts(
        students: Sequence[Student], aliases: Mapping[str, _Alias]
    ) -> None:
        canonical: Dict[str, set[int]] = {}
        for student in students:
            for key in StudentRegistry._student_keys(student):
                canonical.setdefault(key, set()).add(student.number)
        for key, alias in aliases.items():
            conflicts = canonical.get(key, set()) - {alias.student_number}
            if conflicts:
                raise RegistryValidationError(
                    f"Alias {alias.name!r} conflicts with a canonical student name."
                )

    @staticmethod
    def _student_keys(student: Student) -> Tuple[str, str]:
        return (
            normalize_name(f"{student.last_name} {student.first_name}"),
            normalize_name(f"{student.first_name} {student.last_name}"),
        )

    def _canonical_candidates(self, key: str) -> Tuple[Student, ...]:
        found: Dict[int, Student] = {}
        for student in self._students:
            if key in self._student_keys(student):
                found[student.number] = student
        return tuple(sorted(found.values(), key=lambda item: item.number))

    def _exact_candidates(self, key: str) -> Tuple[Student, ...]:
        found = {item.number: item for item in self._canonical_candidates(key)}
        alias = self._aliases.get(key)
        if alias:
            found[alias.student_number] = self._students_by_number[alias.student_number]
        return tuple(sorted(found.values(), key=lambda item: item.number))

    def _fuzzy_suggestions(self, key: str) -> Tuple[StudentSuggestion, ...]:
        aliases_by_student: Dict[int, list[str]] = {}
        for alias_key, alias in self._aliases.items():
            aliases_by_student.setdefault(alias.student_number, []).append(alias_key)

        ranked = []
        for student in self._students:
            candidate_keys = list(self._student_keys(student))
            candidate_keys.extend(aliases_by_student.get(student.number, ()))
            score = max(
                SequenceMatcher(None, key, candidate).ratio()
                for candidate in candidate_keys
            )
            if score >= self.fuzzy_threshold:
                ranked.append(StudentSuggestion(student, round(score, 6)))
        ranked.sort(key=lambda item: (-item.score, item.student.number))
        return tuple(ranked[:self.suggestion_limit])

    def _ensure_unique_canonical(
        self,
        last_name: str,
        first_name: str,
        *,
        except_number: Optional[int] = None,
    ) -> None:
        key = (
            normalize_name(last_name),
            normalize_name(first_name),
        )
        for student in self._students:
            if student.number == except_number:
                continue
            existing = (
                normalize_name(student.last_name),
                normalize_name(student.first_name),
            )
            if existing == key:
                raise RegistryValidationError(
                    "A canonical student with this first and last name already exists."
                )

    def _ensure_active_schema(self, schema: _CsvSchema, *, required: bool) -> _CsvSchema:
        if schema.has_active or not required:
            return schema
        # Adding Aktiv is a schema migration, so preserve the original first.
        self._create_backup(self.csv_path)
        return _CsvSchema(schema.first_name_header, True)

    def _write_students(
        self,
        students: Sequence[Student],
        schema: _CsvSchema,
    ) -> None:
        self._validate_students_for_write(students)
        output = io.StringIO(newline="")
        fieldnames = ["Nummer", "Nachname", schema.first_name_header]
        if schema.has_active:
            fieldnames.append("Aktiv")
        writer = csv.DictWriter(
            output,
            fieldnames=fieldnames,
            delimiter=";",
            lineterminator="\r\n",
            extrasaction="raise",
        )
        writer.writeheader()
        for student in students:
            row = {
                "Nummer": student.number,
                "Nachname": student.last_name,
                schema.first_name_header: student.first_name,
            }
            if schema.has_active:
                row["Aktiv"] = "1" if student.active else "0"
            writer.writerow(row)
        self._atomic_write_bytes(self.csv_path, output.getvalue().encode("utf-8-sig"))

    @staticmethod
    def _validate_students_for_write(students: Sequence[Student]) -> None:
        if not students:
            raise RegistryValidationError("At least one student row is required.")
        numbers = set()
        for student in students:
            if not isinstance(student.number, int) or student.number <= 0:
                raise RegistryValidationError("Every student number must be a positive integer.")
            if student.number in numbers:
                raise RegistryValidationError(
                    f"Duplicate student number {student.number}."
                )
            numbers.add(student.number)
            _clean_display(student.last_name, "last_name")
            _clean_display(student.first_name, "first_name")
            if not isinstance(student.active, bool):
                raise RegistryValidationError("active must be true or false.")

    def _write_aliases(self, aliases: Mapping[str, _Alias]) -> None:
        payload = {
            "version": 1,
            "aliases": [
                {
                    "name": alias.name,
                    "student_number": alias.student_number,
                }
                for _, alias in sorted(aliases.items())
            ],
        }
        encoded = (
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        self._atomic_write_bytes(self.aliases_path, encoded)

    @staticmethod
    def _atomic_write_bytes(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            if isinstance(exc, StudentRegistryError):
                raise
            raise RegistryWriteError(f"Could not atomically write {path}: {exc}") from exc

    @classmethod
    def _create_backup(cls, path: Path) -> Path:
        if not path.exists():
            raise RegistryValidationError(f"Cannot back up missing file: {path}")
        candidate = path.with_name(f"{path.name}.bak")
        counter = 1
        while candidate.exists():
            candidate = path.with_name(f"{path.name}.bak.{counter}")
            counter += 1
        # Copy metadata after an atomic content write; failure never changes source.
        cls._atomic_write_bytes(candidate, path.read_bytes())
        try:
            shutil.copystat(path, candidate)
        except OSError:
            pass
        return candidate

    def _commit_students(
        self,
        students: Sequence[Student],
        schema: _CsvSchema,
    ) -> None:
        self._students = tuple(students)
        self._students_by_number = {item.number: item for item in students}
        self._schema = schema

    def _require_available(self) -> None:
        if not self.is_available:
            raise RegistryUnavailableError(
                self._load_error or "Student registry is unavailable."
            )

    def _schema_or_raise(self) -> _CsvSchema:
        self._require_available()
        if self._schema is None:  # Defensive guard for type checkers and corruption.
            raise RegistryUnavailableError("Student registry schema is unavailable.")
        return self._schema


__all__ = [
    "MatchStatus",
    "RegistryUnavailableError",
    "RegistryValidationError",
    "RegistryWriteError",
    "Student",
    "StudentMatch",
    "StudentRegistry",
    "StudentRegistryError",
    "StudentSuggestion",
    "normalize_name",
]
