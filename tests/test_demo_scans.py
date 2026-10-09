"""The fictional demo scans go through the real OCR pipeline as documented."""

import importlib.util
from pathlib import Path

import pytest

from pdf_extractor import OCRFallbackExtractor, ocr_unavailable_reason
from processing_service import PlannedAction


ROOT = Path(__file__).resolve().parents[1]


def _demo_module():
    spec = importlib.util.spec_from_file_location("run_demo", ROOT / "demo" / "run_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_scans_are_renamed_or_reviewed_as_documented(tmp_path):
    problem = ocr_unavailable_reason(OCRFallbackExtractor())
    if problem:
        pytest.skip(problem)
    demo_registry = (ROOT / "demo" / "Schuler.csv").read_bytes()

    results = _demo_module().run(tmp_path / "demo", month=9, year=2026, dry_run=True)

    by_name = {result.pdf_path.name: result for result in results}
    renamed = {
        name: result.planned_name
        for name, result in by_name.items()
        if result.planned_action == PlannedAction.RENAME
    }
    assert renamed == {
        "Scan_2026-09-30_0001.pdf": "Beispiel_Lena_Mathematik_September_2026.pdf",
        "Scan_2026-09-30_0002.pdf": "Muster_Max_Deutsch_September_2026.pdf",
        # Photocopy look without a GR/EZ column.
        "Scan_2026-09-30_0003.pdf": "Schneider_Jonas_Englisch_September_2026.pdf",
        "Scan_2026-09-30_0004.pdf": "Weber_Mia_Biologie_September_2026.pdf",
        # Older form scanned upside down.
        "Scan_2026-09-30_0005.pdf": "Probe_Paula_Physik_September_2026.pdf",
    }
    new_student = by_name["Scan_2026-09-30_0006.pdf"].review_item
    assert new_student.new_student_proposal == {
        "last_name": "Hoffmann", "first_name": "Sophie", "birth_date": "2013-11-05",
    }
    new_subject = by_name["Scan_2026-09-30_0007.pdf"].review_item
    assert new_subject.subject_proposal == "Latein"
    assert all(not result.error for result in results)
    # The demo files themselves never change.
    assert (ROOT / "demo" / "Schuler.csv").read_bytes() == demo_registry
