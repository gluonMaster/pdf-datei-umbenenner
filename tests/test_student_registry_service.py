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
@pytest.mark.parametrize("delimiter", [";", ","])
def test_import_accepts_headers_bom_delimiters_and_trims_names(
    tmp_path, first_header, delimiter
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
        encoding="utf-8-sig",
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
