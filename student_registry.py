"""Safe canonical student registry backed by a semicolon-separated CSV file.

The registry is deliberately independent from the PDF parser and Tkinter.  A
downstream pipeline can pass the parser's raw name to :meth:`StudentRegistry.match`
and route every non-exact result to manual review.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from enum import Enum
import hashlib
import io
import json
import math
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
class StudentAlias:
    """Public, read-only view of one confirmed student alias."""

    name: str
    student_number: int


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
    fieldnames: Tuple[str, ...] = ()
    extra_values: Mapping[int, Mapping[str, str]] = field(default_factory=dict)
    encoding: str = "utf-8-sig"


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
        raise TypeError("Der Schülername muss Text sein.")
    normalized = unicodedata.normalize("NFC", value)
    return " ".join(normalized.split()).casefold()


def _clean_display(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise RegistryValidationError(f"{field_name} muss Text sein.")
    cleaned = " ".join(unicodedata.normalize("NFC", value).split())
    if not cleaned:
        raise RegistryValidationError(f"{field_name} darf nicht leer sein.")
    if any(unicodedata.category(char) in {"Cc", "Cs"} for char in cleaned):
        raise RegistryValidationError(f"{field_name} enthält ungültige Steuerzeichen.")
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
        f"Zeile {row_number}: Aktiv muss Ja/Nein beziehungsweise 1/0 sein; "
        f"erhalten: {value!r}."
    )


def _parse_student_number(value: object) -> int:
    """Accept integer IDs and decimal text, never truncate floats or booleans."""

    if type(value) is int:
        number = value
    elif isinstance(value, str) and value.strip().isascii() and value.strip().isdecimal():
        number = int(value.strip())
    else:
        raise RegistryValidationError("Die Schülernummer muss eine positive Ganzzahl sein.")
    if number <= 0:
        raise RegistryValidationError("Die Schülernummer muss eine positive Ganzzahl sein.")
    return number


def _unique_json_object(pairs):
    """Do not silently discard conflicting keys in a damaged alias file."""

    result = {}
    for key, value in pairs:
        if key in result:
            raise RegistryValidationError(f"Doppelter Schlüssel in Alias-JSON: {key!r}.")
        result[key] = value
    return result


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
        if type(suggestion_limit) is not int or suggestion_limit < 1:
            raise ValueError("Die Anzahl der Vorschläge muss mindestens 1 sein.")
        if not 0.0 <= fuzzy_threshold <= 1.0:
            raise ValueError(
                "Der Schwellenwert für unscharfe Treffer muss zwischen 0 und 1 liegen."
            )
        if not 0.0 <= ambiguity_threshold <= 1.0:
            raise ValueError(
                "Der Schwellenwert für Mehrdeutigkeiten muss zwischen 0 und 1 liegen."
            )
        if not math.isfinite(ambiguity_delta) or ambiguity_delta < 0.0:
            raise ValueError("Die Toleranz für Mehrdeutigkeiten darf nicht negativ sein.")

        self.suggestion_limit = suggestion_limit
        self.fuzzy_threshold = fuzzy_threshold
        self.ambiguity_threshold = ambiguity_threshold
        self.ambiguity_delta = ambiguity_delta
        self._students: Tuple[Student, ...] = ()
        self._students_by_number: Dict[int, Student] = {}
        self._aliases: Dict[str, _Alias] = {}
        self._aliases_need_migration = False
        self._schema: Optional[_CsvSchema] = None
        self._load_error: Optional[str] = None
        self._source_hashes: Optional[tuple[str, Optional[str]]] = None
        self.reload()

    @property
    def students(self) -> Tuple[Student, ...]:
        return self._students

    @property
    def aliases(self) -> Tuple[StudentAlias, ...]:
        """Return aliases in deterministic display order."""

        return tuple(
            StudentAlias(alias.name, alias.student_number)
            for _, alias in sorted(
                self._aliases.items(),
                key=lambda item: (item[1].student_number, item[0]),
            )
        )

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
            source_hashes = self._read_source_hashes()
            students, schema = self._read_students(self.csv_path)
            by_number = {student.number: student for student in students}
            aliases, aliases_need_migration = self._read_aliases(self.aliases_path, by_number)
            self._validate_alias_conflicts(students, aliases)
            if source_hashes != self._read_source_hashes():
                raise RegistryValidationError("Das Schülerverzeichnis wurde beim Laden geändert.")
        except (OSError, UnicodeError, csv.Error, json.JSONDecodeError,
                RegistryValidationError, TypeError, ValueError) as exc:
            self._students = ()
            self._students_by_number = {}
            self._aliases = {}
            self._aliases_need_migration = False
            self._schema = None
            self._source_hashes = None
            self._load_error = (
                f"Das Schülerverzeichnis ist nicht verfügbar: {exc}. "
                "Betroffene Dokumente müssen manuell geprüft werden."
            )
            return False

        self._students = students
        self._students_by_number = by_number
        self._aliases = aliases
        self._aliases_need_migration = aliases_need_migration
        self._schema = schema
        self._load_error = None
        self._source_hashes = source_hashes
        return True

    def _read_source_hashes(self) -> tuple[str, Optional[str]]:
        csv_hash = hashlib.sha256(self.csv_path.read_bytes()).hexdigest()
        try:
            aliases_hash = hashlib.sha256(self.aliases_path.read_bytes()).hexdigest()
        except FileNotFoundError:
            aliases_hash = None
        return csv_hash, aliases_hash

    def checked_fingerprint(self) -> str:
        """Identify the loaded files and reject writes from an outdated matcher.

        This detects external edits, including extra CSV columns and aliases.
        It is an optimistic check, not an inter-process file lock.
        """
        self._require_available()
        try:
            current = self._read_source_hashes()
        except OSError as exc:
            raise RegistryValidationError(
                "Das Schülerverzeichnis kann nicht geprüft werden; bitte aktualisieren."
            ) from exc
        if current != self._source_hashes:
            raise RegistryValidationError(
                "Das Schülerverzeichnis wurde außerhalb der Anwendung geändert; "
                "bitte zuerst „Aktualisieren“ wählen und eine neue Vorschau erstellen."
            )
        identity = (str(self.csv_path.resolve()), str(self.aliases_path.resolve()), current)
        return hashlib.sha256(repr(identity).encode("utf-8")).hexdigest()

    def match(self, raw_name: str) -> StudentMatch:
        """Resolve a parser name without mutating or guessing a student."""

        if not self.is_available:
            return StudentMatch(
                MatchStatus.ERROR,
                None,
                (),
                self._load_error or "Das Schülerverzeichnis ist nicht verfügbar.",
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
                "Der erkannte Schülername ist leer; eine manuelle Bestätigung ist erforderlich.",
            )

        return self._match_key(key, self._exact_candidates(key))

    def _match_key(self, key: str, exact: Tuple[Student, ...]) -> StudentMatch:
        if len(exact) == 1:
            student = exact[0]
            return StudentMatch(
                MatchStatus.EXACT,
                student,
                (),
                f"Exakter Treffer im Schülerverzeichnis oder über einen bestätigten Alias "
                f"für Schüler Nr. {student.number}.",
            )
        if len(exact) > 1:
            ranked = tuple(StudentSuggestion(item, 1.0) for item in exact)
            return StudentMatch(
                MatchStatus.AMBIGUOUS,
                None,
                exact,
                "Mehrere Schüler haben denselben normalisierten Namen; "
                "bitte manuell auswählen.",
                ranked,
            )

        ranked = self._fuzzy_suggestions(key)
        status = MatchStatus.UNKNOWN
        reason = (
            "Kein exakter Schüler oder bestätigter Alias gefunden; "
            "eine manuelle Bestätigung ist erforderlich."
        )
        if (
            len(ranked) >= 2
            and ranked[0].score >= self.ambiguity_threshold
            and ranked[1].score >= self.ambiguity_threshold
            and ranked[0].score - ranked[1].score <= self.ambiguity_delta
        ):
            status = MatchStatus.AMBIGUOUS
            reason = "Mehrere ähnlich passende Schüler erfordern eine manuelle Auswahl."
        ranked = ranked[:self.suggestion_limit]
        suggestions = tuple(item.student for item in ranked)
        return StudentMatch(status, None, suggestions, reason, ranked)

    def match_parts(self, last_name: str, first_name: str) -> StudentMatch:
        """Match known field boundaries without exchanging first and last names."""

        if not self.is_available:
            return self.match("")
        try:
            last_key = normalize_name(_clean_display(last_name, "Nachname"))
            first_key = normalize_name(_clean_display(first_name, "Vorname"))
        except RegistryValidationError as exc:
            return StudentMatch(MatchStatus.UNKNOWN, None, (), str(exc))
        exact = tuple(sorted(
            (student for student in self._students
             if (normalize_name(student.last_name), normalize_name(student.first_name))
             == (last_key, first_key)),
            key=lambda student: student.number,
        ))
        return self._match_key(f"{last_key} {first_key}", exact)

    # Readable aliases for callers at different integration boundaries.
    match_name = match
    resolve = match

    def select_existing(self, student_number: int) -> Student:
        """Return a manually selected canonical row without changing files."""

        self._require_available()
        try:
            return self._students_by_number[_parse_student_number(student_number)]
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryValidationError(
                f"Unbekannte Schülernummer: {student_number!r}."
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

        alias_name = _clean_display(raw_name, "Erkannter Name")
        alias_key = normalize_name(alias_name)
        existing = self._aliases.get(alias_key)
        if existing:
            if existing.student_number != student.number:
                raise RegistryValidationError(
                    "Dieser Alias ist bereits einem anderen Schüler zugeordnet."
                )
            return student

        canonical_candidates = self._canonical_candidates(alias_key)
        conflicting = [item for item in canonical_candidates if item.number != student.number]
        if conflicting:
            raise RegistryValidationError(
                "Der Alias entspricht exakt dem Namen eines anderen Schülers."
            )
        if canonical_candidates:
            return student  # A redundant alias for the same row need not be written.

        updated = dict(self._aliases)
        updated[alias_key] = _Alias(alias_name, student.number)
        self._write_aliases(updated)
        self._aliases = updated
        return student

    add_alias = confirm_existing

    def remove_alias(self, alias_name: str) -> bool:
        """Remove one erroneous alias without changing its canonical student."""

        self._require_available()
        clean_name = _clean_display(alias_name, "Alias")
        alias_key = normalize_name(clean_name)
        if alias_key not in self._aliases:
            return False

        updated = dict(self._aliases)
        del updated[alias_key]
        self._write_aliases(updated)
        self._aliases = updated
        return True

    def add_student(
        self,
        last_name: str,
        first_name: str,
        *,
        active: bool = True,
    ) -> Student:
        """Explicitly add a canonical student using the next stable number."""

        self._require_available()
        clean_last = _clean_display(last_name, "Nachname")
        clean_first = _clean_display(first_name, "Vorname")
        if not isinstance(active, bool):
            raise RegistryValidationError("Der Aktiv-Status muss wahr oder falsch sein.")
        self._ensure_unique_canonical(clean_last, clean_first)

        number = max(self._students_by_number, default=0) + 1
        student = Student(number, clean_last, clean_first, active)
        updated = self._students + (student,)
        self._validate_alias_conflicts(updated, self._aliases)
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
            current.last_name if last_name is None else _clean_display(last_name, "Nachname")
        )
        clean_first = (
            current.first_name
            if first_name is None
            else _clean_display(first_name, "Vorname")
        )
        new_active = current.active if active is None else active
        if not isinstance(new_active, bool):
            raise RegistryValidationError("Der Aktiv-Status muss wahr oder falsch sein.")
        replacement = Student(current.number, clean_last, clean_first, new_active)
        if replacement == current:
            return current
        if (normalize_name(clean_last), normalize_name(clean_first)) != (
            normalize_name(current.last_name), normalize_name(current.first_name)
        ):
            self._ensure_unique_canonical(clean_last, clean_first, except_number=current.number)
        updated = tuple(
            replacement if item.number == current.number else item
            for item in self._students
        )
        self._validate_alias_conflicts(updated, self._aliases)
        schema = self._schema_or_raise()
        schema = self._ensure_active_schema(schema, required=not new_active)
        self._write_students(updated, schema)
        self._commit_students(updated, schema)
        return replacement

    edit_student = update_student

    def merge_students(
        self,
        students: Iterable[Student],
        *,
        create_backup: bool = True,
    ) -> Optional[Path]:
        """Atomically append explicitly reviewed rows without deleting history.

        This bulk operation is used by the registry import service so a monthly
        import is one all-or-nothing CSV replacement instead of a sequence of
        partially committed ``add_student`` calls.  Existing numbers and names
        are never overwritten by this method.
        """

        self._require_available()
        additions = tuple(students)
        if not additions:
            return None
        self._validate_students_for_write(additions)
        additions = tuple(
            replace(student, last_name=_clean_display(student.last_name, "Nachname"),
                    first_name=_clean_display(student.first_name, "Vorname"))
            for student in additions
        )

        existing_numbers = set(self._students_by_number)
        addition_numbers = set()
        addition_names = set()
        for student in additions:
            if student.number in existing_numbers:
                raise RegistryValidationError(
                    f"Die Schülernummer {student.number} ist bereits vorhanden."
                )
            if student.number in addition_numbers:
                raise RegistryValidationError(
                    f"Die Schülernummer {student.number} kommt in den neuen Einträgen mehrfach vor."
                )
            addition_numbers.add(student.number)
            self._ensure_unique_canonical(student.last_name, student.first_name)
            name_key = (
                normalize_name(student.last_name),
                normalize_name(student.first_name),
            )
            if name_key in addition_names:
                raise RegistryValidationError(
                    "Ein Schüler mit diesem Vor- und Nachnamen ist bereits vorhanden."
                )
            addition_names.add(name_key)

        updated = tuple(sorted(self._students + additions, key=lambda item: item.number))
        self._validate_students_for_write(updated)
        self._validate_alias_conflicts(updated, self._aliases)
        schema = self._schema_or_raise()
        needs_active_migration = (
            not schema.has_active and any(not student.active for student in additions)
        )
        backup = (
            self._create_backup(self.csv_path)
            if create_backup or len(additions) > 1 or needs_active_migration
            else None
        )
        if needs_active_migration:
            schema = replace(schema, has_active=True, fieldnames=schema.fieldnames + ("Aktiv",))
        self._write_students(updated, schema)
        self._commit_students(updated, schema)
        return backup

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
        migrated = replace(
            schema, first_name_header="Vorname",
            fieldnames=tuple("Vorname" if name == "Forname" else name
                             for name in schema.fieldnames),
        )
        self._write_students(self._students, migrated)
        self._schema = migrated
        return backup

    @staticmethod
    def _read_students(path: Path) -> Tuple[Tuple[Student, ...], _CsvSchema]:
        if not path.exists():
            raise RegistryValidationError(f"CSV-Datei ist nicht vorhanden: {path}")
        if not path.is_file():
            raise RegistryValidationError(f"CSV-Pfad verweist nicht auf eine Datei: {path}")

        with path.open("rb") as handle:
            encoding = "utf-8-sig" if handle.read(3) == b"\xef\xbb\xbf" else "utf-8"
        with path.open("r", encoding=encoding, newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";", strict=True)
            if reader.fieldnames is None:
                raise RegistryValidationError("CSV-Kopfzeile fehlt.")
            stripped_headers = [header.strip() for header in reader.fieldnames]
            if not all(stripped_headers):
                raise RegistryValidationError("CSV enthält eine leere Spaltenüberschrift.")
            if len(stripped_headers) != len(set(stripped_headers)):
                raise RegistryValidationError("CSV enthält doppelte Spaltenüberschriften.")
            header_map = dict(zip(stripped_headers, reader.fieldnames))
            if "Nummer" not in header_map or "Nachname" not in header_map:
                raise RegistryValidationError(
                    "CSV muss die Spalten Nummer und Nachname enthalten."
                )
            first_headers = [name for name in ("Vorname", "Forname") if name in header_map]
            if not first_headers:
                raise RegistryValidationError(
                    "CSV muss entweder Vorname oder die veraltete Spalte Forname enthalten."
                )
            first_header = "Vorname" if "Vorname" in first_headers else "Forname"
            extra_values = {}
            schema = _CsvSchema(
                first_header, "Aktiv" in header_map, tuple(stripped_headers),
                extra_values, encoding,
            )

            students = []
            numbers = set()
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise RegistryValidationError(
                        f"Zeile {row_number} enthält mehr Felder als die Kopfzeile."
                    )
                values = ["" if value is None else str(value) for value in row.values()]
                if not any(value.strip() for value in values):
                    continue
                if any(value is None for value in row.values()):
                    raise RegistryValidationError(f"Zeile {row_number} enthält zu wenige Felder.")
                raw_number = row.get(header_map["Nummer"], "")
                try:
                    number = _parse_student_number(raw_number)
                except (TypeError, ValueError) as exc:
                    raise RegistryValidationError(
                        f"Zeile {row_number}: Nummer muss eine Ganzzahl sein."
                    ) from exc
                if number <= 0:
                    raise RegistryValidationError(
                        f"Zeile {row_number}: Nummer muss positiv sein."
                    )
                if number in numbers:
                    raise RegistryValidationError(
                        f"Doppelte Schülernummer {number} in Zeile {row_number}."
                    )
                numbers.add(number)

                last_name = _clean_display(
                    row.get(header_map["Nachname"], ""),
                    f"Zeile {row_number} Nachname",
                )
                first_name = _clean_display(
                    row.get(header_map[first_header], ""),
                    f"Zeile {row_number} {first_header}",
                )
                if len(first_headers) == 2 and normalize_name(first_name) != normalize_name(
                    _clean_display(row[header_map["Forname"]], f"Zeile {row_number} Forname")
                ):
                    raise RegistryValidationError(
                        f"Zeile {row_number}: Forname und Vorname widersprechen sich."
                    )
                extra_values[number] = {
                    name: row[raw_header] for name, raw_header in header_map.items()
                    if name not in {"Nummer", "Nachname", "Forname", "Vorname", "Aktiv"}
                }
                active_value = (
                    row.get(header_map["Aktiv"])
                    if schema.has_active
                    else None
                )
                students.append(
                    Student(number, last_name, first_name, _parse_active(active_value, row_number))
                )

        if not students:
            raise RegistryValidationError("CSV enthält keine Schülerzeilen.")
        return tuple(students), schema

    @staticmethod
    def _read_aliases(
        path: Path,
        students_by_number: Mapping[int, Student],
    ) -> Tuple[Dict[str, _Alias], bool]:
        if not path.exists():
            return {}, False
        if not path.is_file():
            raise RegistryValidationError(f"Alias-Pfad verweist nicht auf eine Datei: {path}")
        with path.open("r", encoding="utf-8-sig") as handle:
            payload = json.load(handle, object_pairs_hook=_unique_json_object)
        if not isinstance(payload, dict):
            raise RegistryValidationError(
                "Das Wurzelelement der Alias-JSON-Datei muss ein Objekt sein."
            )
        version = payload.get("version", 1)
        if type(version) is not int or version != 1:
            raise RegistryValidationError(
                f"Nicht unterstützte Version des Aliasformats: {version!r}."
            )
        if "aliases" not in payload:
            raise RegistryValidationError("Das Feld 'aliases' fehlt in der Alias-JSON-Datei.")
        raw_aliases = payload["aliases"]
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
            raise RegistryValidationError(
                "Das Feld 'aliases' muss ein Objekt oder eine Liste sein."
            )

        aliases: Dict[str, _Alias] = {}
        for position, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                raise RegistryValidationError(
                    f"Aliaseintrag {position} muss ein Objekt sein."
                )
            name = _clean_display(
                entry.get("name"), f"Name in Aliaseintrag {position}"
            )
            try:
                student_number = _parse_student_number(entry.get("student_number"))
            except (TypeError, ValueError) as exc:
                raise RegistryValidationError(
                    f"Aliaseintrag {position} enthält im Feld 'student_number' "
                    "eine ungültige Schülernummer."
                ) from exc
            if student_number not in students_by_number:
                raise RegistryValidationError(
                    f"Alias {name!r} verweist auf die unbekannte Schülernummer "
                    f"{student_number}."
                )
            key = normalize_name(name)
            existing = aliases.get(key)
            if existing and existing.student_number != student_number:
                raise RegistryValidationError(
                    f"Alias {name!r} ist mehreren Schülern zugeordnet."
                )
            aliases[key] = _Alias(name, student_number)
        return aliases, isinstance(raw_aliases, dict)

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
                    f"Alias {alias.name!r} kollidiert mit einem offiziellen Schülernamen."
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
        return tuple(ranked)

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
                    "Ein Schüler mit diesem Vor- und Nachnamen ist bereits vorhanden."
                )

    def _ensure_active_schema(self, schema: _CsvSchema, *, required: bool) -> _CsvSchema:
        if schema.has_active or not required:
            return schema
        # Adding Aktiv is a schema migration, so preserve the original first.
        self._create_backup(self.csv_path)
        return replace(schema, has_active=True, fieldnames=schema.fieldnames + ("Aktiv",))

    def _write_students(
        self,
        students: Sequence[Student],
        schema: _CsvSchema,
    ) -> None:
        self._validate_students_for_write(students)
        self._validate_alias_conflicts(students, self._aliases)
        output = io.StringIO(newline="")
        fieldnames = schema.fieldnames
        writer = csv.DictWriter(
            output,
            fieldnames=fieldnames,
            delimiter=";",
            lineterminator="\r\n",
            extrasaction="raise",
        )
        writer.writeheader()
        for student in students:
            row = dict(schema.extra_values.get(student.number, {}))
            row.update({
                "Nummer": student.number,
                "Nachname": student.last_name,
                schema.first_name_header: student.first_name,
            })
            for name in ("Forname", "Vorname"):
                if name in fieldnames:
                    row[name] = student.first_name
            if schema.has_active:
                row["Aktiv"] = "1" if student.active else "0"
            writer.writerow(row)
        encoded = output.getvalue().encode(schema.encoding)
        self.checked_fingerprint()
        self._atomic_write_bytes(self.csv_path, encoded)
        self._source_hashes = (hashlib.sha256(encoded).hexdigest(), self._source_hashes[1])

    @staticmethod
    def _validate_students_for_write(students: Sequence[Student]) -> None:
        if not students:
            raise RegistryValidationError("Mindestens ein Schülereintrag ist erforderlich.")
        numbers = set()
        for student in students:
            if not isinstance(student, Student) or type(student.number) is not int or student.number <= 0:
                raise RegistryValidationError(
                    "Jede Schülernummer muss eine positive Ganzzahl sein."
                )
            if student.number in numbers:
                raise RegistryValidationError(
                    f"Doppelte Schülernummer {student.number}."
                )
            numbers.add(student.number)
            _clean_display(student.last_name, "Nachname")
            _clean_display(student.first_name, "Vorname")
            if not isinstance(student.active, bool):
                raise RegistryValidationError(
                    "Der Aktiv-Status muss wahr oder falsch sein."
                )

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
        self.checked_fingerprint()
        if self._aliases_need_migration:
            self._create_backup(self.aliases_path)
        self._atomic_write_bytes(self.aliases_path, encoded)
        self._source_hashes = (self._source_hashes[0], hashlib.sha256(encoded).hexdigest())
        self._aliases_need_migration = False

    @staticmethod
    def _atomic_write_bytes(path: Path, data: bytes) -> None:
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception as exc:
            cleanup_detail = ""
            try:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            except OSError as cleanup_error:
                cleanup_detail = (
                    f" Temporäre Datei konnte nicht entfernt werden: "
                    f"{temporary} ({cleanup_error})."
                )
            if isinstance(exc, StudentRegistryError):
                raise
            raise RegistryWriteError(
                f"Datei {path} konnte nicht atomar gespeichert werden: {exc}"
                + cleanup_detail
            ) from exc

    @classmethod
    def _create_backup(cls, path: Path) -> Path:
        if not path.exists():
            raise RegistryValidationError(
                f"Fehlende Datei kann nicht gesichert werden: {path}"
            )
        candidate = path.with_name(f"{path.name}.bak")
        counter = 1
        while candidate.exists():
            candidate = path.with_name(f"{path.name}.bak.{counter}")
            counter += 1
        # Copy metadata after an atomic content write; failure never changes source.
        cls._atomic_write_bytes(candidate, path.read_bytes())
        try:
            shutil.copystat(path, candidate)
        except OSError as error:
            print(
                f"Warnung: Sicherung wurde unter {candidate} gespeichert, "
                f"aber Dateimetadaten konnten nicht übernommen werden: {error}"
            )
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
                self._load_error or "Das Schülerverzeichnis ist nicht verfügbar."
            )

    def _schema_or_raise(self) -> _CsvSchema:
        self._require_available()
        if self._schema is None:  # Defensive guard for type checkers and corruption.
            raise RegistryUnavailableError(
                "Das Schema des Schülerverzeichnisses ist nicht verfügbar."
            )
        return self._schema


__all__ = [
    "MatchStatus",
    "RegistryUnavailableError",
    "RegistryValidationError",
    "RegistryWriteError",
    "Student",
    "StudentAlias",
    "StudentMatch",
    "StudentRegistry",
    "StudentRegistryError",
    "StudentSuggestion",
    "normalize_name",
]
