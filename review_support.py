"""Display-independent helpers for the review tab.

Kept free of Tkinter so the search, preselection and status rules can be
tested without a display.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from name_matching import compare_name, name_tokens

# Ranking kinds shown to the operator.
RECOMMENDED = "recommended"
MATCH = "match"
SIMILAR = "similar"

# Candidates below this similarity are listed normally, not as recommended.
RECOMMEND_SCORE = 0.65
# A recommended candidate is preselected only if it leads clearly.
PRESELECT_SCORE = 0.70
PRESELECT_MARGIN = 0.15
SIMILAR_SCORE = 0.6


@dataclass(frozen=True)
class StudentChoice:
    """One row of the student search list, bound to a stable number."""

    number: int
    label: str
    kind: str


def _choice(student, kind: str) -> StudentChoice:
    marker = {RECOMMENDED: "★ ", SIMILAR: "≈ "}.get(kind, "")
    return StudentChoice(student.number, f"{marker}Nr. {student.number} — {student.full_name}", kind)


def candidate_numbers(item) -> List[int]:
    """Ranked candidate numbers of a queue entry, oldest formats included."""

    numbers = [
        entry.get("number") for entry in (getattr(item, "student_candidates", None) or [])
        if isinstance(entry.get("number"), int)
        and isinstance(entry.get("score"), (int, float))
        and entry["score"] >= RECOMMEND_SCORE
    ]
    canonical = getattr(item, "canonical_student_number", None)
    if isinstance(canonical, int) and canonical not in numbers:
        numbers.insert(0, canonical)
    return numbers


def rank_students(students: Sequence, query: str, recommended: Sequence[int] = ()) -> List[StudentChoice]:
    """Search by name parts and number in any order; keep recommended order.

    Every query token must occur in the number, last or first name. Without
    plain matches, names similar to the query are offered and marked.
    """

    by_number = {student.number: student for student in students}
    recommended = [number for number in dict.fromkeys(recommended) if number in by_number]
    rank = {number: index for index, number in enumerate(recommended)}
    tokens = name_tokens(query)

    def alphabetical(student):
        return (student.last_name.casefold(), student.first_name.casefold(), student.number)

    if not tokens:
        chosen = [_choice(by_number[number], RECOMMENDED) for number in recommended]
        rest = sorted(
            (student for student in students if student.number not in rank), key=alphabetical
        )
        return chosen + [_choice(student, MATCH) for student in rest]

    def searchable(student) -> List[str]:
        return [str(student.number)] + name_tokens(f"{student.last_name} {student.first_name}")

    def quality(student) -> Optional[int]:
        fields = searchable(student)
        score = 0
        for token in tokens:
            if token in fields:
                score += 2
            elif any(field.startswith(token) for field in fields):
                score += 1
            elif not any(token in field for field in fields):
                return None
        return score

    matches = []
    for student in students:
        score = quality(student)
        if score is not None:
            matches.append((-score, rank.get(student.number, len(rank)), alphabetical(student), student))
    if matches:
        matches.sort(key=lambda entry: entry[:3])
        return [
            _choice(student, RECOMMENDED if student.number in rank else MATCH)
            for *_key, student in matches
        ]
    if len("".join(tokens)) < 3:
        return []
    similar = []
    for student in students:
        comparison = compare_name(query, student.last_name, student.first_name)
        if comparison.score >= SIMILAR_SCORE:
            similar.append((-comparison.score, alphabetical(student), student))
    similar.sort(key=lambda entry: entry[:2])
    return [_choice(student, SIMILAR) for *_key, student in similar]


def preselected_number(item) -> Optional[int]:
    """The candidate to select in advance, or ``None`` if none leads clearly."""

    canonical = getattr(item, "canonical_student_number", None)
    if isinstance(canonical, int):
        return canonical
    candidates = [
        entry for entry in (getattr(item, "student_candidates", None) or [])
        if isinstance(entry.get("number"), int) and isinstance(entry.get("score"), (int, float))
    ]
    if not candidates or candidates[0]["score"] < PRESELECT_SCORE:
        return None
    if len(candidates) > 1 and candidates[0]["score"] - candidates[1]["score"] < PRESELECT_MARGIN:
        return None
    return candidates[0]["number"]


def status_lines(item) -> List[str]:
    """Short German statements of what needs a decision."""

    lines: List[str] = []
    status = getattr(item, "student_match_status", None)
    reason = getattr(item, "reason", "") or ""
    if status == "exact":
        lines.append("Schüler: im Verzeichnis gefunden.")
    elif status == "auto_corrected":
        lines.append("Schüler: automatisch korrigiert.")
    elif "Geburtsdatum" in reason and "weicht" in reason:
        lines.append("Schüler: Geburtsdatum weicht vom Verzeichnis ab – bitte prüfen.")
    elif status == "ambiguous":
        lines.append("Schüler: mehrere passende Einträge – bitte auswählen.")
    elif status == "error":
        lines.append("Schüler: Verzeichnis nicht verfügbar.")
    elif getattr(item, "raw_name", None):
        if preselected_number(item) is not None:
            lines.append("Schüler: ähnlicher Eintrag vorgeschlagen – bitte bestätigen.")
        else:
            lines.append("Schüler: nicht im Verzeichnis – neuer Schüler?")
    else:
        lines.append("Schüler: Name nicht lesbar.")

    proposal = getattr(item, "subject_proposal", None)
    if getattr(item, "canonical_subject", None):
        if "Fach" in reason and ("Abweichende" in reason or "Unbekannte" in reason):
            lines.append("Fach: erkannt, aber mit abweichenden Werten – bitte prüfen.")
    elif proposal:
        lines.append(f"Fach: unbekannt – Vorschlag „{proposal}“ als neues Fach.")
    elif "Widersprüchliche" in reason:
        lines.append("Fach: widersprüchliche Werte – bitte auswählen.")
    else:
        lines.append("Fach: nicht erkannt – bitte auswählen.")
    return lines


def field_boxes(item) -> Dict[str, Tuple[float, float, float, float]]:
    """Page boxes of name, birth date and subject from the field evidence."""

    boxes = {}
    for entry in getattr(item, "field_evidence", None) or []:
        box = entry.get("bbox") if isinstance(entry, dict) else None
        field = entry.get("field") if isinstance(entry, dict) else None
        if field and isinstance(box, dict):
            try:
                boxes[field] = (
                    float(box["x0"]), float(box["top"]), float(box["x1"]), float(box["bottom"])
                )
            except (KeyError, TypeError, ValueError):
                continue
    return boxes


def parse_operator_date(text: str) -> Optional[date]:
    """``TT.MM.JJJJ`` from the review form; empty means unknown."""

    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError as error:
        raise ValueError(
            f"Geburtsdatum „{text}“ hat nicht das Format TT.MM.JJJJ."
        ) from error


def format_iso_date(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return date.fromisoformat(value).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return ""


def add_subject_to_config(config_path: Path, subject: str) -> bool:
    """Add a canonical subject atomically; returns whether it was new.

    All other keys, subjects and aliases of the file are preserved.
    """

    subject = " ".join((subject or "").split())
    if not subject:
        raise ValueError("Der Fachname darf nicht leer sein.")
    config_path = Path(config_path)
    data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError(f"{config_path.name} enthält kein JSON-Objekt.")
    subjects = data.setdefault("subjects", [])
    if not isinstance(subjects, list):
        raise ValueError(f"{config_path.name}: 'subjects' muss eine Liste sein.")
    if any(str(item).casefold() == subject.casefold() for item in subjects):
        return False
    subjects.append(subject)
    subjects.sort(key=str.casefold)
    data.setdefault("aliases", {})
    payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(
        dir=config_path.parent, prefix=f".{config_path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, config_path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
    return True


def is_value_list(raw: str) -> bool:
    """A parser list of several values ("Deutsch, Englisch"), never one alias."""

    return any(separator in (raw or "") for separator in (",", ";", "/", "+"))


__all__: Iterable[str] = (
    "MATCH",
    "RECOMMENDED",
    "SIMILAR",
    "StudentChoice",
    "add_subject_to_config",
    "candidate_numbers",
    "field_boxes",
    "format_iso_date",
    "is_value_list",
    "parse_operator_date",
    "preselected_number",
    "rank_students",
    "status_lines",
)
