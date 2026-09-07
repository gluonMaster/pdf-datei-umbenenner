from pathlib import Path

import pytest

import student_registry as registry_module
from student_registry import MatchStatus, RegistryValidationError, StudentRegistry
from student_registry_service import StudentRegistryPaths, StudentRegistryService


def write_registry(path: Path, rows):
    lines = ["Nummer;Nachname;Vorname;Aktiv"]
    lines.extend(";".join(str(value) for value in row) for row in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def service(tmp_path, rows=((1, "Doe", "Jane", 1), (2, "Roe", "John", 0))):
    csv_path = tmp_path / "Schuler.csv"
    write_registry(csv_path, rows)
    registry = StudentRegistry(csv_path, tmp_path / "student_aliases.json")
    return StudentRegistryService(registry)


def test_paths_and_search_are_centralized_and_normalized(tmp_path):
    admin = service(tmp_path)

    assert StudentRegistryPaths.in_directory(tmp_path).csv_path == tmp_path / "Schuler.csv"
    assert [item.number for item in admin.search("  jAnE    doE ")] == [1]
    assert [item.number for item in admin.search("", active=False)] == [2]


def test_aliases_can_be_viewed_and_removed_without_deleting_student(tmp_path):
    admin = service(tmp_path)
    admin.registry.confirm_existing("D0e Jane", 1)

    assert [item.name for item in admin.aliases_for(1)] == ["D0e Jane"]
    assert admin.remove_alias("  d0E   JANE ") is True
    assert admin.remove_alias("missing alias") is False
    assert admin.registry.select_existing(1).full_name == "Doe Jane"
    assert admin.registry.match("D0e Jane").status != MatchStatus.EXACT


def test_duplicate_detection_reports_exact_and_similar_names(tmp_path):
    admin = service(
        tmp_path,
        (
            (1, "Muster", "Anna", 1),
            (2, "Muster", "Anna", 0),
            (3, "Muster", "Anne", 1),
        ),
    )

    duplicates = admin.find_potential_duplicates(threshold=0.80)

    assert any(item.first.number == 1 and item.second.number == 2 for item in duplicates)
    assert duplicates[0].score == 1.0


@pytest.mark.parametrize("first_header", ["Forname", "Vorname"])
@pytest.mark.parametrize("delimiter", [";", ",", "\t", "|"])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_import_accepts_headers_bom_delimiters_and_trims_names(
    tmp_path, first_header, delimiter, encoding
):
    admin = service(tmp_path, ((1, "Doe", "Jane", 1),))
    source = tmp_path / "monthly.csv"
    source.write_text(
        delimiter.join(("Nummer", "Nachname", first_header, "Aktiv"))
        + "\n"
        + delimiter.join(("1", "  Doe  ", " Jane ", "1"))
        + "\n"
        + delimiter.join(("4", "  Müller  ", " Éva ", "0"))
        + "\n",
        encoding=encoding,
    )

    preview = admin.preview_import(source)

    assert [item.number for item in preview.unchanged] == [1]
    assert preview.new == (preview.rows[1],)
    assert preview.new[0].last_name == "Müller"
    assert preview.new[0].first_name == "Éva"
    assert preview.new[0].active is False


def test_preview_separates_duplicates_conflicts_and_missing_without_mutation(tmp_path):
    admin = service(
        tmp_path,
        ((1, "Doe", "Jane", 1), (2, "Roe", "John", 1), (8, "Keep", "History", 0)),
    )
    original = admin.registry.csv_path.read_bytes()
    source = tmp_path / "monthly.csv"
    source.write_text(
        "Nummer;Nachname;Vorname;Aktiv\n"
        "1;Changed;Jane;1\n"
        "3;Roe;John;1\n"
        "4;Duplicate;Number;1\n"
        "4;Another;Number;1\n"
        "5;Same;Name;1\n"
        "6; same ; name ;1\n"
        "7;Clean;New;1\n",
        encoding="utf-8",
    )

    preview = admin.preview_import(source)

    assert [item.number for item in preview.new] == [7]
    assert [item.student.number for item in preview.conflicts] == [1, 3]
    assert [item.student.number for item in preview.duplicates] == [4, 4, 5, 6]
    assert [item.number for item in preview.missing] == [2, 8]
    assert admin.registry.csv_path.read_bytes() == original


def test_import_reports_canonical_name_that_conflicts_with_an_existing_alias(tmp_path):
    admin = service(tmp_path, ((1, "Canonical", "Student", 1),))
    admin.registry.confirm_existing("OCR Student", 1)
    source = tmp_path / "monthly.csv"
    source.write_text(
        "Nummer;Nachname;Vorname\n7;OCR;Student\n",
        encoding="utf-8",
    )

    preview = admin.preview_import(source)

    assert not preview.new
    assert len(preview.conflicts) == 1
    assert "Alias" in preview.conflicts[0].reason


def test_import_requires_confirmation_keeps_missing_and_updates_live_matcher(tmp_path):
    admin = service(tmp_path, ((1, "Doe", "Jane", 1), (9, "Historical", "Person", 0)))
    original = admin.registry.csv_path.read_bytes()
    source = tmp_path / "monthly.csv"
    source.write_text(
        "Nummer;Nachname;Vorname\n1;Doe;Jane\n4;New;Student\n",
        encoding="utf-8",
    )
    preview = admin.preview_import(source)

    with pytest.raises(RegistryValidationError, match="ausdrücklich bestätigt"):
        admin.apply_import(preview)

    result = admin.apply_import(preview, confirmed=True)

    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert result.backup_path.read_bytes() == original
    assert admin.registry.select_existing(9).active is False
    assert admin.registry.match("New Student").status == MatchStatus.EXACT
    assert admin.registry.match("Historical Person").status == MatchStatus.EXACT


def test_import_refuses_stale_preview(tmp_path):
    admin = service(tmp_path, ((1, "Doe", "Jane", 1),))
    source = tmp_path / "monthly.csv"
    source.write_text(
        "Nummer;Nachname;Vorname\n1;Doe;Jane\n4;New;Student\n",
        encoding="utf-8",
    )
    preview = admin.preview_import(source)
    admin.registry.add_student("Other", "Person")

    with pytest.raises(RegistryValidationError, match="nach der Vorschau geändert"):
        admin.apply_import(preview, confirmed=True)


def test_failed_atomic_import_preserves_original_and_in_memory_matcher(
    tmp_path, monkeypatch
):
    admin = service(tmp_path, ((1, "Doe", "Jane", 1),))
    source = tmp_path / "monthly.csv"
    source.write_text(
        "Nummer;Nachname;Vorname\n1;Doe;Jane\n4;New;Student\n",
        encoding="utf-8",
    )
    preview = admin.preview_import(source)
    original = admin.registry.csv_path.read_bytes()
    real_replace = registry_module.os.replace

    def fail_registry_replace(source_path, destination_path):
        if Path(destination_path) == admin.registry.csv_path:
            raise OSError("simulated import replace failure")
        return real_replace(source_path, destination_path)

    monkeypatch.setattr(registry_module.os, "replace", fail_registry_replace)
    with pytest.raises(registry_module.RegistryWriteError):
        admin.apply_import(preview, confirmed=True)

    assert admin.registry.csv_path.read_bytes() == original
    assert admin.registry.match("New Student").status != MatchStatus.EXACT
    assert list(tmp_path.glob(".Schuler.csv.*.tmp")) == []


def test_add_and_edit_immediately_update_the_shared_matcher(tmp_path):
    admin = service(tmp_path, ((1, "Doe", "Jane", 1),))
    matcher = admin.registry

    added = matcher.add_student("Public", "Ada")
    assert matcher.match("Public Ada").status == MatchStatus.EXACT

    matcher.update_student(added.number, first_name="Augusta")
    assert matcher.match("Public Augusta").status == MatchStatus.EXACT
    assert matcher.match("Public Ada").status != MatchStatus.EXACT


@pytest.mark.parametrize("contents, message", [
    ("Nummer;Nachname;Forname;Vorname\n3;New;Jane;Janet\n", "widersprechen"),
    ("Nummer;Nachname;Vorname;Aktiv\n3;New;Jane\n", "zu wenige Felder"),
    ("Nummer;Nachname;Vorname\n3;New;Jane;extra\n", "mehr Werte"),
    ("Nummer;Nachname;Vorname\n1_0;New;Jane\n", "Ganzzahl"),
    ("Nummer;Nachname;Vorname\n+3;New;Jane\n", "Ganzzahl"),
    ("Nummer;Nachname;Vorname\n0;New;Jane\n", "Ganzzahl"),
    ("Nummer;Nachname;Vorname\n3;New;\n", "nicht leer"),
    ("Nummer;Nachname;Vorname;Aktiv\n3;New;Jane;maybe\n", "Aktiv"),
    ("Nummer;Nachname;Vorname; vorname \n3;New;Jane;Jane\n", "doppelte"),
    ('Nummer;Nachname;"Vorname\n', "Kopfzeile"),
])
def test_invalid_import_does_not_change_files_or_matcher(tmp_path, contents, message):
    admin = service(tmp_path)
    original = admin.registry.csv_path.read_bytes()
    students = admin.registry.students
    source = tmp_path / "invalid.csv"
    source.write_text(contents, encoding="utf-8")

    with pytest.raises(RegistryValidationError, match=message):
        admin.preview_import(source)

    assert admin.registry.csv_path.read_bytes() == original
    assert admin.registry.students == students
    assert not list(tmp_path.glob("*.bak*"))


def test_import_accepts_consistent_dual_headers_and_quoted_delimiters(tmp_path):
    admin = service(tmp_path)
    source = tmp_path / "monthly.csv"
    source.write_text(
        ' nummer ; Nachname ; Forname ; Vorname \n3;"New; Family"; ÉVA ; E\u0301va \n',
        encoding="utf-8-sig",
    )
    preview = admin.preview_import(source)
    assert preview.new[0].last_name == "New; Family"
    assert preview.new[0].first_name == "Éva"
    admin.apply_import(preview, confirmed=True)
    assert StudentRegistry(admin.registry.csv_path).match_parts("New; Family", "Éva").student.number == 3


@pytest.mark.parametrize("change", ["alias_added", "alias_removed", "external_csv", "external_aliases"])
def test_import_rejects_any_registry_change_after_preview(tmp_path, change):
    admin = service(tmp_path)
    admin.registry.confirm_existing("D0e Jane", 1)
    source = tmp_path / "monthly.csv"
    source.write_text("Nummer;Nachname;Vorname\n3;New;Student\n", encoding="utf-8")
    preview = admin.preview_import(source)
    if change == "alias_added":
        admin.registry.confirm_existing("Doe Jne", 1)
    elif change == "alias_removed":
        admin.remove_alias("D0e Jane")
    elif change == "external_csv":
        admin.registry.csv_path.write_bytes(admin.registry.csv_path.read_bytes() + b"\n")
    else:
        admin.registry.aliases_path.write_bytes(admin.registry.aliases_path.read_bytes() + b"\n")
    original_csv = admin.registry.csv_path.read_bytes()
    original_aliases = admin.registry.aliases_path.read_bytes()

    with pytest.raises(RegistryValidationError, match="geändert"):
        admin.apply_import(preview, confirmed=True)

    assert admin.registry.csv_path.read_bytes() == original_csv
    assert admin.registry.aliases_path.read_bytes() == original_aliases
    assert not list(tmp_path.glob("*.bak*"))


def test_external_edit_before_preview_requires_reload_and_is_preserved(tmp_path):
    admin = service(tmp_path)
    # Include an extra column, which must not disappear when new rows are merged.
    external = "Nummer;Nachname;Vorname;Note\n1;Doe;Janet;keep this\n"
    admin.registry.csv_path.write_text(external, encoding="utf-8")
    source = tmp_path / "monthly.csv"
    source.write_text("Nummer;Nachname;Vorname\n3;New;Student\n", encoding="utf-8")

    with pytest.raises(RegistryValidationError, match="Aktualisieren"):
        admin.preview_import(source)
    with pytest.raises(RegistryValidationError, match="Aktualisieren"):
        admin.registry.add_student("Another", "Person")

    assert admin.registry.reload()
    admin.apply_import(admin.preview_import(source), confirmed=True)
    assert admin.registry.match("Doe Janet").status == MatchStatus.EXACT
    assert "keep this" in admin.registry.csv_path.read_text(encoding="utf-8")
    assert (tmp_path / "Schuler.csv.bak").read_text(encoding="utf-8") == external


def test_mixed_import_adds_only_safe_rows_and_preserves_conflicts_and_history(tmp_path):
    admin = service(tmp_path)
    source = tmp_path / "monthly.csv"
    source.write_text(
        "Nummer;Nachname;Vorname\n1;Changed;Jane\n3;New;Student\n4;Same;Name\n5;Same;Name\n",
        encoding="utf-8",
    )
    result = admin.apply_import(admin.preview_import(source), confirmed=True)
    assert [s.number for s in result.added] == [3]
    assert result.skipped_conflicts == 1 and result.skipped_duplicates == 2
    assert admin.registry.select_existing(1).full_name == "Doe Jane"
    assert admin.registry.select_existing(2).active is False
    assert len(admin.registry.students) == 3


def test_backup_failure_aborts_import_and_repeated_import_preserves_backups(tmp_path, monkeypatch):
    admin = service(tmp_path)
    source = tmp_path / "monthly.csv"
    source.write_text("Nummer;Nachname;Vorname\n3;New;Student\n", encoding="utf-8")
    preview = admin.preview_import(source)
    original = admin.registry.csv_path.read_bytes()
    real_replace = registry_module.os.replace

    def fail_backup(src, dest):
        if ".bak" in Path(dest).name:
            raise OSError("backup failed")
        return real_replace(src, dest)

    with monkeypatch.context() as patch:
        patch.setattr(registry_module.os, "replace", fail_backup)
        with pytest.raises(registry_module.RegistryWriteError, match="backup failed"):
            admin.apply_import(preview, confirmed=True)
    assert admin.registry.csv_path.read_bytes() == original
    assert len(admin.registry.students) == 2
    assert not list(tmp_path.glob(".*.tmp"))

    first = admin.apply_import(preview, confirmed=True)
    second_original = admin.registry.csv_path.read_bytes()
    source.write_text("Nummer;Nachname;Vorname\n4;Another;Person\n", encoding="utf-8")
    second = admin.apply_import(admin.preview_import(source), confirmed=True)
    assert first.backup_path != second.backup_path
    assert first.backup_path.read_bytes() == original
    assert second.backup_path.read_bytes() == second_original


def test_existing_pipeline_uses_admin_add_edit_and_import_without_restart(tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace

    from form_parser import FormParseResult, FormType
    from processing_service import DocumentProcessingService
    from review_queue import ReviewStore
    from validator import Validator

    admin = service(tmp_path)
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"synthetic")
    parsed = FormParseResult(
        form_type=FormType.LEGACY,
        raw_student_name="New Student", student_name="New Student",
        raw_subject="Mathematik", subject="Mathematik", confidence=0.95,
        reasons=("synthetic parser evidence",),
    )
    pipeline = DocumentProcessingService(
        extractor=SimpleNamespace(extract_page_data=lambda path: SimpleNamespace(text="synthetic")),
        parser=SimpleNamespace(parse=lambda page: parsed),
        student_registry=admin.registry,
        validator=Validator(),
        review_store=ReviewStore(tmp_path / "manual_review.json"),
    )
    assert pipeline.process_document(pdf, 4, 2040, dry_run=True).requires_review
    student = admin.registry.add_student("New", "Student")
    assert pipeline.process_document(pdf, 4, 2040, dry_run=True).succeeded
    admin.registry.confirm_existing("New Student OCR", student.number)
    admin.registry.update_student(student.number, last_name="Changed")
    parsed = replace(parsed, raw_student_name="New Student OCR", student_name="New Student OCR")
    result = pipeline.process_document(pdf, 4, 2040, dry_run=True)
    assert result.succeeded
    assert result.planned_name == "Changed_Student_Mathematik_April_2040.pdf"
    source = tmp_path / "monthly.csv"
    source.write_text("Nummer;Nachname;Vorname\n9;Imported;Person\n", encoding="utf-8")
    admin.apply_import(admin.preview_import(source), confirmed=True)
    parsed = replace(parsed, raw_student_name="Imported Person", student_name="Imported Person")
    assert pipeline.process_document(pdf, 4, 2040, dry_run=True).succeeded
    assert pdf.read_bytes() == b"synthetic"
