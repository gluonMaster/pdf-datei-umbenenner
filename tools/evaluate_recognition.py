"""Measure recognition quality on real scans without changing any data.

The stand runs the CLI pipeline in dry-run mode and compares every planned
decision with a local reference that never belongs in Git:

* ``_expected.csv`` next to the scans (columns ``Datei;Nachname;Vorname;Fach``), or
* a folder of already renamed PDFs, whose names
  ``Nachname_Vorname_Fach_Monat_Jahr[_N].pdf`` are the reference.

An automatic decision is expected only when the reference student exists in
the registry used and the reference subject is in the current subject list.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date
from enum import Enum
import hashlib
from pathlib import Path
import re
import sys
import time
import unicodedata
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from file_renamer import FileRenamer  # noqa: E402
from processing_service import PlannedAction  # noqa: E402
from validator import Validator  # noqa: E402

EXPECTED_FILENAME = "_expected.csv"
EXPECTED_HEADERS = ("Datei", "Nachname", "Vorname", "Fach")

EXIT_OK = 0
EXIT_WRONG_AUTOMATIC = 1
EXIT_USAGE = 2
EXIT_DATA_CHANGED = 3


class Outcome(str, Enum):
    CORRECT_AUTO = "richtig automatisch"
    WRONG_AUTO = "FALSCH automatisch"
    JUSTIFIED_REVIEW = "berechtigte Prüfung"
    UNNECESSARY_REVIEW = "unnötige Prüfung"
    ERROR = "Fehler"
    UNLABELED = "ohne Referenz"


@dataclass(frozen=True)
class Expectation:
    """Reference student (filename spelling) and canonical subject."""

    student_key: str
    subject: str


@dataclass(frozen=True)
class EvaluationRow:
    file_name: str
    outcome: Outcome
    expected: str
    decided: str
    seconds: float


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip().casefold()


def student_key(last_name: str, first_name: str) -> str:
    """Compare names the way they appear in planned filenames."""

    sanitize = FileRenamer.sanitize_filename
    return _normalize(f"{sanitize(last_name.strip())}_{sanitize(first_name.strip())}")


_MONTHS = {_normalize(name) for name in FileRenamer.MONTH_NAMES_DE.values()}


def parse_renamed_filename(file_name: str) -> Optional[Expectation]:
    """Read the reference from ``Nachname_Vorname_Fach_Monat_Jahr[_N].pdf``.

    Surname and first name are kept together, so spaces replaced by ``_``
    inside either part do not require guessing the split.
    """

    path = Path(file_name)
    if path.suffix.casefold() != ".pdf":
        return None
    parts = unicodedata.normalize("NFC", path.stem).split("_")
    if len(parts) >= 6 and parts[-1].isdigit() and re.fullmatch(r"\d{4}", parts[-2]):
        parts = parts[:-1]
    if len(parts) < 5 or not re.fullmatch(r"\d{4}", parts[-1]):
        return None
    if _normalize(parts[-2]) not in _MONTHS:
        return None
    subject = parts[-3]
    name_parts = parts[:-3]
    if len(name_parts) < 2 or not subject or not all(name_parts):
        return None
    return Expectation(_normalize("_".join(name_parts)), subject)


def load_expected_csv(path: Path) -> Dict[str, Expectation]:
    """Load ``Datei;Nachname;Vorname;Fach`` keyed by case-folded file name."""

    text = Path(path).read_text(encoding="utf-8-sig")
    reader = csv.DictReader(text.splitlines(), delimiter=";")
    headers = tuple(header.strip() for header in (reader.fieldnames or ()))
    missing = [header for header in EXPECTED_HEADERS if header not in headers]
    if missing:
        raise ValueError(
            f"Referenzdatei {path.name}: Spalten fehlen: {', '.join(missing)}."
        )
    expectations: Dict[str, Expectation] = {}
    for line_number, row in enumerate(reader, start=2):
        values = {key.strip(): (value or "").strip() for key, value in row.items() if key}
        if not any(values.values()):
            continue
        if not all(values.get(header) for header in EXPECTED_HEADERS):
            raise ValueError(f"Referenzdatei {path.name}, Zeile {line_number}: leeres Feld.")
        file_key = values["Datei"].casefold()
        if file_key in expectations:
            raise ValueError(
                f"Referenzdatei {path.name}, Zeile {line_number}: Datei doppelt."
            )
        expectations[file_key] = Expectation(
            student_key(values["Nachname"], values["Vorname"]),
            values["Fach"],
        )
    return expectations


def automatic_expected(
    expectation: Expectation,
    registry_keys: Set[str],
    subject_catalog: Set[str],
) -> bool:
    return (
        expectation.student_key in registry_keys
        and _normalize(expectation.subject) in subject_catalog
    )


def classify(
    expectation: Optional[Expectation],
    result,
    registry_keys: Set[str],
    subject_catalog: Set[str],
) -> Outcome:
    if getattr(result, "error", None):
        return Outcome.ERROR
    if expectation is None:
        return Outcome.UNLABELED
    action = getattr(result, "planned_action", None)
    if action == PlannedAction.RENAME:
        student = getattr(getattr(result, "student_match", None), "student", None)
        subject = getattr(result, "canonical_subject", None) or ""
        if (
            student is not None
            and student_key(student.last_name, student.first_name) == expectation.student_key
            and _normalize(subject) == _normalize(expectation.subject)
        ):
            return Outcome.CORRECT_AUTO
        return Outcome.WRONG_AUTO
    if action == PlannedAction.MANUAL_REVIEW:
        if automatic_expected(expectation, registry_keys, subject_catalog):
            return Outcome.UNNECESSARY_REVIEW
        return Outcome.JUSTIFIED_REVIEW
    return Outcome.ERROR


def exit_code(rows: Iterable[EvaluationRow], data_changed: bool) -> int:
    if data_changed:
        return EXIT_DATA_CHANGED
    if any(row.outcome == Outcome.WRONG_AUTO for row in rows):
        return EXIT_WRONG_AUTOMATIC
    return EXIT_OK


def _file_digest(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(folder: Path, data_files: Sequence[Path]) -> Dict[str, Optional[str]]:
    """Digests of every PDF and data file that a dry-run must not change."""

    paths = sorted(
        {*(item for item in folder.iterdir() if item.suffix.casefold() == ".pdf"), *data_files}
    )
    state = {str(path): _file_digest(path) for path in paths}
    state[str(folder / "manual_check")] = "exists" if (folder / "manual_check").exists() else None
    return state


def _natural_key(path: Path):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", path.name)]


def _describe_expected(expectation: Optional[Expectation]) -> str:
    if expectation is None:
        return "—"
    return f"{expectation.student_key} / {expectation.subject}"


def _describe_decision(result) -> str:
    if getattr(result, "error", None):
        return f"Fehler: {result.error}"
    if result.planned_action == PlannedAction.RENAME:
        student = result.student_match.student
        return f"{student_key(student.last_name, student.first_name)} / {result.canonical_subject}"
    found = result.found_data
    raw_name = getattr(found, "raw_student_name", None) or "?"
    raw_subject = getattr(found, "raw_subject", None) or "?"
    reason = result.review_item.reason if result.review_item else ""
    return f"Prüfung [{raw_name} / {raw_subject}]: {reason[:160]}"


def format_summary(rows: Sequence[EvaluationRow]) -> List[str]:
    counts = {outcome: 0 for outcome in Outcome}
    for row in rows:
        counts[row.outcome] += 1
    seconds = [row.seconds for row in rows]
    lines = [f"Dokumente: {len(rows)}"]
    lines.extend(f"  {outcome.value}: {counts[outcome]}" for outcome in Outcome)
    if seconds:
        lines.append(
            f"Zeit pro Dokument: Mittel {sum(seconds) / len(seconds):.2f} s, "
            f"Maximum {max(seconds):.2f} s"
        )
    return lines


def _subject_catalog() -> Set[str]:
    subjects = Validator._load_subjects_config().get("subjects", [])
    return {_normalize(subject) for subject in subjects}


def build_argument_parser() -> argparse.ArgumentParser:
    today = date.today()
    parser = argparse.ArgumentParser(
        description=(
            "Messstand: Dry-run der Erkennung mit Vergleich gegen eine lokale "
            "Referenz. Ändert keine PDF-, CSV- oder JSON-Dateien."
        )
    )
    parser.add_argument("folder", help="Ordner mit den zu prüfenden PDF-Dateien")
    parser.add_argument(
        "--expected",
        help=f"Referenz-CSV (Standard: {EXPECTED_FILENAME} im Ordner)",
    )
    parser.add_argument(
        "--from-filenames",
        action="store_true",
        help="Referenz aus Dateinamen bereits umbenannter PDFs lesen",
    )
    parser.add_argument("--registry", help="Alternative Schuler.csv, z. B. eine temporäre Kopie")
    parser.add_argument("--month", type=int, choices=range(1, 13), default=today.month)
    parser.add_argument("--year", type=int, default=today.year)
    parser.add_argument("--tesseract-path", help="Optionaler Pfad zu tesseract.exe")
    parser.add_argument(
        "--report",
        help=(
            "Detailtabelle zusätzlich in diese Datei schreiben, z. B. "
            "recognition_report.txt (enthält Namen, nur lokal; von Git ignoriert)"
        ),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    args = build_argument_parser().parse_args(argv)
    folder = Path(args.folder).resolve()
    if not folder.is_dir():
        print(f"Fehler: Ordner {folder} existiert nicht.")
        return EXIT_USAGE

    expected_path = Path(args.expected) if args.expected else folder / EXPECTED_FILENAME
    expectations: Mapping[str, Expectation] = {}
    if not args.from_filenames:
        if not expected_path.is_file():
            print(
                f"Fehler: Referenz {expected_path} fehlt. "
                "Alternativ --from-filenames für umbenannte PDFs verwenden."
            )
            return EXIT_USAGE
        try:
            expectations = load_expected_csv(expected_path)
        except (OSError, UnicodeError, ValueError, csv.Error) as error:
            print(f"Fehler: {error}")
            return EXIT_USAGE

    import main as cli  # imported late: tests may replace pipeline parts first

    registry_csv = Path(args.registry).resolve() if args.registry else None
    service = cli.build_processing_service(
        folder,
        tesseract_path=args.tesseract_path,
        registry_csv=registry_csv,
    )
    registry = service.student_registry
    if not registry.is_available:
        print(f"Fehler: Schülerverzeichnis nicht verfügbar: {registry.load_error}")
        return EXIT_USAGE
    registry_keys = {student_key(item.last_name, item.first_name) for item in registry.students}
    catalog = _subject_catalog()

    data_files = [
        registry.csv_path,
        registry.aliases_path,
        folder / "manual_review.json",
        PROJECT_ROOT / "subjects_config.json",
        PROJECT_ROOT / "subjects_aliases.json",
    ]
    before = snapshot(folder, data_files)

    pdf_paths = sorted(
        (item for item in folder.iterdir() if item.is_file() and item.suffix.casefold() == ".pdf"),
        key=_natural_key,
    )
    service.renamer.reset_reservations()
    rows: List[EvaluationRow] = []
    for pdf_path in pdf_paths:
        expectation = (
            parse_renamed_filename(pdf_path.name)
            if args.from_filenames
            else expectations.get(pdf_path.name.casefold())
        )
        started = time.perf_counter()
        result = service.process_document(pdf_path, args.month, args.year, dry_run=True)
        elapsed = time.perf_counter() - started
        rows.append(EvaluationRow(
            pdf_path.name,
            classify(expectation, result, registry_keys, catalog),
            _describe_expected(expectation),
            _describe_decision(result),
            elapsed,
        ))

    data_changed = snapshot(folder, data_files) != before

    detail = [
        f"{row.file_name}\t{row.outcome.value}\t{row.seconds:.2f} s\n"
        f"    erwartet:    {row.expected}\n"
        f"    entschieden: {row.decided}"
        for row in rows
    ]
    summary = format_summary(rows)
    if data_changed:
        summary.append("FEHLER: Daten wurden während des Dry-runs verändert!")
    output = "\n".join([*detail, "", *summary])
    print(output)
    if args.report:
        Path(args.report).write_text(output + "\n", encoding="utf-8")
    return exit_code(rows, data_changed)


if __name__ == "__main__":
    sys.exit(main())
