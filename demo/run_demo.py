"""Process the fictional demo scans with the real recognition pipeline.

The scans and the demo registry are copied into a fresh output folder
(default: demo/output), so demo/scans, demo/Schuler.csv and the program's
own Schuler.csv never change. Requires Tesseract OCR with German data.

usage: python demo/run_demo.py [--dry-run] [--month 9] [--year 2026] [--output DIR]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DEMO_DIR.parent))

from main import build_processing_service  # noqa: E402
from pdf_extractor import ocr_unavailable_reason  # noqa: E402
from processing_service import PlannedAction  # noqa: E402


def prepare_output(output: Path) -> tuple[Path, Path]:
    if output.exists():
        shutil.rmtree(output)
    scans = output / "scans"
    shutil.copytree(DEMO_DIR / "scans", scans)
    registry = output / "Schuler.csv"
    shutil.copy2(DEMO_DIR / "Schuler.csv", registry)
    return scans, registry


def run(output: Path, month: int, year: int, dry_run: bool):
    scans, registry = prepare_output(output)
    service = build_processing_service(scans, registry_csv=registry)
    problem = ocr_unavailable_reason(service.extractor)
    if problem:
        raise SystemExit(f"OCR nicht verfügbar: {problem}")
    paths = sorted(scans.glob("*.pdf"))
    batch = service.process_batch(paths, month, year, dry_run=dry_run)
    return batch.results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="only plan, do not rename")
    parser.add_argument("--month", type=int, default=9)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--output", type=Path, default=DEMO_DIR / "output")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    results = run(args.output, args.month, args.year, args.dry_run)
    width = max(len(result.pdf_path.name) for result in results)
    for result in results:
        if result.error:
            outcome = "Fehler: " + result.error.splitlines()[0]
        elif result.planned_action == PlannedAction.RENAME:
            outcome = "-> " + result.planned_name
        else:
            outcome = "-> Prüfung: " + result.review_item.reason.splitlines()[0][:90]
        print(f"{result.pdf_path.name:<{width}}  {outcome}")
    print(f"\nErgebnis in: {args.output}" + (" (Simulation, nichts umbenannt)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
