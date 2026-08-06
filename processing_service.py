"""UI-independent document processing and safe rename orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple

from file_renamer import FileRenamer
from form_parser import FormType
from review_queue import ReviewItem, ReviewStore
from student_registry import MatchStatus
from validator import ValidationLevel


class ProcessingStage(str, Enum):
    """Observable stages completed for one document."""

    EXTRACTION = "extraction"
    PARSING = "parsing"
    STUDENT_MATCHING = "student_matching"
    SUBJECT_VALIDATION = "subject_validation"
    PLANNING = "planning"
    ACTION = "action"


class PlannedAction(str, Enum):
    NONE = "none"
    RENAME = "rename"
    MANUAL_REVIEW = "manual_review"


class PerformedAction(str, Enum):
    NONE = "none"
    RENAMED = "renamed"
    MOVED_TO_REVIEW = "moved_to_review"
    ERROR = "error"


@dataclass(frozen=True)
class FoundDocumentData:
    """Parser data normalized to one stable pipeline boundary."""

    raw_student_name: Optional[str]
    student_name: Optional[str]
    raw_subject: Optional[str]
    subject: Optional[str]
    confidence: float = 0.0
    reasons: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()

    @property
    def raw_name(self) -> Optional[str]:
        return self.raw_student_name


@dataclass(frozen=True)
class ProcessingResult:
    """Complete plan and outcome for one PDF, including controlled failures."""

    pdf_path: Path
    month: int
    year: int
    dry_run: bool
    stages: Tuple[ProcessingStage, ...] = ()
    extraction: Optional[Any] = None
    form_type: Optional[str] = None
    found_data: Optional[FoundDocumentData] = None
    student_match: Optional[Any] = None
    subject_validation: Optional[Any] = None
    canonical_subject: Optional[str] = None
    planned_name: Optional[str] = None
    planned_path: Optional[Path] = None
    planned_action: PlannedAction = PlannedAction.NONE
    performed_action: PerformedAction = PerformedAction.NONE
    review_item: Optional[ReviewItem] = None
    warnings: Tuple[str, ...] = ()
    error: Optional[str] = None

    @property
    def planned_filename(self) -> Optional[str]:
        return self.planned_name

    @property
    def action(self) -> PerformedAction:
        """Compatibility-friendly short name for the performed action."""

        return self.performed_action

    @property
    def succeeded(self) -> bool:
        return self.error is None and self.planned_action == PlannedAction.RENAME

    @property
    def requires_review(self) -> bool:
        return self.planned_action == PlannedAction.MANUAL_REVIEW


@dataclass(frozen=True)
class BatchProcessingResult:
    """Results of an isolated best-effort batch."""

    results: Tuple[ProcessingResult, ...]
    review_queue_saved: bool = False
    review_queue_error: Optional[str] = None

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def renamed(self) -> int:
        return sum(item.planned_action == PlannedAction.RENAME for item in self.results)

    @property
    def manual_review(self) -> int:
        return sum(item.requires_review for item in self.results)

    @property
    def errors(self) -> int:
        return sum(item.error is not None for item in self.results)


class DocumentProcessingService:
    """Single source of truth for parsing, matching, review and renaming."""

    def __init__(
        self,
        extractor: Any,
        parser: Any,
        student_registry: Any = None,
        validator: Any = None,
        review_store: Optional[ReviewStore] = None,
        renamer: Optional[FileRenamer] = None,
        manual_review_folder: Optional[Path] = None,
        *,
        registry: Any = None,
        review_queue: Optional[ReviewStore] = None,
    ) -> None:
        # Friendly aliases keep dependency-injected callers concise.
        if student_registry is None:
            student_registry = registry
        if review_store is None:
            review_store = review_queue
        missing = [
            name
            for name, value in (
                ("extractor", extractor),
                ("parser", parser),
                ("student_registry", student_registry),
                ("validator", validator),
                ("review_store", review_store),
            )
            if value is None
        ]
        if missing:
            raise TypeError(f"Missing processing dependencies: {', '.join(missing)}")

        self.extractor = extractor
        self.parser = parser
        self.student_registry = student_registry
        self.validator = validator
        self.review_store = review_store
        self.renamer = renamer or FileRenamer()
        self.manual_review_folder = (
            Path(manual_review_folder) if manual_review_folder is not None else None
        )

    def process_document(
        self,
        pdf_path: Path,
        month: int,
        year: int,
        dry_run: bool = False,
    ) -> ProcessingResult:
        """Process one PDF and always return a structured result."""

        pdf_path = Path(pdf_path)
        stages = []
        warnings = []
        extraction = None
        form_type = None
        found_data = None
        student_match = None
        subject_validation = None
        canonical_subject = None

        try:
            self._validate_period(month, year)

            extraction = self._extract(pdf_path)
            stages.append(ProcessingStage.EXTRACTION)
            if extraction is None:
                raise ValueError("Text und Layout konnten nicht aus der PDF gelesen werden.")

            raw_parse_result = self._parse(extraction)
            found_data, form_type = self._found_data(raw_parse_result)
            stages.append(ProcessingStage.PARSING)
            warnings.extend(found_data.warnings)

            match_name = found_data.student_name or found_data.raw_student_name or ""
            student_match = self._match_student(match_name)
            stages.append(ProcessingStage.STUDENT_MATCHING)

            subject_validation = self._validate_subject(found_data.subject)
            stages.append(ProcessingStage.SUBJECT_VALIDATION)
            canonical_subject = (
                getattr(subject_validation, "canonical_value", None)
                or getattr(subject_validation, "canonical_subject", None)
                or found_data.subject
            )

            validation_message = str(getattr(subject_validation, "message", "") or "")
            if self._validation_level(subject_validation) == ValidationLevel.WARNING.value:
                if validation_message:
                    warnings.append(validation_message)

            review_reasons = self._review_reasons(
                raw_parse_result,
                form_type,
                found_data,
                student_match,
                subject_validation,
            )
            if review_reasons:
                return self._plan_review(
                    pdf_path=pdf_path,
                    month=month,
                    year=year,
                    dry_run=dry_run,
                    stages=stages,
                    warnings=warnings,
                    extraction=extraction,
                    form_type=form_type,
                    found_data=found_data,
                    student_match=student_match,
                    subject_validation=subject_validation,
                    canonical_subject=canonical_subject,
                    reasons=review_reasons,
                )

            student = getattr(student_match, "student", None)
            if student is None or not canonical_subject:
                raise ValueError("Kanonische Schüler- oder Fachdaten fehlen.")

            planned_path = self.renamer.plan_new_path(
                pdf_path.parent,
                student.last_name,
                student.first_name,
                canonical_subject,
                month,
                year,
                source_path=pdf_path,
            )
            stages.append(ProcessingStage.PLANNING)

            performed_action = PerformedAction.NONE
            if not dry_run:
                if not self.renamer.rename_file(pdf_path, planned_path):
                    raise OSError(f"Datei konnte nicht in {planned_path.name} umbenannt werden.")
                performed_action = PerformedAction.RENAMED
                stages.append(ProcessingStage.ACTION)

            return ProcessingResult(
                pdf_path=pdf_path,
                month=month,
                year=year,
                dry_run=dry_run,
                stages=tuple(stages),
                extraction=extraction,
                form_type=form_type,
                found_data=found_data,
                student_match=student_match,
                subject_validation=subject_validation,
                canonical_subject=canonical_subject,
                planned_name=planned_path.name,
                planned_path=planned_path,
                planned_action=PlannedAction.RENAME,
                performed_action=performed_action,
                warnings=tuple(dict.fromkeys(warnings)),
            )
        except Exception as error:
            return ProcessingResult(
                pdf_path=pdf_path,
                month=month,
                year=year,
                dry_run=dry_run,
                stages=tuple(stages),
                extraction=extraction,
                form_type=form_type,
                found_data=found_data,
                student_match=student_match,
                subject_validation=subject_validation,
                canonical_subject=canonical_subject,
                performed_action=PerformedAction.ERROR,
                warnings=tuple(dict.fromkeys(warnings)),
                error=str(error) or error.__class__.__name__,
            )

    # Short aliases used by CLI adapters and dependency-injected tests.
    process = process_document
    process_file = process_document

    def process_batch(
        self,
        pdf_paths: Iterable[Path],
        month: int,
        year: int,
        dry_run: bool = False,
        *,
        save_review_queue: bool = True,
    ) -> BatchProcessingResult:
        """Process every path even when an earlier document fails."""

        self._validate_period(month, year)
        self.renamer.reset_reservations()
        results = tuple(
            self.process_document(path, month, year, dry_run)
            for path in pdf_paths
        )

        saved = False
        queue_error = None
        if not dry_run and save_review_queue and self.review_store.count() > 0:
            try:
                saved = bool(self.review_store.save())
                if not saved:
                    queue_error = "Warteschlange konnte nicht gespeichert werden."
            except Exception as error:
                queue_error = str(error) or error.__class__.__name__
        return BatchProcessingResult(results, saved, queue_error)

    def process_folder(
        self,
        folder: Path,
        month: int,
        year: int,
        dry_run: bool = False,
    ) -> BatchProcessingResult:
        """Convenience batch entry point that includes unreadable PDF files."""

        folder = Path(folder)
        paths = sorted(
            (
                item
                for item in folder.iterdir()
                if item.is_file() and item.suffix.casefold() == ".pdf"
            ),
            key=lambda item: item.name.casefold(),
        )
        return self.process_batch(paths, month, year, dry_run)

    @staticmethod
    def _validate_period(month: int, year: int) -> None:
        if not isinstance(month, int) or isinstance(month, bool) or not 1 <= month <= 12:
            raise ValueError("month must be an integer between 1 and 12")
        if not isinstance(year, int) or isinstance(year, bool) or year < 1:
            raise ValueError("year must be a positive integer")

    def _extract(self, pdf_path: Path) -> Any:
        for method_name in ("extract_page_data", "extract_first_page", "extract"):
            method = getattr(self.extractor, method_name, None)
            if callable(method):
                return method(pdf_path)
        if callable(self.extractor):
            return self.extractor(pdf_path)
        raise TypeError("Extractor has no supported extraction method.")

    def _parse(self, extraction: Any) -> Any:
        for method_name in ("parse", "parse_document", "parse_page"):
            method = getattr(self.parser, method_name, None)
            if callable(method):
                return method(extraction)
        if callable(self.parser):
            return self.parser(extraction)
        raise TypeError("Parser has no supported parse method.")

    @staticmethod
    def _value(value: Any) -> Any:
        return getattr(value, "value", value)

    @classmethod
    def _found_data(cls, parsed: Any) -> Tuple[FoundDocumentData, str]:
        if isinstance(parsed, (tuple, list)) and len(parsed) >= 2:
            # Compatibility for simple parser stubs and the pre-structure API.
            name, subject = parsed[:2]
            return FoundDocumentData(name, name, subject, subject, 1.0), FormType.LEGACY.value

        form_type = str(cls._value(getattr(parsed, "form_type", FormType.UNKNOWN)))
        raw_name = getattr(parsed, "raw_student_name", None)
        if raw_name is None:
            raw_name = getattr(parsed, "raw_name", None)
        student_name = getattr(parsed, "student_name", None)
        if student_name is None:
            student_name = getattr(parsed, "canonical_student_name", None)
        raw_subject = getattr(parsed, "raw_subject", None)
        subject = getattr(parsed, "subject", None)
        if subject is None:
            subject = getattr(parsed, "canonical_subject", None)
        reasons = tuple(str(item) for item in (getattr(parsed, "reasons", ()) or ()))
        warnings = tuple(str(item) for item in (getattr(parsed, "warnings", ()) or ()))
        confidence = float(getattr(parsed, "confidence", 0.0) or 0.0)
        return (
            FoundDocumentData(
                raw_name,
                student_name,
                raw_subject,
                subject,
                confidence,
                reasons,
                warnings,
            ),
            form_type,
        )

    def _match_student(self, raw_name: str) -> Any:
        for method_name in ("match", "match_name", "resolve"):
            method = getattr(self.student_registry, method_name, None)
            if callable(method):
                return method(raw_name)
        raise TypeError("Student registry has no supported match method.")

    def _validate_subject(self, subject: Optional[str]) -> Any:
        method = getattr(self.validator, "validate_subject", None)
        if callable(method):
            return method(subject)
        method = getattr(self.validator, "validate_document_data", None)
        if callable(method):
            return method(None, subject)
        if callable(self.validator):
            return self.validator(subject)
        raise TypeError("Validator has no supported subject validation method.")

    @classmethod
    def _match_status(cls, match: Any) -> str:
        return str(cls._value(getattr(match, "status", MatchStatus.ERROR)))

    @classmethod
    def _validation_level(cls, validation: Any) -> str:
        return str(cls._value(getattr(validation, "level", ValidationLevel.MANUAL_CHECK)))

    def _review_reasons(
        self,
        raw_parse_result: Any,
        form_type: str,
        found_data: FoundDocumentData,
        student_match: Any,
        subject_validation: Any,
    ) -> Tuple[str, ...]:
        reasons = []
        if form_type == FormType.UNKNOWN.value:
            reasons.append("Formulartyp konnte nicht sicher erkannt werden.")
        if bool(getattr(raw_parse_result, "requires_review", False)):
            if found_data.warnings:
                reasons.extend(found_data.warnings)
            else:
                reasons.append("Parsergebnis muss manuell bestätigt werden.")
        if not found_data.student_name and not found_data.raw_student_name:
            reasons.append("Schülername wurde nicht gefunden.")
        if not found_data.subject:
            reasons.append("Fach wurde nicht gefunden.")

        status = self._match_status(student_match)
        if status != MatchStatus.EXACT.value or getattr(student_match, "student", None) is None:
            match_reason = str(getattr(student_match, "reason", "") or "")
            reasons.append(match_reason or "Schüler muss manuell bestätigt werden.")

        if self._validation_level(subject_validation) == ValidationLevel.MANUAL_CHECK.value:
            validation_reason = str(getattr(subject_validation, "message", "") or "")
            reasons.append(validation_reason or "Fach muss manuell bestätigt werden.")
        return tuple(dict.fromkeys(item for item in reasons if item))

    def _plan_review(
        self,
        *,
        pdf_path: Path,
        month: int,
        year: int,
        dry_run: bool,
        stages: list[ProcessingStage],
        warnings: list[str],
        extraction: Any,
        form_type: str,
        found_data: FoundDocumentData,
        student_match: Any,
        subject_validation: Any,
        canonical_subject: Optional[str],
        reasons: Tuple[str, ...],
    ) -> ProcessingResult:
        review_folder = self.manual_review_folder or pdf_path.parent / "manual_check"
        planned_path = self.renamer.reserve_target_path(
            review_folder / pdf_path.name,
            source_path=pdf_path,
        )
        stages.append(ProcessingStage.PLANNING)

        student_suggestions = []
        for suggestion in getattr(student_match, "suggestions", ()) or ():
            student_suggestions.append(
                str(getattr(suggestion, "full_name", suggestion))
            )
        subject_suggestions = [
            str(item) for item in (getattr(subject_validation, "suggestions", ()) or ())
        ]
        student = getattr(student_match, "student", None)
        relative_path = self._review_path(pdf_path.parent, planned_path)
        review_item = ReviewItem(
            file_path=relative_path,
            raw_name=found_data.raw_student_name or found_data.student_name,
            raw_subject=found_data.raw_subject or found_data.subject,
            reason="; ".join(reasons),
            suggestions=subject_suggestions,
            preview_text=str(getattr(extraction, "text", extraction) or "")[:500],
            form_type=form_type,
            parser_confidence=found_data.confidence,
            parser_reasons=list(found_data.reasons),
            student_match_status=self._match_status(student_match),
            student_suggestions=student_suggestions,
            canonical_student_number=getattr(student, "number", None),
            canonical_name=getattr(student, "full_name", None),
            canonical_subject=canonical_subject,
            selected_month=month,
            selected_year=year,
            planned_filename=planned_path.name,
        )

        performed_action = PerformedAction.NONE
        if not dry_run:
            if not self.renamer.move_file(pdf_path, planned_path):
                raise OSError(
                    f"Datei konnte nicht nach {planned_path.parent.name} verschoben werden."
                )
            self.review_store.add(review_item)
            performed_action = PerformedAction.MOVED_TO_REVIEW
            stages.append(ProcessingStage.ACTION)

        return ProcessingResult(
            pdf_path=pdf_path,
            month=month,
            year=year,
            dry_run=dry_run,
            stages=tuple(stages),
            extraction=extraction,
            form_type=form_type,
            found_data=found_data,
            student_match=student_match,
            subject_validation=subject_validation,
            canonical_subject=canonical_subject,
            planned_name=planned_path.name,
            planned_path=planned_path,
            planned_action=PlannedAction.MANUAL_REVIEW,
            performed_action=performed_action,
            review_item=review_item,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    @staticmethod
    def _review_path(base_folder: Path, planned_path: Path) -> str:
        try:
            return str(planned_path.relative_to(base_folder))
        except ValueError:
            return str(planned_path)


# Alternative names make the public boundary obvious to existing callers.
ProcessingService = DocumentProcessingService
ProcessingPipeline = DocumentProcessingService


__all__ = [
    "BatchProcessingResult",
    "DocumentProcessingService",
    "FoundDocumentData",
    "PerformedAction",
    "PlannedAction",
    "ProcessingPipeline",
    "ProcessingResult",
    "ProcessingService",
    "ProcessingStage",
]
