"""Synthetic checks of the recognition measurement stand."""

from pathlib import Path
from types import SimpleNamespace

import pytest

import main as main_module
from processing_service import PlannedAction, ProcessingResult
from student_registry import Student
from tools import evaluate_recognition as stand


@pytest.mark.parametrize("name,key,subject", [
    ("Muster_Max_Mathematik_März_2026.pdf", "muster_max", "Mathematik"),
    ("Muster_Max_Deutsch_September_2026_2.pdf", "muster_max", "Deutsch"),
    ("van_Dijk_Lena_Englisch_Mai_2025.pdf", "van_dijk_lena", "Englisch"),
    ("MUSTER_max_Biologie_januar_2024.PDF", "muster_max", "Biologie"),
])
def test_reference_is_read_from_renamed_filenames(name, key, subject):
    assert stand.parse_renamed_filename(name) == stand.Expectation(key, subject)


@pytest.mark.parametrize("name", [
    "scan_001.pdf",
    "Muster_Mathematik_März_2026.pdf",
    "Muster_Max_Mathematik_Sommer_2026.pdf",
    "Muster_Max_Mathematik_März_26.pdf",
    "Muster_Max_Mathematik_März_2026.txt",
])
def test_unrelated_filenames_have_no_reference(name):
    assert stand.parse_renamed_filename(name) is None


def test_expected_csv_with_bom_is_keyed_by_file_name(tmp_path):
    path = tmp_path / stand.EXPECTED_FILENAME
    path.write_text(
        "﻿Datei;Nachname;Vorname;Fach\nA.pdf;van Dijk;Lena;Deutsch\n\n",
        encoding="utf-8",
    )

    assert stand.load_expected_csv(path) == {
        "a.pdf": stand.Expectation("van_dijk_lena", "Deutsch"),
    }


@pytest.mark.parametrize("content", [
    "Datei;Nachname;Fach\na.pdf;Muster;Deutsch\n",
    "Datei;Nachname;Vorname;Fach\na.pdf;Muster;;Deutsch\n",
    "Datei;Nachname;Vorname;Fach\na.pdf;Muster;Max;Deutsch\nA.pdf;Muster;Max;Deutsch\n",
])
def test_invalid_reference_is_rejected(tmp_path, content):
    path = tmp_path / "ref.csv"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        stand.load_expected_csv(path)


def _result(action, student=None, subject=None, error=None):
    return SimpleNamespace(
        error=error,
        planned_action=action,
        student_match=SimpleNamespace(student=student),
        canonical_subject=subject,
    )


KNOWN = Student(1, "Muster", "Max")
OTHER = Student(2, "Probe", "Paula")
REGISTRY = {"muster_max", "probe_paula"}
CATALOG = {"deutsch", "mathematik"}


@pytest.mark.parametrize("expectation,result,outcome", [
    (stand.Expectation("muster_max", "Deutsch"),
     _result(PlannedAction.RENAME, KNOWN, "Deutsch"), stand.Outcome.CORRECT_AUTO),
    (stand.Expectation("muster_max", "Deutsch"),
     _result(PlannedAction.RENAME, OTHER, "Deutsch"), stand.Outcome.WRONG_AUTO),
    (stand.Expectation("muster_max", "Deutsch"),
     _result(PlannedAction.RENAME, KNOWN, "Mathematik"), stand.Outcome.WRONG_AUTO),
    (stand.Expectation("muster_max", "Deutsch"),
     _result(PlannedAction.MANUAL_REVIEW), stand.Outcome.UNNECESSARY_REVIEW),
    (stand.Expectation("neu_nina", "Deutsch"),
     _result(PlannedAction.MANUAL_REVIEW), stand.Outcome.JUSTIFIED_REVIEW),
    (stand.Expectation("muster_max", "Informatik"),
     _result(PlannedAction.MANUAL_REVIEW), stand.Outcome.JUSTIFIED_REVIEW),
    (stand.Expectation("muster_max", "Deutsch"),
     _result(PlannedAction.NONE, error="kaputt"), stand.Outcome.ERROR),
    (None, _result(PlannedAction.RENAME, KNOWN, "Deutsch"), stand.Outcome.UNLABELED),
])
def test_classification(expectation, result, outcome):
    assert stand.classify(expectation, result, REGISTRY, CATALOG) == outcome


class _StubRegistry:
    is_available = True
    load_error = None

    def __init__(self, folder):
        self.students = (KNOWN, OTHER)
        self.csv_path = folder / "Schuler.csv"
        self.aliases_path = folder / "student_aliases.json"


class _StubService:
    """Plans one decision per file name; never touches the file system."""

    def __init__(self, folder, decisions):
        self.student_registry = _StubRegistry(folder)
        self.renamer = SimpleNamespace(reset_reservations=lambda: None)
        self.decisions = decisions

    def process_document(self, pdf_path, month, year, dry_run=False):
        assert dry_run is True
        student, subject = self.decisions[pdf_path.name]
        if student is None:
            return ProcessingResult(
                pdf_path, month, year, dry_run,
                planned_action=PlannedAction.MANUAL_REVIEW,
                found_data=SimpleNamespace(raw_student_name="X", raw_subject="Y"),
                review_item=SimpleNamespace(reason="synthetisch"),
            )
        return ProcessingResult(
            pdf_path, month, year, dry_run,
            planned_action=PlannedAction.RENAME,
            student_match=SimpleNamespace(student=student),
            canonical_subject=subject,
        )


def _run_stand(monkeypatch, tmp_path, decisions, argv_extra=()):
    folder = tmp_path / "scans"
    folder.mkdir(exist_ok=True)
    for name in decisions:
        (folder / name).write_bytes(b"%PDF synthetic")
    (folder / "Schuler.csv").write_text("Nummer;Nachname;Vorname\n", encoding="utf-8")
    monkeypatch.setattr(
        main_module,
        "build_processing_service",
        lambda *_a, **_k: _StubService(folder, decisions),
    )
    before = {path: path.read_bytes() for path in folder.iterdir()}
    code = stand.main([str(folder), "--month", "3", "--year", "2026", *argv_extra])
    after = {path: path.read_bytes() for path in folder.iterdir()}
    return code, before == after


def test_stand_reports_counts_and_keeps_files(monkeypatch, tmp_path, capsys):
    folder = tmp_path / "scans"
    folder.mkdir()
    (folder / stand.EXPECTED_FILENAME).write_text(
        "Datei;Nachname;Vorname;Fach\n"
        "1.pdf;Muster;Max;Deutsch\n2.pdf;Neu;Nina;Deutsch\n10.pdf;Probe;Paula;Mathematik\n",
        encoding="utf-8",
    )

    code, unchanged = _run_stand(monkeypatch, tmp_path, {
        "1.pdf": (KNOWN, "Deutsch"),
        "2.pdf": (None, None),
        "10.pdf": (None, None),
    })

    output = capsys.readouterr().out
    assert code == stand.EXIT_OK
    assert unchanged
    assert "richtig automatisch: 1" in output
    assert "berechtigte Prüfung: 1" in output
    assert "unnötige Prüfung: 1" in output
    assert output.index("2.pdf") < output.index("10.pdf")


def test_wrong_automatic_decision_fails_the_stand(monkeypatch, tmp_path, capsys):
    code, _ = _run_stand(
        monkeypatch,
        tmp_path,
        {"Muster_Max_Deutsch_März_2026.pdf": (OTHER, "Deutsch")},
        ["--from-filenames"],
    )

    assert code == stand.EXIT_WRONG_AUTOMATIC
    assert "FALSCH automatisch: 1" in capsys.readouterr().out


def test_missing_reference_is_a_usage_error(tmp_path, capsys):
    (tmp_path / "1.pdf").write_bytes(b"%PDF")

    assert stand.main([str(tmp_path)]) == stand.EXIT_USAGE
    assert "--from-filenames" in capsys.readouterr().out


def test_changed_data_is_reported():
    rows = [stand.EvaluationRow("a.pdf", stand.Outcome.CORRECT_AUTO, "", "", 0.1)]

    assert stand.exit_code(rows, data_changed=True) == stand.EXIT_DATA_CHANGED
    assert stand.exit_code(rows, data_changed=False) == stand.EXIT_OK
