"""Display-independent background orchestration for the Tkinter application."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Event
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class BatchSettings:
    """Immutable snapshot captured on the Tk main thread before a batch."""

    folder: Path
    month: int
    year: int
    dry_run: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "folder", Path(self.folder))
        if (
            not isinstance(self.month, int)
            or isinstance(self.month, bool)
            or not 1 <= self.month <= 12
        ):
            raise ValueError("month must be between 1 and 12")
        if not isinstance(self.year, int) or isinstance(self.year, bool) or self.year < 1:
            raise ValueError("year must be positive")
        if not isinstance(self.dry_run, bool):
            raise ValueError("dry_run must be a boolean")


class WorkerEventKind(str, Enum):
    STARTED = "started"
    PROGRESS = "progress"
    RESULT = "result"
    DOCUMENT_ERROR = "document_error"
    QUEUE_SAVED = "queue_saved"
    QUEUE_ERROR = "queue_error"
    COMPLETED = "completed"
    FATAL = "fatal"
    FINISHED = "finished"


@dataclass(frozen=True)
class WorkerEvent:
    kind: WorkerEventKind
    payload: Any = None


class BatchRunner:
    """Run file work in a worker thread and communicate only through events."""

    def __init__(self, processing_service: Any) -> None:
        self.processing_service = processing_service

    def run(
        self,
        settings: BatchSettings,
        stop_event: Event,
        emit: Callable[[WorkerEvent], None],
    ) -> None:
        stopped = False
        try:
            service = self.processing_service
            service.manual_review_folder = settings.folder / "manual_check"
            service.renamer.reset_reservations()
            pdf_files = sorted(
                (
                    path
                    for path in settings.folder.iterdir()
                    if path.is_file() and path.suffix.casefold() == ".pdf"
                ),
                key=lambda path: path.name.casefold(),
            )
            emit(WorkerEvent(WorkerEventKind.STARTED, (settings, len(pdf_files))))

            for index, pdf_path in enumerate(pdf_files, start=1):
                if stop_event.is_set():
                    stopped = True
                    break
                emit(
                    WorkerEvent(
                        WorkerEventKind.PROGRESS,
                        (index, len(pdf_files), pdf_path),
                    )
                )
                try:
                    result = service.process_document(
                        pdf_path,
                        settings.month,
                        settings.year,
                        settings.dry_run,
                    )
                except Exception as error:
                    emit(
                        WorkerEvent(
                            WorkerEventKind.DOCUMENT_ERROR,
                            (pdf_path, str(error) or error.__class__.__name__),
                        )
                    )
                    continue
                emit(WorkerEvent(WorkerEventKind.RESULT, result))

            if stop_event.is_set():
                stopped = True

            store = service.review_store
            if not settings.dry_run and store.count() > 0:
                try:
                    if store.save():
                        emit(
                            WorkerEvent(
                                WorkerEventKind.QUEUE_SAVED,
                                (store.count(), store.store_path),
                            )
                        )
                    else:
                        emit(
                            WorkerEvent(
                                WorkerEventKind.QUEUE_ERROR,
                                "Warteschlange konnte nicht gespeichert werden.",
                            )
                        )
                except Exception as error:
                    emit(
                        WorkerEvent(
                            WorkerEventKind.QUEUE_ERROR,
                            str(error) or error.__class__.__name__,
                        )
                    )
            emit(WorkerEvent(WorkerEventKind.COMPLETED, stopped))
        except Exception as error:
            emit(
                WorkerEvent(
                    WorkerEventKind.FATAL,
                    str(error) or error.__class__.__name__,
                )
            )
        finally:
            emit(WorkerEvent(WorkerEventKind.FINISHED, stopped))


__all__ = [
    "BatchRunner",
    "BatchSettings",
    "WorkerEvent",
    "WorkerEventKind",
]
