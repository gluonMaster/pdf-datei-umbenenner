"""Layout-aware parsing for legacy and attendance PDF forms."""

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from name_matching import latinize as _latinize
from page_data import ExtractionSource, PageData, PageWord
from text_parser import TextParser


class FormType(str, Enum):
    LEGACY = "legacy"
    ATTENDANCE = "attendance"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BoundingBox:
    x0: float
    top: float
    x1: float
    bottom: float

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2

    def to_dict(self) -> Dict[str, float]:
        return {
            "x0": round(self.x0, 2),
            "top": round(self.top, 2),
            "x1": round(self.x1, 2),
            "bottom": round(self.bottom, 2),
        }


@dataclass(frozen=True)
class AnchorMatch:
    name: str
    box: BoundingBox
    words: Tuple[PageWord, ...]
    errors: int = 0


@dataclass(frozen=True)
class FormDetection:
    form_type: FormType
    confidence: float
    reasons: Tuple[str, ...]
    matched_anchors: Tuple[str, ...] = ()


# Sources of a field value.
SOURCE_OCR_PAGE = "ocr_page"
SOURCE_OCR_FIELD = "ocr_field"
SOURCE_TEXT_LAYER = "text_layer"


@dataclass(frozen=True)
class FieldReading:
    """One reading of a field from one source."""

    source: str
    value: str
    confidence: Optional[float] = None

    def to_dict(self) -> dict:
        return {"source": self.source, "value": self.value, "confidence": self.confidence}


@dataclass(frozen=True)
class FieldEvidence:
    """Where a field was found and what every reading said.

    ``bbox`` uses the coordinates of the upright page of the primary reading.
    """

    field: str
    value: Optional[str]
    canonical: Optional[str] = None
    bbox: Optional[BoundingBox] = None
    source: Optional[str] = None
    localization: Optional[str] = None
    readings: Tuple[FieldReading, ...] = ()
    uncertainties: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "value": self.value,
            "canonical": self.canonical,
            "bbox": self.bbox.to_dict() if self.bbox else None,
            "source": self.source,
            "localization": self.localization,
            "readings": [reading.to_dict() for reading in self.readings],
            "uncertainties": list(self.uncertainties),
        }


@dataclass(frozen=True)
class FormParseResult:
    """Structured parse output, including evidence for manual review.

    ``warnings`` block automatic renaming; ``notes`` are informational, for
    example a column boundary that was estimated while the column content
    stayed consistent.
    """

    form_type: FormType
    raw_student_name: Optional[str]
    student_name: Optional[str]
    raw_subject: Optional[str]
    subject: Optional[str]
    confidence: float
    reasons: Tuple[str, ...]
    warnings: Tuple[str, ...] = ()
    name_confidence: Optional[float] = None
    subject_confidence: Optional[float] = None
    birth_date: Optional[date] = None
    raw_birth_date: Optional[str] = None
    subject_proposal: Optional[str] = None
    notes: Tuple[str, ...] = ()
    evidence: Tuple[FieldEvidence, ...] = ()

    @property
    def raw_name(self) -> Optional[str]:
        """Short compatibility alias for the uncanonicalized student name."""

        return self.raw_student_name

    @property
    def canonical_name(self) -> Optional[str]:
        """Short compatibility alias used by downstream processing."""

        return self.student_name

    @property
    def canonical_student_name(self) -> Optional[str]:
        return self.student_name

    @property
    def canonical_subject(self) -> Optional[str]:
        return self.subject

    @property
    def diagnostic_reasons(self) -> Tuple[str, ...]:
        return self.reasons

    @property
    def requires_review(self) -> bool:
        return (
            self.form_type == FormType.UNKNOWN
            or not self.student_name
            or not self.subject
            or bool(self.warnings)
            or self.confidence < 0.75
            or (
                self.name_confidence is not None
                and self.name_confidence < OCR_FIELD_CONFIDENCE_THRESHOLD
            )
            or (
                self.subject_confidence is not None
                and self.subject_confidence < OCR_FIELD_CONFIDENCE_THRESHOLD
                and not self._subject_is_consistent_table_value()
            )
        )

    def _subject_is_consistent_table_value(self) -> bool:
        evidence = next((item for item in self.evidence if item.field == "subject"), None)
        return bool(evidence and evidence.localization == "table_column_consistent")

    def as_legacy_tuple(self) -> Tuple[Optional[str], Optional[str]]:
        return self.student_name, self.subject


_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_LATIN = re.compile(r"[A-Za-z]")


def _latinize_mixed(token: str) -> str:
    """Repair Latin words with stray Cyrillic look-alikes; keep pure Cyrillic."""

    if "ı" in token or (_CYRILLIC.search(token) and _LATIN.search(token)):
        return _latinize(token)
    return token


def _normalized_key(value: str) -> str:
    """Normalize anchors while tolerating punctuation and OCR whitespace."""

    decomposed = unicodedata.normalize("NFKD", _latinize(value).casefold())
    return "".join(char for char in decomposed if char.isalnum())


def _edit_distance(first: str, second: str, limit: int) -> int:
    """Levenshtein distance, returning ``limit + 1`` once it is exceeded."""

    if abs(len(first) - len(second)) > limit:
        return limit + 1
    previous = list(range(len(second) + 1))
    for row, first_char in enumerate(first, 1):
        current = [row]
        for column, second_char in enumerate(second, 1):
            current.append(min(
                previous[column] + 1,
                current[column - 1] + 1,
                previous[column - 1] + (first_char != second_char),
            ))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


OCR_FIELD_CONFIDENCE_THRESHOLD = 0.70
# Share of a label's letters that may be misread; "Ort" must match exactly,
# "Anwesenheitsnachweis" tolerates five errors.
ANCHOR_ERROR_RATIO = 0.25


def _reading_source(page: PageData) -> str:
    source = getattr(page.source, "value", page.source)
    return SOURCE_OCR_PAGE if source == ExtractionSource.OCR.value else SOURCE_TEXT_LAYER


def _ocr_confidence_for_text(page: PageData, value: Optional[str]) -> Optional[float]:
    """Return mean word confidence for a parsed OCR field, if traceable."""

    source = getattr(page.source, "value", page.source)
    if source != "ocr" or not value:
        return None
    target = _normalized_key(value)
    if not target:
        return None
    words = [word for word in _ordered_words(page) if _normalized_key(word.text)]
    matched_confidences: List[float] = []
    for start in range(len(words)):
        combined = ""
        selected: List[PageWord] = []
        for word in words[start:start + 12]:
            combined += _normalized_key(word.text)
            selected.append(word)
            if combined == target:
                confidences = [
                    word.confidence for word in selected if word.confidence is not None
                ]
                if len(confidences) != len(selected):
                    continue
                matched_confidences.extend(confidences)
                break
            if len(combined) >= len(target) or not target.startswith(combined):
                break
    if not matched_confidences:
        return None
    return sum(matched_confidences) / len(matched_confidences)


def _low_ocr_confidence(label: str, confidence: Optional[float]) -> Optional[str]:
    if confidence is not None and confidence < OCR_FIELD_CONFIDENCE_THRESHOLD:
        return (
            f"Niedrige OCR-Konfidenz für {label} ({confidence:.0%}); "
            "keine automatische Umbenennung."
        )
    return None


def _box_for(words: Sequence[PageWord]) -> BoundingBox:
    return BoundingBox(
        x0=min(word.x0 for word in words),
        top=min(word.top for word in words),
        x1=max(word.x1 for word in words),
        bottom=max(word.bottom for word in words),
    )


def _ordered_words(page: PageData) -> List[PageWord]:
    return [word for row in _group_rows(page.words, page.height) for word in row]


def _anchor_parts(value: str) -> List[str]:
    # Keep punctuation boundaries, including a suffix such as
    # "Lehrkraft(Druckbuchstaben)", without requiring OCR word boundaries.
    return [_normalized_key(part) for part in re.findall(r"[^\W_]+", value)]


def _anchor_spans(parts: Sequence[str], label: str) -> Iterable[Tuple[int, int, int]]:
    """Yield ``(start, end, errors)`` for runs of parts that spell the label.

    A bounded number of letter errors is tolerated, so OCR variants such as
    "Daucr" or "Anwesenheitsnachwels" still locate their labels.
    """

    target = _normalized_key(label)
    allowed = int(len(target) * ANCHOR_ERROR_RATIO)
    for start in range(len(parts)):
        combined = ""
        for end in range(start, len(parts)):
            combined += parts[end]
            if len(combined) > len(target) + allowed:
                break
            if len(combined) < len(target) - allowed:
                continue
            errors = _edit_distance(combined, target, allowed)
            if errors <= allowed:
                yield start, end + 1, errors


def _best_spans(parts: Sequence[str], label: str) -> List[Tuple[int, int, int]]:
    """Non-overlapping spans, preferring fewer errors and then longer runs."""

    per_start: Dict[int, Tuple[int, int]] = {}
    for start, end, errors in _anchor_spans(parts, label):
        current = per_start.get(start)
        if current is None or (errors, -(end - start)) < (current[1], -(current[0] - start)):
            per_start[start] = (end, errors)
    chosen: List[Tuple[int, int, int]] = []
    used: set = set()
    for start, (end, errors) in sorted(per_start.items(), key=lambda item: (item[1][1], item[0])):
        if used.intersection(range(start, end)):
            continue
        used.update(range(start, end))
        chosen.append((start, end, errors))
    return sorted(chosen)


def _find_anchors(page: PageData, label: str) -> List[AnchorMatch]:
    """Locate an anchor even when OCR has split or misread one of its words."""

    matches: List[AnchorMatch] = []
    for row in _group_rows(page.words, page.height):
        parts = [(part, word) for word in row for part in _anchor_parts(word.text)]
        for start, end, errors in _best_spans([part for part, _ in parts], label):
            selected = tuple(dict.fromkeys(word for _, word in parts[start:end]))
            # Do not join unrelated labels across separate columns.
            if any(
                right.x0 - left.x1 > page.width * 0.04
                for left, right in zip(selected, selected[1:])
            ):
                continue
            matches.append(AnchorMatch(label, _box_for(selected), selected, errors))
    return matches


def _find_anchor(page: PageData, label: str) -> Optional[AnchorMatch]:
    matches = _find_anchors(page, label)
    return min(matches, key=lambda match: match.errors) if matches else None


def _group_rows(words: Iterable[PageWord], page_height: float) -> List[List[PageWord]]:
    """Group words into lines by their vertical centers.

    The tolerance absorbs baseline jitter of skewed photocopies (words of one
    printed line differ by a few points) while table rows stay separate.
    """

    tolerance = page_height * 0.006
    rows: List[List[PageWord]] = []
    row_centers: List[float] = []
    for word in sorted(words, key=lambda item: (item.center_y, item.x0)):
        for index, row_center in enumerate(row_centers):
            if abs(word.center_y - row_center) <= tolerance:
                rows[index].append(word)
                row_centers[index] = sum(item.center_y for item in rows[index]) / len(rows[index])
                break
        else:
            rows.append([word])
            row_centers.append(word.center_y)
    for row in rows:
        row.sort(key=lambda item: item.x0)
    rows.sort(key=lambda row: min(word.top for word in row))
    return rows


def _localization(anchor: AnchorMatch) -> str:
    return "anchor" if anchor.errors == 0 else "anchor_ocr_tolerant"


def _field_words(page: PageData, box: BoundingBox) -> Tuple[PageWord, ...]:
    """Re-recognize one page region as a single line, if an image is available."""

    image = getattr(page, "image", None)
    reader = getattr(image, "reader", None)
    if image is None or reader is None or page.width <= 0 or page.height <= 0:
        return ()
    scale_x = image.width / page.width
    scale_y = image.height / page.height
    pad_x = page.width * 0.01
    pad_y = page.height * 0.004
    pixel_box = (
        (box.x0 - pad_x) * scale_x,
        (box.top - pad_y) * scale_y,
        (box.x1 + pad_x) * scale_x,
        (box.bottom + pad_y) * scale_y,
    )
    try:
        recognized = reader(image, pixel_box)
    except Exception:
        return ()
    return tuple(
        PageWord(
            text=word.text,
            x0=word.left / scale_x,
            top=word.top / scale_y,
            x1=(word.left + word.width) / scale_x,
            bottom=(word.top + word.height) / scale_y,
            confidence=max(0.0, min(1.0, float(word.confidence))),
        )
        for word in recognized
        if word.text.strip()
    )


def _mean_confidence(words: Sequence[PageWord]) -> Optional[float]:
    values = [word.confidence for word in words if word.confidence is not None]
    if not values or len(values) != len(words):
        return None
    return sum(values) / len(values)


_DATE_TOKEN = re.compile(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}")
_DIGIT_LOOKALIKES = str.maketrans({
    "O": "0", "o": "0", "D": "0", "Q": "0", "l": "1", "I": "1", "|": "1",
    "i": "1", "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2",
})
_BIRTH_DATE = re.compile(
    r"(?<!\d)(\d{1,2})\s*[.,/-]?\s*(\d{1,2})\s*[.,/-]?\s*((?:19|20)\d{2})(?!\d)"
)


def parse_birth_date(text: str, today: Optional[date] = None) -> Optional[Tuple[date, str]]:
    """Find a plausible pupil's birth date in OCR text.

    Letters that OCR confuses with digits are repaired only inside tokens that
    already contain at least two digits. Returns the date and matched text.
    """

    today = today or date.today()
    tokens = [
        token.translate(_DIGIT_LOOKALIKES) if sum(char.isdigit() for char in token) >= 2 else token
        for token in text.split()
    ]
    candidate_text = " ".join(tokens)
    for match in _BIRTH_DATE.finditer(candidate_text):
        day, month, year = (int(group) for group in match.groups())
        try:
            value = date(year, month, day)
        except ValueError:
            continue
        if today.year - 30 <= value.year <= today.year - 2:
            return value, match.group(0)
    return None


class FormDetector:
    """Detect a form from normalized structural anchors, never its filename."""

    ATTENDANCE_ANCHORS = (
        "Anwesenheitsnachweis",
        "Name des Schülers",
        "Fach",
        "Name der Lehrkraft",
    )
    LEGACY_ANCHORS = (
        "Bewilligungszeitraum",
        "Monat",
        "Unterrichtsart",
        "Lehrer(in)",
    )

    def detect(self, page: PageData) -> FormDetection:
        text_parts = _anchor_parts(page.text)

        def has_anchor(label: str) -> bool:
            return bool(_find_anchor(page, label)) or next(
                iter(_anchor_spans(text_parts, label)), None
            ) is not None

        attendance = tuple(
            anchor
            for anchor in self.ATTENDANCE_ANCHORS
            if has_anchor(anchor)
        )
        legacy = tuple(
            anchor
            for anchor in self.LEGACY_ANCHORS
            if has_anchor(anchor)
        )

        if len(attendance) >= 3:
            confidence = 0.98 if len(attendance) == 4 else 0.86
            return FormDetection(
                FormType.ATTENDANCE,
                confidence,
                (
                    f"Anwesenheitsformular: {len(attendance)}/4 Strukturanker erkannt "
                    f"({', '.join(attendance)}).",
                ),
                attendance,
            )
        if len(legacy) >= 2:
            confidence = min(0.97, 0.65 + len(legacy) * 0.08)
            return FormDetection(
                FormType.LEGACY,
                confidence,
                (
                    f"Altes Formular: {len(legacy)}/4 Strukturanker erkannt "
                    f"({', '.join(legacy)}).",
                ),
                legacy,
            )

        reason = (
            "Formulartyp nicht sicher erkannt: "
            f"{len(attendance)}/4 Anwesenheitsanker und {len(legacy)}/4 Altformularanker."
        )
        return FormDetection(FormType.UNKNOWN, 0.0, (reason,))


class SubjectCatalog:
    """Canonical subjects, standard abbreviations and confirmed aliases.

    Exact normalized keys win. Otherwise a misread word may match one key
    with a few letter errors, provided no other subject is equally close.
    """

    MINIMUM_FUZZY_LENGTH = 5

    def __init__(self, entries: Dict[str, str]):
        self.entries = entries

    @classmethod
    def load(cls) -> "SubjectCatalog":
        entries: Dict[str, str] = {}
        for raw in TextParser.get_valid_subjects():
            canonical = TextParser.normalize_subject(raw)
            if canonical:
                entries[_normalized_key(raw)] = canonical
                entries[_normalized_key(canonical)] = canonical
        manager = TextParser._get_alias_manager()
        for alias, target in manager.get_all_aliases().items():
            canonical = TextParser.normalize_subject(target) or target
            if _normalized_key(alias):
                entries[_normalized_key(alias)] = canonical
        entries.pop("", None)
        return cls(entries)

    def match(self, raw: str) -> Optional[str]:
        key = _normalized_key(raw)
        if not key:
            return None
        if key in self.entries:
            return self.entries[key]
        if len(key) < self.MINIMUM_FUZZY_LENGTH:
            return None
        allowed = max(1, int(len(key) * ANCHOR_ERROR_RATIO))
        scored = sorted(
            (_edit_distance(key, candidate, allowed), canonical)
            for candidate, canonical in self.entries.items()
            if len(candidate) >= self.MINIMUM_FUZZY_LENGTH
        )
        close = [(distance, canonical) for distance, canonical in scored if distance <= allowed]
        if not close:
            return None
        best_distance = close[0][0]
        best = {canonical for distance, canonical in close if distance == best_distance}
        return best.pop() if len(best) == 1 else None


def _is_gr_ez_value(text: str) -> bool:
    """Values of the GR/EZ column (and their OCR variants) are not subjects."""

    key = _normalized_key(text.replace("&", ""))
    return bool(key) and len(key) <= 3 and set(key) <= set("ezgr2il17")


@dataclass(frozen=True)
class _NameField:
    value: Optional[str]
    box: Optional[BoundingBox]
    localization: Optional[str]
    words: Tuple[PageWord, ...] = ()
    birth_anchor: Optional[AnchorMatch] = None


_EXCLUDED_NAME_WORDS = {
    "name", "des", "schulers", "geburtsdatum", "hiermit", "wird",
    "fur", "folgenden", "schuler", "lehrkraft", "druckbuchstaben",
    "formular", "anwesenheitsnachweis", "unterschrift", "firma",
    "anbieter", "erziehungsberechtigten",
}
_NAME_PUNCTUATION = ".,:;()[]{}*_\"“”„'’`"


def _clean_name_candidate(words: Sequence[PageWord]) -> Optional[str]:
    """Return a plausible printed name from one line of words, or ``None``.

    Short fragments (for example signature strokes) are dropped; a longer
    token with digits or symbols makes the line implausible.
    """

    tokens: List[str] = []
    for word in sorted(words, key=lambda item: item.x0):
        token = word.text.strip(_NAME_PUNCTUATION)
        if not token or _DATE_TOKEN.fullmatch(token):
            continue
        token = _latinize_mixed(token)
        if not all(char.isalpha() or char in "-'’" for char in token):
            if len(token) <= 3:
                continue
            return None
        tokens.append(token)

    merged: List[str] = []
    for token in tokens:
        # OCR splits a leading capital such as "I van"; rejoin it.
        if merged and len(merged[-1]) == 1 and merged[-1].isupper() and token[:1].islower():
            merged[-1] += token
        else:
            merged.append(token)
    tokens = [token for token in merged if len(token) > 1]

    if not 2 <= len(tokens) <= 4:
        return None
    if sum(token[:1].isupper() for token in tokens) < 2:
        return None
    if any(_normalized_key(token) in _EXCLUDED_NAME_WORDS for token in tokens):
        return None
    return " ".join(tokens)


def _merge_readings(
    page: PageData,
    primary: Optional[str],
    primary_confidence: Optional[float],
    others: Sequence[FieldReading],
) -> Tuple[Optional[str], Tuple[FieldReading, ...], Optional[str]]:
    """Primary value if present, else the first other reading.

    Returns the chosen value, all readings and the chosen source. Comparing
    and correcting readings against the registry happens in matching.
    """

    readings: List[FieldReading] = []
    if primary:
        readings.append(FieldReading(_reading_source(page), primary, primary_confidence))
    readings.extend(reading for reading in others if reading.value)
    if not readings:
        return None, (), None
    return readings[0].value, tuple(readings), readings[0].source


class LegacyFormParser:
    """Preserve the successful parser behavior for the old top-line form."""

    def parse(self, page: PageData, detection: FormDetection) -> FormParseResult:
        header = self._header_text(page.text)
        name = TextParser.extract_student_name(header)
        subject = TextParser.extract_subject(header)
        raw_subject = self._raw_subject(header)
        reasons = list(detection.reasons)
        warnings: List[str] = []

        name_words = self._locate(page, name) if name else ()
        name_box = _box_for(name_words) if name_words else None
        other_readings: List[FieldReading] = []
        if name_box is not None:
            field_words = _field_words(page, name_box)
            field_name = TextParser.extract_student_name(
                " ".join(word.text for word in field_words)
            ) if field_words else None
            if field_name:
                other_readings.append(
                    FieldReading(SOURCE_OCR_FIELD, field_name, _mean_confidence(field_words))
                )
        for alternative in page.alternatives:
            alternative_name = TextParser.extract_student_name(
                self._header_text(alternative.text)
            )
            if alternative_name:
                other_readings.append(FieldReading(SOURCE_TEXT_LAYER, alternative_name))

        name_confidence = _ocr_confidence_for_text(page, name)
        chosen, readings, source = _merge_readings(page, name, name_confidence, other_readings)
        if chosen and not name:
            reasons.append("Schülername nur in einer zusätzlichen Lesung gefunden.")
            name_confidence = None
        name = chosen

        if name:
            reasons.append("Schülername aus dem Kopfbereich des alten Formulars gelesen.")
        else:
            warnings.append("Schülername im Kopfbereich nicht gefunden.")
        if subject:
            reasons.append("Fach aus dem Kopfbereich des alten Formulars gelesen.")
        else:
            warnings.append("Fach im Kopfbereich nicht gefunden.")

        confidence = detection.confidence
        if not name or not subject:
            confidence = min(confidence, 0.55)
        subject_confidence = _ocr_confidence_for_text(page, raw_subject or subject)
        for message in (
            _low_ocr_confidence("Schülername", name_confidence),
            _low_ocr_confidence("Fach", subject_confidence),
        ):
            if message:
                warnings.append(message)

        subject_proposal = None
        if not subject:
            proposal = self._subject_proposal(header)
            if proposal:
                subject_proposal = proposal
                raw_subject = raw_subject or proposal

        evidence = (
            FieldEvidence(
                "student_name", name, None, name_box, source,
                "header_line" if name_box else None, readings,
            ),
            FieldEvidence(
                "subject", raw_subject or subject, subject, None,
                _reading_source(page) if subject else None,
                "header_line" if subject else None,
            ),
        )
        return FormParseResult(
            form_type=FormType.LEGACY,
            raw_student_name=name,
            student_name=name,
            raw_subject=raw_subject or subject,
            subject=subject,
            confidence=confidence,
            reasons=tuple(reasons),
            warnings=tuple(warnings),
            name_confidence=name_confidence,
            subject_confidence=subject_confidence,
            subject_proposal=subject_proposal,
            evidence=evidence,
        )

    @staticmethod
    def _header_text(text: str) -> str:
        lines = []
        for line in text.splitlines():
            parts = _anchor_parts(line)
            if any(
                start == 0
                for label in FormDetector.LEGACY_ANCHORS
                for start, _end, _errors in _anchor_spans(parts[:4], label)
            ):
                break
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _raw_subject(text: str) -> Optional[str]:
        catalog = SubjectCatalog.load()
        for line in text.splitlines()[:3]:
            for token in reversed(TextParser.clean_text(line).split()):
                cleaned = token.strip(".,:;()[]{}*_")
                if _normalized_key(cleaned) in catalog.entries:
                    return cleaned
        return None

    @staticmethod
    def _subject_proposal(header: str) -> Optional[str]:
        """An unknown single word on the first header line may be a new subject."""

        lines = [TextParser.clean_text(line) for line in header.splitlines() if line.strip()]
        if not lines:
            return None
        tokens = lines[0].split()
        if len(tokens) == 3:
            candidate = tokens[2].strip(".,:;()[]{}*_")
            if len(candidate) >= 4 and candidate.isalpha():
                return candidate
        return None

    @staticmethod
    def _locate(page: PageData, name: str) -> Tuple[PageWord, ...]:
        """Words that spell the parsed name, for evidence and a field reading."""

        target = _normalized_key(name)
        words = _ordered_words(page)
        for start in range(len(words)):
            combined = ""
            for end in range(start, min(len(words), start + 6)):
                combined += _normalized_key(words[end].text)
                if combined == target:
                    return tuple(words[start:end + 1])
                if not target.startswith(combined):
                    break
        return ()


class AttendanceFormParser:
    """Parse name, birth date and subject relative to the printed labels."""

    NAME_ANCHOR = "Name des Schülers"
    BIRTH_ANCHOR = "Geburtsdatum"
    SUBJECT_ANCHOR = "Fach"
    GR_EZ_ANCHOR = "GR/EZ"
    DURATION_ANCHOR = "Dauer"
    LOCATION_ANCHOR = "Ort"
    TEACHER_ANCHOR = "Name der Lehrkraft"
    # A subject printed in at least this many rows, without any other value,
    # makes estimated column boundaries informational.
    MINIMUM_CONSISTENT_ROWS = 3

    def parse(self, page: PageData, detection: FormDetection) -> FormParseResult:
        reasons = list(detection.reasons)
        warnings: List[str] = []
        notes: List[str] = []

        name_field = self._extract_name(page, reasons, warnings, notes)
        name, name_readings, name_source, name_confidence = self._name_readings(
            page, name_field
        )
        if name and not name_field.value:
            reasons.append("Schülername nur in einer zusätzlichen Lesung gefunden.")
            warnings[:] = [
                warning for warning in warnings
                if "plausibler Schülername" not in warning
            ]
        birth = self._birth_date(page, name_field)
        raw_subject, subject, subject_evidence, subject_proposal = self._extract_subject(
            page, reasons, warnings, notes
        )

        confidence = detection.confidence
        if not name or not subject:
            confidence = min(confidence, 0.55)
        subject_confidence = _ocr_confidence_for_text(page, raw_subject or subject)
        message = _low_ocr_confidence("Schülername", name_confidence)
        if message:
            warnings.append(message)
        message = _low_ocr_confidence("Fach", subject_confidence)
        if message:
            if subject_evidence.localization == "table_column_consistent":
                notes.append(message.replace("; keine automatische Umbenennung.", "."))
            else:
                warnings.append(message)
        if warnings:
            confidence = min(confidence, 0.70)

        birth_value, raw_birth, birth_evidence = birth
        evidence = (
            FieldEvidence(
                "student_name", name, None, name_field.box, name_source,
                name_field.localization, name_readings,
            ),
            birth_evidence,
            subject_evidence,
        )
        return FormParseResult(
            form_type=FormType.ATTENDANCE,
            raw_student_name=name,
            student_name=name,
            raw_subject=raw_subject,
            subject=subject,
            confidence=confidence,
            reasons=tuple(reasons),
            warnings=tuple(warnings),
            name_confidence=name_confidence,
            subject_confidence=subject_confidence,
            birth_date=birth_value,
            raw_birth_date=raw_birth,
            subject_proposal=subject_proposal,
            notes=tuple(notes),
            evidence=evidence,
        )

    def _extract_name(
        self,
        page: PageData,
        reasons: List[str],
        warnings: List[str],
        notes: List[str],
    ) -> _NameField:
        name_anchor = _find_anchor(page, self.NAME_ANCHOR)
        if name_anchor is None:
            warnings.append("Strukturanker 'Name des Schülers' nicht lokalisiert.")
            return _NameField(None, None, None)

        birth_anchors = _find_anchors(page, self.BIRTH_ANCHOR)
        beside = [
            anchor for anchor in birth_anchors
            if anchor.box.x0 > name_anchor.box.x1
            and abs(anchor.box.top - name_anchor.box.top) <= page.height * 0.025
        ]
        birth_anchor = min(beside, key=lambda anchor: anchor.errors) if beside else None
        if birth_anchor is None and birth_anchors:
            warnings.append("Strukturanker 'Geburtsdatum' liegt nicht neben dem Namensfeld.")
            return _NameField(None, None, None)

        right_edge = (
            birth_anchor.box.x0 - page.width * 0.01
            if birth_anchor is not None
            else page.width * 0.68
        )
        region_words = page.words_in_region(
            max(0.0, name_anchor.box.x0 - page.width * 0.03),
            max(0.0, name_anchor.box.top - page.height * 0.08),
            right_edge,
            name_anchor.box.top,
        )
        rows = _group_rows(region_words, page.height)
        rows.sort(key=lambda row: max(word.top for word in row), reverse=True)

        localization = _localization(name_anchor)
        for row in rows:
            candidate = self._clean_name_candidate(row)
            if candidate:
                reasons.append(
                    "Schülername relativ über 'Name des Schülers' und links von "
                    "'Geburtsdatum' gelesen."
                )
                if birth_anchor is None:
                    notes.append(
                        "Strukturanker 'Geburtsdatum' fehlt; rechte Namensgrenze geschätzt."
                    )
                return _NameField(candidate, _box_for(row), localization, tuple(row), birth_anchor)

        if birth_anchor is None:
            warnings.append("Strukturanker 'Geburtsdatum' fehlt; rechte Namensgrenze geschätzt.")
        warnings.append("Kein plausibler Schülername im beschrifteten Namensfeld gefunden.")
        band = BoundingBox(
            max(0.0, name_anchor.box.x0 - page.width * 0.03),
            max(0.0, name_anchor.box.top - page.height * 0.03),
            right_edge,
            max(0.0, name_anchor.box.top - page.height * 0.002),
        )
        return _NameField(None, band, localization, (), birth_anchor)

    @staticmethod
    def _clean_name_candidate(words: Sequence[PageWord]) -> Optional[str]:
        return _clean_name_candidate(words)

    def _name_readings(self, page: PageData, name_field: _NameField):
        others: List[FieldReading] = []
        if name_field.box is not None:
            field_words = _field_words(page, name_field.box)
            field_name = _clean_name_candidate(field_words) if field_words else None
            if field_name:
                others.append(
                    FieldReading(SOURCE_OCR_FIELD, field_name, _mean_confidence(field_words))
                )
        for alternative in page.alternatives:
            alternative_field = self._extract_name(alternative, [], [], [])
            if alternative_field.value:
                others.append(FieldReading(SOURCE_TEXT_LAYER, alternative_field.value))
        primary_confidence = None
        if name_field.value:
            primary_confidence = _ocr_confidence_for_text(page, name_field.value)
            if primary_confidence is None:
                # Signature fragments between name words break the run above;
                # use only the words that are part of the name.
                target = _normalized_key(name_field.value)
                primary_confidence = _mean_confidence([
                    word for word in name_field.words
                    if _normalized_key(word.text) and _normalized_key(word.text) in target
                ])
        name, readings, source = _merge_readings(
            page, name_field.value, primary_confidence, others
        )
        confidence = primary_confidence if name_field.value else None
        return name, readings, source, confidence

    def _birth_date(self, page: PageData, name_field: _NameField):
        """Birth date above 'Geburtsdatum', from every available reading."""

        readings: List[FieldReading] = []
        uncertainties: List[str] = []
        box = None
        primary = self._birth_date_in(page, name_field.birth_anchor)
        if primary is not None:
            value, raw, box, words = primary
            readings.append(FieldReading(_reading_source(page), raw, _mean_confidence(words)))
        elif name_field.birth_anchor is None:
            uncertainties.append("Strukturanker 'Geburtsdatum' nicht lokalisiert.")
        region = box or self._birth_region(page, name_field.birth_anchor)
        if region is not None:
            field_words = _field_words(page, region)
            parsed = parse_birth_date(" ".join(word.text for word in field_words))
            if parsed:
                readings.append(
                    FieldReading(SOURCE_OCR_FIELD, parsed[1], _mean_confidence(field_words))
                )
        for alternative in page.alternatives:
            alternative_name = self._extract_name(alternative, [], [], [])
            alternative_date = self._birth_date_in(alternative, alternative_name.birth_anchor)
            if alternative_date is not None:
                readings.append(FieldReading(SOURCE_TEXT_LAYER, alternative_date[1]))

        chosen = None
        for reading in readings:
            parsed = parse_birth_date(reading.value)
            if parsed:
                chosen = (parsed[0], reading)
                break
        if chosen is None:
            if not uncertainties:
                uncertainties.append("Kein gültiges Geburtsdatum über 'Geburtsdatum' gefunden.")
            evidence = FieldEvidence(
                "birth_date", None, None, box, None, None, tuple(readings), tuple(uncertainties)
            )
            return None, None, evidence
        value, reading = chosen
        distinct = {
            parse_birth_date(item.value)[0]
            for item in readings
            if parse_birth_date(item.value)
        }
        if len(distinct) > 1:
            uncertainties.append("Lesungen des Geburtsdatums widersprechen sich.")
        localization = _localization(name_field.birth_anchor) if name_field.birth_anchor else None
        evidence = FieldEvidence(
            "birth_date", reading.value, value.isoformat(), box, reading.source,
            localization, tuple(readings), tuple(uncertainties),
        )
        return value, reading.value, evidence

    @staticmethod
    def _birth_region(page: PageData, birth_anchor: Optional[AnchorMatch]) -> Optional[BoundingBox]:
        if birth_anchor is None:
            return None
        return BoundingBox(
            max(0.0, birth_anchor.box.x0 - page.width * 0.03),
            max(0.0, birth_anchor.box.top - page.height * 0.03),
            min(page.width, birth_anchor.box.x1 + page.width * 0.25),
            max(0.0, birth_anchor.box.top - page.height * 0.002),
        )

    def _birth_date_in(self, page: PageData, birth_anchor: Optional[AnchorMatch]):
        if birth_anchor is None:
            return None
        region = page.words_in_region(
            max(0.0, birth_anchor.box.x0 - page.width * 0.03),
            max(0.0, birth_anchor.box.top - page.height * 0.05),
            min(page.width, birth_anchor.box.x1 + page.width * 0.25),
            birth_anchor.box.top,
        )
        rows = _group_rows(region, page.height)
        rows.sort(key=lambda row: max(word.top for word in row), reverse=True)
        for row in rows:
            parsed = parse_birth_date(" ".join(word.text for word in row))
            if parsed:
                return parsed[0], parsed[1], _box_for(row), tuple(row)
        return None

    def _extract_subject(
        self,
        page: PageData,
        reasons: List[str],
        warnings: List[str],
        notes: List[str],
    ) -> Tuple[Optional[str], Optional[str], FieldEvidence, Optional[str]]:
        subject_anchors = _find_anchors(page, self.SUBJECT_ANCHOR)
        if not subject_anchors:
            warnings.append("Strukturanker 'Fach' nicht lokalisiert.")
            return None, None, FieldEvidence("subject", None), None

        def neighbor(
            subject_anchor: AnchorMatch, label: str, left: bool,
        ) -> Optional[AnchorMatch]:
            candidates = [
                anchor for anchor in _find_anchors(page, label)
                if abs(anchor.box.top - subject_anchor.box.top) <= page.height * 0.025
                and (
                    anchor.box.center_x < subject_anchor.box.center_x if left
                    else anchor.box.center_x > subject_anchor.box.center_x
                )
            ]
            return min(
                candidates,
                key=lambda anchor: abs(anchor.box.center_x - subject_anchor.box.center_x),
                default=None,
            )

        def left_neighbor(subject_anchor: AnchorMatch) -> Optional[AnchorMatch]:
            # GR/EZ, if printed, sits between Dauer and Fach.
            gr_ez = neighbor(subject_anchor, self.GR_EZ_ANCHOR, left=True)
            duration = neighbor(subject_anchor, self.DURATION_ANCHOR, left=True)
            if gr_ez is not None and (
                duration is None or gr_ez.box.center_x > duration.box.center_x
            ):
                return gr_ez
            return duration

        # Prefer the table header to an earlier mention of "Fach" in prose.
        subject_anchor = max(
            subject_anchors,
            key=lambda anchor: (
                sum((
                    left_neighbor(anchor) is not None,
                    neighbor(anchor, self.LOCATION_ANCHOR, left=False) is not None,
                )),
                -anchor.errors,
            ),
        )
        left_anchor = left_neighbor(subject_anchor)
        location_anchor = neighbor(subject_anchor, self.LOCATION_ANCHOR, left=False)
        teacher_anchor = next(
            (anchor for anchor in _find_anchors(page, self.TEACHER_ANCHOR)
             if anchor.box.top > subject_anchor.box.bottom),
            None,
        )

        estimated: List[str] = []
        center = subject_anchor.box.center_x
        if left_anchor is not None:
            left = (left_anchor.box.center_x + center) / 2
        else:
            left = center - page.width * 0.05
            estimated.append("Strukturanker 'Dauer' fehlt; linke Fachgrenze geschätzt.")
        if location_anchor is not None:
            right = (center + location_anchor.box.center_x) / 2
        else:
            right = center + page.width * 0.05
            estimated.append("Strukturanker 'Ort' fehlt; rechte Fachgrenze geschätzt.")

        lower_edge = (
            teacher_anchor.box.top - page.height * 0.005
            if teacher_anchor is not None
            else page.height * 0.88
        )
        if teacher_anchor is None:
            estimated.append(
                "Strukturanker 'Name der Lehrkraft' fehlt; untere Fachgrenze geschätzt."
            )
        column = BoundingBox(left, subject_anchor.box.bottom + page.height * 0.005, right, lower_edge)
        values = page.words_in_region(column.x0, column.top, column.x1, column.bottom)
        catalog = SubjectCatalog.load()
        matches: List[Tuple[str, str]] = []
        matched_words: List[PageWord] = []
        matched_tops: List[float] = []
        unknown_rows: List[Tuple[float, str, Optional[float]]] = []
        for row in _group_rows(values, page.height):
            row = [word for word in row if not _is_gr_ez_value(word.text)]
            if not row:
                continue
            cell_matches = self._match_subjects(row, catalog)
            if cell_matches:
                # One vote per subject and row; repeated words in one cell must
                # not outweigh independent table rows.
                seen = set()
                for raw, canonical in cell_matches:
                    if canonical not in seen:
                        matches.append((raw, canonical))
                        seen.add(canonical)
                matched_words.extend(row)
                matched_tops.append(min(word.top for word in row))
            else:
                raw = " ".join(word.text for word in row).strip()
                if sum(char.isalpha() for char in raw) >= 3:
                    unknown_rows.append(
                        (min(word.top for word in row), raw, _mean_confidence(row))
                    )

        if teacher_anchor is None and len(matched_tops) >= 2:
            # Without the label below the table, text under the last table row
            # (footnotes, signatures) is not part of the column.
            gaps = sorted(b - a for a, b in zip(matched_tops, matched_tops[1:]) if b > a)
            row_gap = gaps[len(gaps) // 2] if gaps else page.height * 0.03
            limit = max(matched_tops) + 1.5 * row_gap
            unknown_rows = [row for row in unknown_rows if row[0] <= limit]

        leader = Counter(canonical for _, canonical in matches).most_common(1)
        well_supported = bool(leader) and leader[0][1] >= self.MINIMUM_CONSISTENT_ROWS
        variants: List[str] = []
        fragments: List[str] = []
        unknown_values: List[str] = []
        for _top, raw, row_confidence in unknown_rows:
            uncertain = (
                row_confidence is not None
                and row_confidence < OCR_FIELD_CONFIDENCE_THRESHOLD
            )
            if well_supported and self._is_variant_of(raw, leader[0][0]):
                variants.append(raw)
            elif (
                well_supported
                and sum(char.isalpha() for char in raw) <= 4
                and uncertain
            ):
                # Short, uncertain OCR fragments of a cell in a column whose
                # subject is printed consistently in many rows.
                fragments.append(raw)
            else:
                unknown_values.append(raw)

        localization = "table_column"
        uncertainties: List[str] = []
        if variants:
            uncertainties.append(
                "Unsicher gelesene Varianten des Fachs: " + ", ".join(sorted(set(variants)))
            )
        if fragments:
            uncertainties.append(
                "Unleserliche Bruchstücke im Tabellenfeld ignoriert: "
                + ", ".join(sorted(set(fragments)))
            )
        proposal = None
        if unknown_values:
            warnings.append(
                "Unbekannte Fachwerte im Tabellenfeld: "
                + ", ".join(sorted(set(unknown_values)))
            )

        if not matches:
            warnings.extend(estimated)
            warnings.append("Kein bekanntes Fach im Tabellenfeld 'Fach' gefunden.")
            proposal = self._subject_proposal(unknown_values)
            raw_subject = ", ".join(sorted(set(unknown_values))) or None
            return raw_subject, None, FieldEvidence(
                "subject", raw_subject, None, column, _reading_source(page), localization,
                uncertainties=tuple(estimated) + tuple(uncertainties),
            ), proposal

        canonical_counts = Counter(canonical for _, canonical in matches)
        most_common = canonical_counts.most_common()
        leaders = [name for name, count in most_common if count == most_common[0][1]]
        counts_text = ", ".join(
            f"{name}={count}" for name, count in sorted(canonical_counts.items())
        )

        if len(leaders) != 1 or most_common[0][1] * 2 <= sum(canonical_counts.values()):
            warnings.extend(estimated)
            raw_values = ", ".join(sorted({raw for raw, _ in matches}))
            warnings.append(
                f"Widersprüchliche Fächer ohne Mehrheit erkannt ({counts_text})."
            )
            return raw_values, None, FieldEvidence(
                "subject", raw_values, None, column, _reading_source(page), localization,
                uncertainties=tuple(estimated) + tuple(uncertainties),
            ), None

        subject = leaders[0]
        raw_counts = Counter(raw for raw, canonical in matches if canonical == subject)
        raw_subject = raw_counts.most_common(1)[0][0]
        reasons.append(
            f"Fach '{subject}' per Mehrheit im relativen Tabellenfeld erkannt "
            f"({counts_text})."
        )
        consistent = (
            len(canonical_counts) == 1
            and canonical_counts[subject] >= self.MINIMUM_CONSISTENT_ROWS
            and not unknown_values
        )
        if consistent:
            localization = "table_column_consistent"
            notes.extend(estimated)
        else:
            warnings.extend(estimated)
        if len(canonical_counts) > 1:
            warnings.append(
                f"Abweichende Fachwerte erkannt; Mehrheit '{subject}' verwendet "
                f"({counts_text})."
            )
        bbox = _box_for(matched_words) if matched_words else column
        return raw_subject, subject, FieldEvidence(
            "subject", raw_subject, subject, bbox, _reading_source(page), localization,
            tuple(FieldReading(_reading_source(page), raw) for raw in sorted(raw_counts)),
            tuple(estimated) + tuple(uncertainties),
        ), None

    @staticmethod
    def _is_variant_of(raw: str, canonical: str) -> bool:
        """A badly misread copy of the majority subject, not another subject.

        Only used when the majority is printed in several rows: photocopies
        then contain misread copies with up to half of the letters changed.
        A real second subject is a catalog value and stays a conflict.
        """

        key = _normalized_key(raw)
        target = _normalized_key(canonical)
        if len(key) < 4 or not target:
            return False
        limit = max(len(key), len(target)) // 2
        return _edit_distance(key, target, limit) <= limit

    @staticmethod
    def _subject_proposal(unknown_values: Sequence[str]) -> Optional[str]:
        """A word printed consistently in the Fach column may be a new subject."""

        cleaned = [value.strip(".,:;()[]{}*_ ") for value in unknown_values]
        counts = Counter(_normalized_key(value) for value in cleaned if value)
        if not counts:
            return None
        key, count = counts.most_common(1)[0]
        if count < 2 or len(key) < 4:
            return None
        spellings = Counter(value for value in cleaned if _normalized_key(value) == key)
        candidate = spellings.most_common(1)[0][0]
        return candidate if candidate.replace("-", "").isalpha() else None

    @staticmethod
    def _subject_catalog() -> Dict[str, str]:
        """Compatibility view of the catalog as ``normalized key -> canonical``."""

        return SubjectCatalog.load().entries

    @staticmethod
    def _match_subjects(
        row: Sequence[PageWord],
        catalog: SubjectCatalog,
    ) -> List[Tuple[str, str]]:
        # Consume all matches, including several subjects in one cell. Joining
        # arbitrary OCR fragments must not silently hide a conflicting value.
        tokens = [
            token for word in row for token in re.split(r"[/,;+]", word.text)
            if token.strip() and not _is_gr_ez_value(token)
        ]
        matches: List[Tuple[str, str]] = []
        start = 0
        while start < len(tokens):
            for end in range(len(tokens), start, -1):
                raw = " ".join(tokens[start:end]).strip()
                canonical = catalog.match(raw)
                if canonical:
                    matches.append((raw, canonical))
                    start = end
                    break
            else:
                start += 1
        return matches


class FormParser:
    """Dispatch structured pages to the parser for the detected form type."""

    def __init__(self, detector: Optional[FormDetector] = None):
        self.detector = detector or FormDetector()
        self.legacy_parser = LegacyFormParser()
        self.attendance_parser = AttendanceFormParser()

    def parse(self, page: PageData) -> FormParseResult:
        diagnostics = tuple(page.diagnostics)
        detection = self.detector.detect(page)
        if detection.form_type == FormType.UNKNOWN:
            # An alternative reading (the PDF text layer) replaces the primary
            # one only when the primary shows no form structure at all.
            for alternative in getattr(page, "alternatives", ()):
                alternative_detection = self.detector.detect(alternative)
                if alternative_detection.form_type != FormType.UNKNOWN:
                    page = alternative
                    detection = replace(
                        alternative_detection,
                        reasons=(
                            "Formularstruktur nur in der Textebene der PDF erkannt.",
                        ) + alternative_detection.reasons,
                    )
                    break
        detection = replace(detection, reasons=diagnostics + detection.reasons)
        if detection.form_type == FormType.LEGACY:
            return self.legacy_parser.parse(page, detection)
        if detection.form_type == FormType.ATTENDANCE:
            return self.attendance_parser.parse(page, detection)
        return FormParseResult(
            form_type=FormType.UNKNOWN,
            raw_student_name=None,
            student_name=None,
            raw_subject=None,
            subject=None,
            confidence=detection.confidence,
            reasons=detection.reasons,
            warnings=("Dokument muss manuell geprüft werden.",),
        )

    def parse_page(self, page: PageData) -> FormParseResult:
        return self.parse(page)

    def parse_document(self, page: PageData) -> FormParseResult:
        """Compatibility-friendly name for consumers treating a page as a document."""

        return self.parse(page)
