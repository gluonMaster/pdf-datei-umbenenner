"""UI-independent actions for resolving items in the manual review queue."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from file_renamer import FileRenamer
from review_queue import ReviewItem, ReviewStore
from student_registry import Student, normalize_name


class ReviewDecision(str, Enum):
    """Explicit choices offered by the review tab."""

    USE_EXISTING = "use_existing"
    ADD_NEW = "add_new"
    RENAME_ONLY = "rename_only"


class ReviewActionStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    ERROR = "error"


@dataclass(frozen=True)
class ReviewActionRequest:
    item: ReviewItem
    base_folder: Path
    last_name: str
    first_name: str
    subject: str
    decision: ReviewDecision = ReviewDecision.ADD_NEW
    student_number: Optional[int] = None
    save_student_alias: bool = True
    fallback_month: Optional[int] = None
    fallback_year: Optional[int] = None


@dataclass(frozen=True)
class ReviewActionResult:
    status: ReviewActionStatus
    message: str
    target_path: Optional[Path] = None
    student: Optional[Student] = None
    registry_saved: bool = False
    student_alias_saved: bool = False
    subject_alias_saved: bool = False
    file_moved: bool = False
    queue_removed: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status == ReviewActionStatus.SUCCESS


class ReviewActionService:
    """Apply one explicit review decision without depending on Tkinter.

    Registry and alias changes are persisted before the PDF is moved.  If a
    later operation fails, the queue entry is retained and the result reports
    whether an earlier persistent change was already made.
    """

    def __init__(
        self,
        student_registry: Any,
        subject_aliases: Any,
        review_store: ReviewStore,
        renamer: Optional[FileRenamer] = None,
    ) -> None:
        self.student_registry = student_registry
        self.subject_aliases = subject_aliases
        self.review_store = review_store
        self.renamer = renamer or FileRenamer()

    def apply(self, request: ReviewActionRequest) -> ReviewActionResult:
        registry_saved = False
        student_alias_saved = False
        subject_alias_saved = False
        file_moved = False
        student = None
        target_path = None

        try:
            last_name = self._required(request.last_name, "Nachname")
            first_name = self._required(request.first_name, "Vorname")
            subject = self._required(request.subject, "Fach")
            month = (
                request.item.selected_month
                if request.item.selected_month is not None
                else request.fallback_month
            )
            year = (
                request.item.selected_year
                if request.item.selected_year is not None
                else request.fallback_year
            )
            self._validate_period(month, year)

            source_path = self._source_path(request.base_folder, request.item.file_path)
            if not source_path.is_file():
                raise FileNotFoundError(f"Datei nicht gefunden: {source_path}")

            decision = ReviewDecision(request.decision)
            if decision == ReviewDecision.USE_EXISTING:
                if request.student_number is None:
                    raise ValueError("Bitte wählen Sie einen bestehenden Schüler aus.")
                student = self.student_registry.confirm_existing(
                    request.item.raw_name or f"{last_name} {first_name}",
                    request.student_number,
                    save_alias=bool(request.save_student_alias and request.item.raw_name),
                )
                student_alias_saved = bool(
                    request.save_student_alias and request.item.raw_name
                )
                last_name, first_name = student.last_name, student.first_name
            elif decision == ReviewDecision.ADD_NEW:
                student, registry_saved = self._find_or_add_student(last_name, first_name)
                last_name, first_name = student.last_name, student.first_name
                if request.save_student_alias and request.item.raw_name:
                    # When the user corrected an OCR spelling, remember that
                    # spelling too.  The same scan name will then match on the
                    # next run instead of returning to manual review.
                    self.student_registry.confirm_existing(
                        request.item.raw_name,
                        student.number,
                        save_alias=True,
                    )
                    student_alias_saved = True
            elif decision != ReviewDecision.RENAME_ONLY:
                raise ValueError(f"Unbekannte Prüfaktion: {decision}")

            raw_subject = (request.item.raw_subject or "").strip()
            if raw_subject and raw_subject != subject:
                add_and_save = getattr(self.subject_aliases, "add_and_save", None)
                if callable(add_and_save):
                    alias_saved = bool(add_and_save(raw_subject, subject))
                else:
                    previous_aliases = self.subject_aliases.get_all_aliases()
                    try:
                        alias_saved = bool(
                            self.subject_aliases.add_alias(raw_subject, subject)
                            and self.subject_aliases.save()
                        )
                    except Exception:
                        if hasattr(self.subject_aliases, "aliases"):
                            self.subject_aliases.aliases = previous_aliases
                        raise
                    if not alias_saved and hasattr(self.subject_aliases, "aliases"):
                        self.subject_aliases.aliases = previous_aliases
                if not alias_saved:
                    raise OSError("Fachalias konnte nicht gespeichert werden.")
                subject_alias_saved = True

            target_path = self.renamer.plan_new_path(
                Path(request.base_folder),
                last_name,
                first_name,
                subject,
                int(month),
                int(year),
                source_path=source_path,
            )
            try:
                moved = self.renamer.move_file(source_path, target_path)
            except Exception:
                self._release_reservation(target_path)
                raise
            if not moved:
                self._release_reservation(target_path)
                raise OSError(
                    f"PDF konnte nicht nach {target_path.name} verschoben werden. "
                    + (getattr(self.renamer, "last_error", None) or "")
                )
            file_moved = True

            if not self._remove_from_queue_safely(request.item.file_path):
                rollback_error = None
                try:
                    rolled_back = self.renamer.move_file(target_path, source_path)
                    if not rolled_back:
                        rollback_error = getattr(self.renamer, "last_error", None)
                except Exception as error:
                    rolled_back = False
                    rollback_error = str(error) or error.__class__.__name__
                self._release_reservation(target_path)
                if rolled_back:
                    file_moved = False
                    persistent_change = (
                        registry_saved or student_alias_saved or subject_alias_saved
                    )
                    return ReviewActionResult(
                        ReviewActionStatus.PARTIAL if persistent_change else ReviewActionStatus.ERROR,
                        "Warteschlange konnte nicht gespeichert werden; die PDF wurde "
                        "an ihren Prüfpfad zurückverschoben. Der Eintrag bleibt erhalten.",
                        target_path,
                        student,
                        registry_saved,
                        student_alias_saved,
                        subject_alias_saved,
                        file_moved=False,
                        queue_removed=False,
                    )

                recovery_saved = self._retarget_queue_item(
                    request.item,
                    target_path,
                    Path(request.base_folder),
                )
                return ReviewActionResult(
                    ReviewActionStatus.PARTIAL,
                    "PDF wurde umbenannt, konnte aber nach einem Warteschlangenfehler "
                    "nicht zurückverschoben werden. "
                    + (
                        "Der Eintrag wurde auf den neuen Pfad aktualisiert."
                        if recovery_saved
                        else f"Bitte Datei und Warteschlange manuell prüfen: {target_path}"
                    )
                    + (f" Rückverschieben: {rollback_error}" if rollback_error else ""),
                    target_path,
                    student,
                    registry_saved,
                    student_alias_saved,
                    subject_alias_saved,
                    file_moved=True,
                    queue_removed=False,
                )

            return ReviewActionResult(
                ReviewActionStatus.SUCCESS,
                f"PDF wurde als {target_path.name} gespeichert.",
                target_path,
                student,
                registry_saved,
                student_alias_saved,
                subject_alias_saved,
                file_moved=True,
                queue_removed=True,
            )
        except Exception as error:
            persistent_change = (
                registry_saved or student_alias_saved or subject_alias_saved or file_moved
            )
            status = (
                ReviewActionStatus.PARTIAL
                if persistent_change
                else ReviewActionStatus.ERROR
            )
            prefix = (
                "Teilweise ausgeführt; der Eintrag bleibt in der Warteschlange: "
                if persistent_change
                else "Aktion nicht ausgeführt: "
            )
            return ReviewActionResult(
                status,
                prefix + (str(error) or error.__class__.__name__),
                target_path,
                student,
                registry_saved,
                student_alias_saved,
                subject_alias_saved,
                file_moved,
                False,
            )

    def _find_or_add_student(self, last_name: str, first_name: str) -> tuple[Student, bool]:
        requested_key = (normalize_name(last_name), normalize_name(first_name))
        canonical_matches = [
            item
            for item in getattr(self.student_registry, "students", ())
            if (normalize_name(item.last_name), normalize_name(item.first_name))
            == requested_key
        ]
        if len(canonical_matches) == 1:
            # Makes a retry safe when the registry write succeeded but moving
            # the PDF failed during the preceding attempt.
            return self.student_registry.select_existing(canonical_matches[0].number), False
        if len(canonical_matches) > 1:
            raise ValueError("Mehrere Schüler haben diesen Namen; bitte auswählen.")

        student = self.student_registry.add_student(last_name, first_name)
        return student, True

    def _remove_from_queue_safely(self, file_path: str) -> bool:
        previous_items = self.review_store.get_all()
        try:
            saved = bool(
                self.review_store.remove(file_path) and self.review_store.save()
            )
        except Exception:
            saved = False
        if saved:
            return True
        # Keep the item available in memory as well as in the unchanged
        # on-disk queue when its atomic save failed.
        self.review_store.items = previous_items
        return False

    def _retarget_queue_item(
        self,
        original_item: ReviewItem,
        target_path: Path,
        base_folder: Path,
    ) -> bool:
        try:
            stored_path = str(target_path.relative_to(base_folder))
        except ValueError:
            stored_path = str(target_path)
        recovery_item = replace(
            original_item,
            file_path=stored_path,
            planned_filename=target_path.name,
        )
        updated_items = []
        replaced_existing = False
        for item in self.review_store.get_all():
            if item.file_path == original_item.file_path:
                updated_items.append(recovery_item)
                replaced_existing = True
            else:
                updated_items.append(item)
        if not replaced_existing:
            # The UI may hold a stale item while the in-memory queue no longer
            # contains it.  Never report a successful recovery after merely
            # saving an empty queue: add an explicit entry for the actual PDF.
            updated_items.append(recovery_item)
        self.review_store.items = updated_items
        try:
            return bool(self.review_store.save())
        except Exception:
            return False

    def _release_reservation(self, path: Path) -> None:
        release = getattr(self.renamer, "release_target_path", None)
        if callable(release):
            release(path)

    @staticmethod
    def _source_path(base_folder: Path, item_path: str) -> Path:
        path = Path(item_path)
        return path if path.is_absolute() else Path(base_folder) / path

    @staticmethod
    def _required(value: str, label: str) -> str:
        clean = (value or "").strip()
        if not clean:
            raise ValueError(f"{label} darf nicht leer sein.")
        return clean

    @staticmethod
    def _validate_period(month: Optional[int], year: Optional[int]) -> None:
        if not isinstance(month, int) or isinstance(month, bool) or not 1 <= month <= 12:
            raise ValueError("Monat muss zwischen 1 und 12 liegen.")
        if not isinstance(year, int) or isinstance(year, bool) or year < 1:
            raise ValueError("Jahr muss eine positive Ganzzahl sein.")


# Concise aliases for integrations that used the earlier "correction" wording.
ReviewAction = ReviewDecision
ReviewCoordinator = ReviewActionService


__all__ = [
    "ReviewAction",
    "ReviewActionRequest",
    "ReviewActionResult",
    "ReviewActionService",
    "ReviewActionStatus",
    "ReviewCoordinator",
    "ReviewDecision",
]
