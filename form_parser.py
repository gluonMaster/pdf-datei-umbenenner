"""Layout-aware parsing for legacy and attendance PDF forms."""

from collections import Counter
from dataclasses import dataclass, replace
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
    name_confidence: Optional[float] = None
    subject_confidence: Optional[float] = None

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
            )
        )

    def as_legacy_tuple(self) -> Tuple[Optional[str], Optional[str]]:
        return self.student_name, self.subject


def _normalized_key(value: str) -> str:
    """Normalize anchors while tolerating punctuation and OCR whitespace."""

    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if char.isalnum())


OCR_FIELD_CONFIDENCE_THRESHOLD = 0.70


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


def _add_low_ocr_confidence_warning(
    warnings: List[str],
    label: str,
    confidence: Optional[float],
) -> None:
    if confidence is not None and confidence < OCR_FIELD_CONFIDENCE_THRESHOLD:
        warnings.append(
            f"Niedrige OCR-Konfidenz für {label} ({confidence:.0%}); "
            "keine automatische Umbenennung."
        )


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


def _anchor_spans(parts: Sequence[str], label: str) -> Iterable[Tuple[int, int]]:
    target = _normalized_key(label)
    for start in range(len(parts)):
        combined = ""
        for end in range(start, len(parts)):
            combined += parts[end]
            if combined == target:
                yield start, end + 1
                break
            if not target.startswith(combined):
                break


def _find_anchors(page: PageData, label: str) -> List[AnchorMatch]:
    """Locate an anchor even when OCR has split one of its words."""

    matches: List[AnchorMatch] = []
    for row in _group_rows(page.words, page.height):
        parts = [(part, word) for word in row for part in _anchor_parts(word.text)]
        for start, end in _anchor_spans([part for part, _ in parts], label):
            selected = tuple(dict.fromkeys(word for _, word in parts[start:end]))
            # Do not join unrelated labels across separate columns.
            if any(
                right.x0 - left.x1 > page.width * 0.04
                for left, right in zip(selected, selected[1:])
            ):
                continue
            matches.append(AnchorMatch(label, _box_for(selected), selected))
    return matches


def _find_anchor(page: PageData, label: str) -> Optional[AnchorMatch]:
    return next(iter(_find_anchors(page, label)), None)


def _group_rows(words: Iterable[PageWord], page_height: float) -> List[List[PageWord]]:
    tolerance = page_height * 0.004
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


class LegacyFormParser:
    """Preserve the successful parser behavior for the old top-line form."""

    def parse(self, page: PageData, detection: FormDetection) -> FormParseResult:
        header = self._header_text(page.text)
        name = TextParser.extract_student_name(header)
        subject = TextParser.extract_subject(header)
        raw_subject = self._raw_subject(header)
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
        name_confidence = _ocr_confidence_for_text(page, name)
        subject_confidence = _ocr_confidence_for_text(page, raw_subject or subject)
        _add_low_ocr_confidence_warning(warnings, "Schülername", name_confidence)
        _add_low_ocr_confidence_warning(warnings, "Fach", subject_confidence)

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
        )

    @staticmethod
    def _header_text(text: str) -> str:
        lines = []
        for line in text.splitlines():
            key = _normalized_key(line)
            if any(
                key.startswith(_normalized_key(label))
                for label in FormDetector.LEGACY_ANCHORS
            ):
                break
            lines.append(line)
        return "\n".join(lines)

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
        name_confidence = _ocr_confidence_for_text(page, raw_name)
        subject_confidence = _ocr_confidence_for_text(page, raw_subject or subject)
        _add_low_ocr_confidence_warning(warnings, "Schülername", name_confidence)
        _add_low_ocr_confidence_warning(warnings, "Fach", subject_confidence)
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
            name_confidence=name_confidence,
            subject_confidence=subject_confidence,
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

        if birth_anchor is None:
            warnings.append("Strukturanker 'Geburtsdatum' fehlt; rechte Namensgrenze geschätzt.")
        elif (
            birth_anchor.box.x0 <= name_anchor.box.x1
            or abs(birth_anchor.box.top - name_anchor.box.top) > page.height * 0.025
        ):
            warnings.append("Strukturanker 'Geburtsdatum' liegt nicht neben dem Namensfeld.")
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
            "formular", "anwesenheitsnachweis", "unterschrift", "firma",
            "anbieter", "erziehungsberechtigten",
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
        subject_anchors = _find_anchors(page, self.SUBJECT_ANCHOR)
        if not subject_anchors:
            warnings.append("Strukturanker 'Fach' nicht lokalisiert.")
            return None, None

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

        # Prefer the table header to an earlier mention of "Fach" in prose.
        subject_anchor = max(
            subject_anchors,
            key=lambda anchor: sum((
                neighbor(anchor, self.DURATION_ANCHOR, left=True) is not None,
                neighbor(anchor, self.LOCATION_ANCHOR, left=False) is not None,
            )),
        )
        duration_anchor = neighbor(subject_anchor, self.DURATION_ANCHOR, left=True)
        location_anchor = neighbor(subject_anchor, self.LOCATION_ANCHOR, left=False)
        teacher_anchor = next(
            (anchor for anchor in _find_anchors(page, self.TEACHER_ANCHOR)
             if anchor.box.top > subject_anchor.box.bottom),
            None,
        )

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
        if teacher_anchor is None:
            warnings.append("Strukturanker 'Name der Lehrkraft' fehlt; untere Fachgrenze geschätzt.")
        values = page.words_in_region(
            left,
            subject_anchor.box.bottom + page.height * 0.005,
            right,
            lower_edge,
        )
        catalog = self._subject_catalog()
        matches: List[Tuple[str, str]] = []
        unknown_values: List[str] = []
        for row in _group_rows(values, page.height):
            cell_matches = self._match_subjects(row, catalog)
            if cell_matches:
                # One vote per subject and row; repeated words in one cell must
                # not outweigh independent table rows.
                seen = set()
                for raw, canonical in cell_matches:
                    if canonical not in seen:
                        matches.append((raw, canonical))
                        seen.add(canonical)
            else:
                raw = " ".join(word.text for word in row).strip()
                if any(char.isalpha() for char in raw):
                    unknown_values.append(raw)

        if unknown_values:
            warnings.append(
                "Unbekannte Fachwerte im Tabellenfeld: "
                + ", ".join(sorted(set(unknown_values)))
            )

        if not matches:
            warnings.append("Kein bekanntes Fach im Tabellenfeld 'Fach' gefunden.")
            return ", ".join(sorted(set(unknown_values))) or None, None

        canonical_counts = Counter(canonical for _, canonical in matches)
        most_common = canonical_counts.most_common()
        leaders = [name for name, count in most_common if count == most_common[0][1]]
        counts_text = ", ".join(
            f"{name}={count}" for name, count in sorted(canonical_counts.items())
        )

        if len(leaders) != 1 or most_common[0][1] * 2 <= sum(canonical_counts.values()):
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
    def _match_subjects(
        row: Sequence[PageWord],
        catalog: Dict[str, str],
    ) -> List[Tuple[str, str]]:
        # Consume all matches, including several subjects in one cell. Joining
        # arbitrary OCR fragments must not silently hide a conflicting value.
        tokens = [
            token for word in row for token in re.split(r"[/,;+]", word.text)
            if token.strip()
        ]
        matches: List[Tuple[str, str]] = []
        start = 0
        while start < len(tokens):
            for end in range(len(tokens), start, -1):
                raw = " ".join(tokens[start:end]).strip()
                canonical = catalog.get(_normalized_key(raw))
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
        detection = self.detector.detect(page)
        detection = replace(detection, reasons=tuple(page.diagnostics) + detection.reasons)
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
