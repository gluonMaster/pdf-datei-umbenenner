"""Layout-aware parsing for legacy and attendance PDF forms."""

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from page_data import PageData, PageWord
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


@dataclass(frozen=True)
class AnchorMatch:
    name: str
    box: BoundingBox
    words: Tuple[PageWord, ...]


@dataclass(frozen=True)
class FormDetection:
    form_type: FormType
    confidence: float
    reasons: Tuple[str, ...]
    matched_anchors: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FormParseResult:
    """Structured parse output, including evidence for manual review."""

    form_type: FormType
    raw_student_name: Optional[str]
    student_name: Optional[str]
    raw_subject: Optional[str]
    subject: Optional[str]
    confidence: float
    reasons: Tuple[str, ...]
    warnings: Tuple[str, ...] = ()

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
        )

    def as_legacy_tuple(self) -> Tuple[Optional[str], Optional[str]]:
        return self.student_name, self.subject


def _normalized_key(value: str) -> str:
    """Normalize anchors while tolerating punctuation and OCR whitespace."""

    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if char.isalnum())


def _box_for(words: Sequence[PageWord]) -> BoundingBox:
    return BoundingBox(
        x0=min(word.x0 for word in words),
        top=min(word.top for word in words),
        x1=max(word.x1 for word in words),
        bottom=max(word.bottom for word in words),
    )


def _ordered_words(page: PageData) -> List[PageWord]:
    return sorted(page.words, key=lambda word: (word.top, word.x0))


def _find_anchor(page: PageData, label: str) -> Optional[AnchorMatch]:
    """Locate an anchor even when OCR has split one of its words."""

    target = _normalized_key(label)
    words = [word for word in _ordered_words(page) if _normalized_key(word.text)]

    for start in range(len(words)):
        combined = ""
        selected: List[PageWord] = []
        for word in words[start:start + 12]:
            combined += _normalized_key(word.text)
            selected.append(word)
            if combined == target:
                return AnchorMatch(label, _box_for(selected), tuple(selected))
            if len(combined) >= len(target) or not target.startswith(combined):
                break
    return None


def _group_rows(words: Iterable[PageWord], page_height: float) -> List[List[PageWord]]:
    tolerance = max(2.0, page_height * 0.004)
    rows: List[List[PageWord]] = []
    row_tops: List[float] = []
    for word in sorted(words, key=lambda item: (item.top, item.x0)):
        for index, row_top in enumerate(row_tops):
            if abs(word.top - row_top) <= tolerance:
                rows[index].append(word)
                break
        else:
            rows.append([word])
            row_tops.append(word.top)
    for row in rows:
        row.sort(key=lambda item: item.x0)
    return rows


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
        normalized_text = _normalized_key(page.text)
        attendance = tuple(
            anchor
            for anchor in self.ATTENDANCE_ANCHORS
            if _normalized_key(anchor) in normalized_text
        )
        legacy = tuple(
            anchor
            for anchor in self.LEGACY_ANCHORS
            if _normalized_key(anchor) in normalized_text
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


class LegacyFormParser:
    """Preserve the successful parser behavior for the old top-line form."""

    def parse(self, page: PageData, detection: FormDetection) -> FormParseResult:
        name = TextParser.extract_student_name(page.text)
        subject = TextParser.extract_subject(page.text)
        raw_subject = self._raw_subject(page.text)
        reasons = list(detection.reasons)
        warnings: List[str] = []

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

        return FormParseResult(
            form_type=FormType.LEGACY,
            raw_student_name=name,
            student_name=name,
            raw_subject=raw_subject or subject,
            subject=subject,
            confidence=confidence,
            reasons=tuple(reasons),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _raw_subject(text: str) -> Optional[str]:
        known = {_normalized_key(item) for item in TextParser.get_valid_subjects()}
        for line in text.splitlines()[:3]:
            for token in reversed(TextParser.clean_text(line).split()):
                cleaned = token.strip(".,:;()[]{}*_")
                if _normalized_key(cleaned) in known:
                    return cleaned
        return None


class AttendanceFormParser:
    """Parse name and subject using anchors and page-relative coordinates."""

    NAME_ANCHOR = "Name des Schülers"
    BIRTH_ANCHOR = "Geburtsdatum"
    SUBJECT_ANCHOR = "Fach"
    DURATION_ANCHOR = "Dauer"
    LOCATION_ANCHOR = "Ort"
    TEACHER_ANCHOR = "Name der Lehrkraft"

    def parse(self, page: PageData, detection: FormDetection) -> FormParseResult:
        reasons = list(detection.reasons)
        warnings: List[str] = []
        raw_name = self._extract_name(page, reasons, warnings)
        raw_subject, subject = self._extract_subject(page, reasons, warnings)

        confidence = detection.confidence
        if not raw_name or not subject:
            confidence = min(confidence, 0.55)
        if warnings:
            confidence = min(confidence, 0.70)

        return FormParseResult(
            form_type=FormType.ATTENDANCE,
            raw_student_name=raw_name,
            student_name=raw_name,
            raw_subject=raw_subject,
            subject=subject,
            confidence=confidence,
            reasons=tuple(reasons),
            warnings=tuple(warnings),
        )

    def _extract_name(
        self,
        page: PageData,
        reasons: List[str],
        warnings: List[str],
    ) -> Optional[str]:
        name_anchor = _find_anchor(page, self.NAME_ANCHOR)
        birth_anchor = _find_anchor(page, self.BIRTH_ANCHOR)
        if name_anchor is None:
            warnings.append("Strukturanker 'Name des Schülers' nicht lokalisiert.")
            return None

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

        for row in rows:
            candidate = self._clean_name_candidate(row)
            if candidate:
                reasons.append(
                    "Schülername relativ über 'Name des Schülers' und links von "
                    "'Geburtsdatum' gelesen."
                )
                return candidate

        warnings.append("Kein plausibler Schülername im beschrifteten Namensfeld gefunden.")
        return None

    @staticmethod
    def _clean_name_candidate(words: Sequence[PageWord]) -> Optional[str]:
        tokens: List[str] = []
        for word in words:
            token = word.text.strip(".,:;()[]{}*_\"“”")
            if re.fullmatch(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", token):
                continue
            if not token or not all(char.isalpha() or char in "-'’" for char in token):
                return None
            tokens.append(token)

        if not 2 <= len(tokens) <= 4:
            return None
        if sum(token[:1].isupper() for token in tokens) < 2:
            return None
        excluded = {
            "name", "des", "schulers", "geburtsdatum", "hiermit", "wird",
            "fur", "folgenden", "schuler", "lehrkraft", "druckbuchstaben",
        }
        if any(_normalized_key(token) in excluded for token in tokens):
            return None
        return " ".join(tokens)

    def _extract_subject(
        self,
        page: PageData,
        reasons: List[str],
        warnings: List[str],
    ) -> Tuple[Optional[str], Optional[str]]:
        subject_anchor = _find_anchor(page, self.SUBJECT_ANCHOR)
        duration_anchor = _find_anchor(page, self.DURATION_ANCHOR)
        location_anchor = _find_anchor(page, self.LOCATION_ANCHOR)
        teacher_anchor = _find_anchor(page, self.TEACHER_ANCHOR)

        if subject_anchor is None:
            warnings.append("Strukturanker 'Fach' nicht lokalisiert.")
            return None, None

        center = subject_anchor.box.center_x
        if duration_anchor is not None:
            left = (duration_anchor.box.center_x + center) / 2
        else:
            left = center - page.width * 0.05
            warnings.append("Strukturanker 'Dauer' fehlt; linke Fachgrenze geschätzt.")
        if location_anchor is not None:
            right = (center + location_anchor.box.center_x) / 2
        else:
            right = center + page.width * 0.05
            warnings.append("Strukturanker 'Ort' fehlt; rechte Fachgrenze geschätzt.")

        lower_edge = (
            teacher_anchor.box.top - page.height * 0.005
            if teacher_anchor is not None
            else page.height * 0.88
        )
        values = page.words_in_region(
            left,
            subject_anchor.box.bottom + page.height * 0.005,
            right,
            lower_edge,
        )
        catalog = self._subject_catalog()
        matches: List[Tuple[str, str]] = []
        for row in _group_rows(values, page.height):
            match = self._match_subject(row, catalog)
            if match:
                matches.append(match)

        if not matches:
            warnings.append("Kein bekanntes Fach im Tabellenfeld 'Fach' gefunden.")
            return None, None

        canonical_counts = Counter(canonical for _, canonical in matches)
        most_common = canonical_counts.most_common()
        leaders = [name for name, count in most_common if count == most_common[0][1]]
        counts_text = ", ".join(
            f"{name}={count}" for name, count in sorted(canonical_counts.items())
        )

        if len(leaders) != 1:
            raw_values = ", ".join(sorted({raw for raw, _ in matches}))
            warnings.append(
                f"Widersprüchliche Fächer ohne Mehrheit erkannt ({counts_text})."
            )
            return raw_values, None

        subject = leaders[0]
        raw_counts = Counter(raw for raw, canonical in matches if canonical == subject)
        raw_subject = raw_counts.most_common(1)[0][0]
        reasons.append(
            f"Fach '{subject}' per Mehrheit im relativen Tabellenfeld erkannt "
            f"({counts_text})."
        )
        if len(canonical_counts) > 1:
            warnings.append(
                f"Abweichende Fachwerte erkannt; Mehrheit '{subject}' verwendet "
                f"({counts_text})."
            )
        return raw_subject, subject

    @staticmethod
    def _subject_catalog() -> Dict[str, str]:
        catalog: Dict[str, str] = {}
        for raw in TextParser.get_valid_subjects():
            canonical = TextParser.normalize_subject(raw)
            if canonical:
                catalog[_normalized_key(raw)] = canonical
                catalog[_normalized_key(canonical)] = canonical
        return catalog

    @staticmethod
    def _match_subject(
        row: Sequence[PageWord],
        catalog: Dict[str, str],
    ) -> Optional[Tuple[str, str]]:
        # Try the entire cell first so OCR splits such as "Deu tsch" are joined.
        for length in range(min(4, len(row)), 0, -1):
            for start in range(0, len(row) - length + 1):
                selected = row[start:start + length]
                raw = " ".join(word.text for word in selected).strip()
                canonical = catalog.get(_normalized_key(raw))
                if canonical:
                    return raw, canonical
        return None


class FormParser:
    """Dispatch structured pages to the parser for the detected form type."""

    def __init__(self, detector: Optional[FormDetector] = None):
        self.detector = detector or FormDetector()
        self.legacy_parser = LegacyFormParser()
        self.attendance_parser = AttendanceFormParser()

    def parse(self, page: PageData) -> FormParseResult:
        detection = self.detector.detect(page)
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
