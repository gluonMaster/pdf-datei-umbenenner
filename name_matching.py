"""OCR-aware comparison of recognized names and birth dates.

Substitutions that OCR typically makes (``i``/``l``/``1``, ``o``/``a``,
``rn``/``m`` ...) are cheap; every other change is a "real" edit that hints at
a different spelling and therefore possibly a different person.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import unicodedata
from typing import List, Optional, Sequence, Tuple

# Cyrillic letters that scanner OCR produced inside German or Latin words.
LATIN_LOOKALIKES = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "Х": "X", "У": "Y", "І": "I", "Ј": "J",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "к": "k", "т": "t", "і": "i", "ј": "j", "ı": "i",
})


def latinize(value: str) -> str:
    return value.translate(LATIN_LOOKALIKES)


# Confusions frequent in OCR of printed text: thin vertical strokes, closed
# round letters and digit look-alikes. Pairs such as a/e are deliberately
# missing: they would let one real name (Anna) pass for another (Anne).
_CONFUSABLE_LETTERS = {
    frozenset(pair) for pair in (
        "il", "i1", "l1", "ij", "lt", "it", "tf", "fl",
        "oa", "o0", "ec", "un", "nh", "hb", "mn",
        "s5", "b8", "g9", "z2",
    )
}
# Character groups OCR merges or splits.
_CONFUSABLE_GROUPS = (("rn", "m"), ("cl", "d"), ("vv", "w"), ("ii", "u"), ("ri", "n"))
CONFUSION_COST = 0.3

_CONFUSABLE_DIGITS = {
    frozenset(pair) for pair in ("06", "08", "68", "38", "56", "35", "17", "14")
}


def name_tokens(text: str) -> List[str]:
    """Lower-case letter tokens without diacritics; hyphens split tokens."""

    decomposed = unicodedata.normalize("NFKD", latinize(text).casefold())
    cleaned = "".join(
        char if char.isalnum() else " "
        for char in decomposed
        if not unicodedata.combining(char)
    )
    return cleaned.split()


def ocr_distance(first: str, second: str) -> Tuple[float, int]:
    """Weighted edit distance and the number of non-confusion edits."""

    rows, columns = len(first) + 1, len(second) + 1
    weighted = [[0.0] * columns for _ in range(rows)]
    real = [[0] * columns for _ in range(rows)]
    for row in range(1, rows):
        weighted[row][0] = float(row)
        real[row][0] = row
    for column in range(1, columns):
        weighted[0][column] = float(column)
        real[0][column] = column
    for row in range(1, rows):
        for column in range(1, columns):
            a, b = first[row - 1], second[column - 1]
            if a == b:
                substitution = (0.0, 0)
            elif frozenset((a, b)) in _CONFUSABLE_LETTERS:
                substitution = (CONFUSION_COST, 0)
            else:
                substitution = (1.0, 1)
            options = [
                (weighted[row - 1][column - 1] + substitution[0],
                 real[row - 1][column - 1] + substitution[1]),
                (weighted[row - 1][column] + 1.0, real[row - 1][column] + 1),
                (weighted[row][column - 1] + 1.0, real[row][column - 1] + 1),
            ]
            for group, single in _CONFUSABLE_GROUPS:
                for left, right in ((group, single), (single, group)):
                    if (
                        row >= len(left) and column >= len(right)
                        and first[row - len(left):row] == left
                        and second[column - len(right):column] == right
                    ):
                        options.append((
                            weighted[row - len(left)][column - len(right)] + CONFUSION_COST,
                            real[row - len(left)][column - len(right)],
                        ))
            weighted[row][column], real[row][column] = min(options)
    return weighted[-1][-1], real[-1][-1]


@dataclass(frozen=True)
class NameComparison:
    """Similarity of a recognized name to one registry spelling (0.0 to 1.0)."""

    score: float
    minimum_part_score: float
    real_edits: int
    swapped: bool = False


def compare_name(reading: str, last_name: str, first_name: str) -> NameComparison:
    """Compare in both name orders, part by part when token counts agree."""

    tokens = name_tokens(reading)
    last, first = name_tokens(last_name), name_tokens(first_name)
    if not tokens or not (last or first):
        return NameComparison(0.0, 0.0, max(len("".join(tokens)), 1))
    best: Optional[NameComparison] = None
    for swapped, order in ((False, last + first), (True, first + last)):
        if len(order) == len(tokens):
            pairs = list(zip(tokens, order))
        else:
            pairs = [("".join(tokens), "".join(order))]
        distances = [ocr_distance(a, b) for a, b in pairs]
        lengths = [max(len(a), len(b)) for a, b in pairs]
        score = 1.0 - sum(weighted for weighted, _ in distances) / max(sum(lengths), 1)
        parts = [
            1.0 - weighted / max(length, 1)
            for (weighted, _), length in zip(distances, lengths)
        ]
        candidate = NameComparison(
            round(score, 4), round(min(parts), 4), sum(real for _, real in distances), swapped
        )
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def compare_birth_dates(read: date, stored: date) -> str:
    """``match``, ``near`` (one confusable digit) or ``contradiction``."""

    if read == stored:
        return "match"
    first, second = read.strftime("%d%m%Y"), stored.strftime("%d%m%Y")
    differences = [(a, b) for a, b in zip(first, second) if a != b]
    if len(differences) == 1 and frozenset(differences[0]) in _CONFUSABLE_DIGITS:
        return "near"
    return "contradiction"


def same_spelling(first: str, second: str) -> bool:
    return name_tokens(first) == name_tokens(second)


def proposal_parts(reading: str) -> Optional[Tuple[str, str]]:
    """Split a printed "Nachname Vorname" into its parts for a new student."""

    parts = [part for part in latinize(reading).split() if part]
    if len(parts) < 2:
        return None
    return parts[0], " ".join(parts[1:])


__all__: Sequence[str] = (
    "CONFUSION_COST",
    "NameComparison",
    "compare_birth_dates",
    "compare_name",
    "latinize",
    "name_tokens",
    "ocr_distance",
    "proposal_parts",
    "same_spelling",
)
