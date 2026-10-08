"""Command-line adapter for the document processing service."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import List, Optional, Tuple

from alias_manager import AliasManager
from file_renamer import FileRenamer
from form_parser import FormParser
from ocr import TesseractOCRProvider
from pdf_extractor import OCRFallbackExtractor, PDFExtractor
from processing_service import (
    BatchProcessingResult,
    DocumentProcessingService,
    PlannedAction,
    ProcessingResult,
)
from review_queue import ReviewStore
from student_registry import StudentRegistry
from student_registry_service import StudentRegistryPaths
from text_parser import TextParser
from validator import Validator


def build_processing_service(
    input_folder: Path,
    *,
    tesseract_path: Optional[str] = None,
    registry_csv: Optional[Path] = None,
) -> DocumentProcessingService:
    """Assemble the CLI pipeline for one input folder.

    ``registry_csv`` replaces the project registry, for example with a
    temporary copy; its aliases are read from the file next to it.
    """

    project_folder = Path(__file__).parent
    input_folder = Path(input_folder)
    # Explicitly bind the parser to this application's alias store.
    # This prevents a GUI or test created earlier in the same Python
    # process from leaking its singleton manager into the CLI.
    TextParser.set_alias_manager(
        AliasManager(
            project_folder / "subjects_aliases.json",
            create_if_missing=False,
        )
    )
    if registry_csv is None:
        registry_paths = StudentRegistryPaths.in_directory(project_folder)
    else:
        registry_csv = Path(registry_csv)
        registry_paths = StudentRegistryPaths(
            registry_csv, registry_csv.with_name("student_aliases.json")
        )
    review_store = ReviewStore(input_folder / "manual_review.json")
    review_store.load()
    return DocumentProcessingService(
        extractor=OCRFallbackExtractor(
            PDFExtractor(),
            TesseractOCRProvider(tesseract_path),
        ),
        parser=FormParser(),
        student_registry=StudentRegistry(
            registry_paths.csv_path,
            registry_paths.aliases_path,
        ),
        validator=Validator(),
        review_store=review_store,
        renamer=FileRenamer(),
        manual_review_folder=input_folder / "manual_check",
    )


class PDFRenamerApp:
    """Thin CLI facade retained for callers of the original application API."""

    def __init__(
        self,
        input_folder: Path,
        dry_run: bool = False,
        month: Optional[int] = None,
        year: Optional[int] = None,
        *,
        service: Optional[DocumentProcessingService] = None,
        tesseract_path: Optional[str] = None,
    ) -> None:
        if month is None or year is None:
            raise ValueError("Monat und Jahr müssen explizit angegeben werden.")
        self.input_folder = Path(input_folder)
        self.dry_run = dry_run
        self.month = month
        self.year = year
        self.manual_check_folder = self.input_folder / "manual_check"

        if service is None:
            service = build_processing_service(
                self.input_folder,
                tesseract_path=tesseract_path,
            )
        self.service = service
        # Compatibility attributes for integrations that inspect the facade.
        self.extractor = service.extractor
        self.parser = service.parser
        self.validator = service.validator
        self.renamer = service.renamer
        self.review_store = service.review_store
        self.stats = self._empty_stats()

    @staticmethod
    def _empty_stats() -> dict[str, int]:
        return {"total": 0, "success": 0, "warning": 0, "manual": 0, "error": 0}

    def find_pdf_files(self) -> List[Path]:
        """Find every PDF candidate, including files that may fail to open."""

        return sorted(
            (
                path
                for path in self.input_folder.iterdir()
                if path.is_file() and path.suffix.casefold() == ".pdf"
            ),
            key=lambda path: path.name.casefold(),
        )

    def process_file(self, pdf_path: Path) -> Tuple[bool, str]:
        """Compatibility wrapper around the one-document service method."""

        if self.review_store.load_error:
            return False, self.review_store.load_error

        result = self.service.process_document(
            pdf_path,
            self.month,
            self.year,
            self.dry_run,
        )
        self._record_result(result)
        self._print_result(result)
        if result.error:
            return False, result.error
        if result.requires_review:
            return False, result.review_item.reason if result.review_item else "Manuelle Prüfung"
        return True, "Umbenennung geplant" if self.dry_run else "Erfolgreich umbenannt"

    def run(self) -> BatchProcessingResult:
        """Run an isolated batch and return its complete action plan."""

        print("=" * 60)
        print("PDF Datei Umbenenner")
        print("=" * 60)
        print(f"Eingabeordner: {self.input_folder}")
        print(f"Zeitraum: {self.month:02d}/{self.year}")
        if self.dry_run:
            print("MODUS: Simulation (keine Änderungen)")

        if self.review_store.load_error:
            print(f"Fehler: {self.review_store.load_error}")
            return BatchProcessingResult(
                (),
                review_queue_saved=False,
                review_queue_error=self.review_store.load_error,
            )

        pdf_files = self.find_pdf_files()
        if not pdf_files:
            print("Keine PDF-Dateien gefunden!")
            return BatchProcessingResult(())
        print(f"Gefunden: {len(pdf_files)} PDF-Datei(en)")

        batch = self.service.process_batch(
            pdf_files,
            self.month,
            self.year,
            self.dry_run,
        )
        self.stats = self._empty_stats()
        for result in batch.results:
            self._record_result(result)
            self._print_result(result)

        if batch.review_queue_saved:
            print(
                "\nWarteschlange für manuelle Prüfung gespeichert: "
                f"{self.review_store.store_path.name}"
            )
        if batch.review_queue_error:
            print(f"\nFehler beim Speichern der Warteschlange: {batch.review_queue_error}")
        self.print_statistics()
        return batch

    def _record_result(self, result: ProcessingResult) -> None:
        self.stats["total"] += 1
        if result.error:
            self.stats["error"] += 1
        elif result.planned_action == PlannedAction.MANUAL_REVIEW:
            self.stats["manual"] += 1
        elif result.warnings:
            self.stats["warning"] += 1
        else:
            self.stats["success"] += 1

    @staticmethod
    def _print_result(result: ProcessingResult) -> None:
        print(f"\nVerarbeite: {result.pdf_path.name}")
        if result.error:
            print(f"  -> Fehler: {result.error}")
            return
        if result.found_data:
            print(f"  Erkannter Name: {result.found_data.raw_student_name or '—'}")
            print(f"  Erkanntes Fach: {result.found_data.raw_subject or '—'}")
        if result.requires_review:
            prefix = "[Simulation] " if result.dry_run else ""
            print(f"  -> {prefix}Manuelle Prüfung: {result.review_item.reason}")
            print(f"  -> Ziel: {result.planned_path}")
            return
        prefix = "[Simulation] " if result.dry_run else ""
        print(f"  -> {prefix}Neue Datei: {result.planned_name}")
        for warning in result.warnings:
            print(f"  Warnung: {warning}")

    def print_statistics(self) -> None:
        print("\n" + "=" * 60)
        print("STATISTIK")
        print("=" * 60)
        print(f"Insgesamt verarbeitet:          {self.stats['total']}")
        print(f"Erfolgreich umbenannt/geplant: {self.stats['success']}")
        print(f"Mit Warnungen:                 {self.stats['warning']}")
        print(f"Manuelle Prüfung erforderlich: {self.stats['manual']}")
        print(f"Fehler:                        {self.stats['error']}")
        print("=" * 60)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Automatisches Umbenennen von gescannten PDF-Formularen"
    )
    parser.add_argument(
        "folder",
        type=str,
        nargs="?",
        help="Pfad zum Ordner mit PDF-Dateien (ohne Ordner wird die GUI gestartet)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulation ohne Änderungen an PDF-, CSV- oder JSON-Dateien",
    )
    parser.add_argument("--gui", action="store_true", help="GUI-Modus starten")
    parser.add_argument(
        "--month",
        type=int,
        choices=range(1, 13),
        help="Monat (1-12) für Dateinamen",
    )
    parser.add_argument("--year", type=int, help="Jahr für Dateinamen")
    parser.add_argument(
        "--tesseract-path",
        type=str,
        help=(
            "Optionaler Pfad zu tesseract.exe; sonst werden TESSERACT_CMD, "
            "PATH und Standardordner geprüft"
        ),
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    # The application contains German/Russian diagnostics; do not let a legacy
    # Windows code page make even ``--help`` crash on an umlaut.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    if args.gui or not args.folder:
        try:
            from gui_main import PDFRenamerGUI

            PDFRenamerGUI().run()
        except ImportError:
            print("Fehler: GUI-Komponenten nicht verfügbar!")
            return 1
        return 0

    if args.month is None or args.year is None:
        parser.error("für die CLI-Verarbeitung sind --month und --year erforderlich")
    if args.year < 1:
        parser.error("--year muss eine positive Ganzzahl sein")

    input_folder = Path(args.folder)
    if not input_folder.exists() or not input_folder.is_dir():
        parser.error(f"Ordner '{args.folder}' existiert nicht")

    app = PDFRenamerApp(
        input_folder,
        dry_run=args.dry_run,
        month=args.month,
        year=args.year,
        tesseract_path=args.tesseract_path,
    )
    batch = app.run()
    return 2 if batch.errors or batch.review_queue_error else 0


if __name__ == "__main__":
    sys.exit(main())
