import csv
import json
from pathlib import Path

import pytest

import student_registry as registry_module
from student_registry import (
    MatchStatus,
    RegistryValidationError,
    RegistryWriteError,
    StudentRegistry,
    normalize_name,
)


def _write_csv(path: Path, rows, *, first_header="Forname", active=False, bom=True):
    headers = ["Nummer", "Nachname", first_header]
    if active:
        headers.append("Aktiv")
    lines = [";".join(headers)]
    lines.extend(";".join(str(value) for value in row) for row in rows)
    encoding = "utf-8-sig" if bom else "utf-8"
    path.write_text("\n".join(lines) + "\n", encoding=encoding)


@pytest.mark.parametrize("first_header", ["Forname", "Vorname"])
@pytest.mark.parametrize("bom", [False, True])
def test_reads_supported_headers_and_utf8_variants(tmp_path, first_header, bom):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(1, "  Müller  ", "  Éva  ")], first_header=first_header, bom=bom)

    registry = StudentRegistry(csv_path)
    match = registry.match("  éVA    müLLER ")

    assert registry.is_available
    assert match.status == MatchStatus.EXACT
    assert match.student.number == 1
    assert match.student.last_name == "Müller"
    assert match.student.first_name == "Éva"


def test_normalization_keeps_diacritics_hyphens_and_apostrophes_significant(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(
        csv_path,
        [
            (1, "Müller-Lüdenscheidt", "Zoë"),
            (2, "O’Neil", "Anne-Marie"),
        ],
    )
    registry = StudentRegistry(csv_path)

    assert registry.match("Zoë Müller-Lüdenscheidt").status == MatchStatus.EXACT
    assert registry.match("Anne-Marie O’Neil").status == MatchStatus.EXACT
    assert registry.match("Zoe Muller-Ludenscheidt").status != MatchStatus.EXACT
    assert registry.match("Anne Marie O'Neil").status != MatchStatus.EXACT
    assert normalize_name("  O’NEIL  Anne-Marie ") == "o’neil anne-marie"


def test_inactive_students_remain_matchable(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(7, "Archiv", "Ada", "0")], first_header="Vorname", active=True)

    match = StudentRegistry(csv_path).match("Archiv Ada")

    assert match.status == "exact"
    assert match.student.active is False


def test_duplicate_canonical_names_are_ambiguous_not_silently_selected(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(2, "Same", "Name"), (9, "Same", "Name")])

    match = StudentRegistry(csv_path).match("Same Name")

    assert match.status == MatchStatus.AMBIGUOUS
    assert match.student is None
    assert [item.number for item in match.suggestions] == [2, 9]
    assert match.requires_manual_review


def test_fuzzy_match_only_suggests_and_can_report_ambiguity(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(
        csv_path,
        [
            (1, "Musterko", "Dmytro"),
            (2, "Musterko", "Mykhailo"),
            (3, "Other", "Person"),
        ],
    )
    registry = StudentRegistry(csv_path, ambiguity_threshold=0.70, ambiguity_delta=0.20)

    match = registry.match("Musterko Mikhail")

    assert match.status in {MatchStatus.UNKNOWN, MatchStatus.AMBIGUOUS}
    assert match.student is None
    assert match.suggestions
    assert match.ranked_suggestions[0].score >= match.ranked_suggestions[-1].score
    assert not csv_path.read_text(encoding="utf-8-sig").endswith("Mikhail\n")


@pytest.mark.parametrize(
    "contents",
    [
        "Nummer,Nachname,Vorname\n1,Doe,Jane\n",
        "Nummer;Nachname;Vorname\nnope;Doe;Jane\n",
        "Nummer;Nachname;Vorname\n1;Doe\n",
        'Nummer;Nachname;Vorname\n1;"unclosed;Jane\n',
        "Nummer;Nachname;Vorname\n1;Doe;Jane\n1;Roe;Janet\n",
    ],
)
def test_corrupt_registry_returns_diagnostic_instead_of_crashing(tmp_path, contents):
    csv_path = tmp_path / "Schuler.csv"
    csv_path.write_text(contents, encoding="utf-8")

    registry = StudentRegistry(csv_path)
    match = registry.match("Doe Jane")

    assert not registry.is_available
    assert match.status == MatchStatus.ERROR
    assert match.student is None
    assert "manuell geprüft" in match.reason.casefold()


def test_missing_registry_and_bad_alias_file_return_error_match(tmp_path):
    missing = StudentRegistry(tmp_path / "missing.csv")
    assert missing.match("Doe Jane").status == MatchStatus.ERROR

    csv_path = tmp_path / "Schuler.csv"
    aliases_path = tmp_path / "aliases.json"
    _write_csv(csv_path, [(1, "Doe", "Jane")])
    aliases_path.write_text("{not json", encoding="utf-8")
    damaged_aliases = StudentRegistry(csv_path, aliases_path)
    assert damaged_aliases.match("Doe Jane").status == MatchStatus.ERROR


def test_empty_lines_are_ignored(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    csv_path.write_text(
        "Nummer;Nachname;Vorname\n\n1;Doe;Jane\n;;\n\n",
        encoding="utf-8-sig",
    )

    registry = StudentRegistry(csv_path)

    assert len(registry.students) == 1
    assert registry.match("Doe Jane").status == MatchStatus.EXACT


def test_unknown_requires_explicit_confirmation_and_alias_persists(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    aliases_path = tmp_path / "student_aliases.json"
    _write_csv(csv_path, [(4, "Musterkowa", "Daria")])
    original_csv = csv_path.read_bytes()
    registry = StudentRegistry(csv_path, aliases_path)

    unknown = registry.match("Musterkova Daria")
    assert unknown.status == MatchStatus.UNKNOWN
    assert csv_path.read_bytes() == original_csv
    assert not aliases_path.exists()

    selected = registry.confirm_existing("Musterkova Daria", 4)
    repeated = StudentRegistry(csv_path, aliases_path).match("musterkova   DARIA")

    assert selected.number == 4
    assert repeated.status == MatchStatus.EXACT
    assert repeated.student.full_name == "Musterkowa Daria"
    payload = json.loads(aliases_path.read_text(encoding="utf-8"))
    assert payload["aliases"] == [
        {"name": "Musterkova Daria", "student_number": 4}
    ]


def test_legacy_alias_mapping_is_read_for_backward_compatibility(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    aliases_path = tmp_path / "student_aliases.json"
    _write_csv(csv_path, [(12, "Canonical", "Name")])
    aliases_path.write_text(
        json.dumps({"aliases": {"OCR Name": 12}}),
        encoding="utf-8",
    )

    match = StudentRegistry(csv_path, aliases_path).match("ocr name")

    assert match.status == MatchStatus.EXACT
    assert match.student.number == 12


def test_add_uses_next_number_and_edit_preserves_it(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(2, "Doe", "Jane"), (8, "Roe", "John")])
    registry = StudentRegistry(csv_path)

    added = registry.confirm_new("Public", "Ada")
    edited = registry.update_student(added.number, first_name="Augusta")
    reloaded = StudentRegistry(csv_path)

    assert added.number == 9
    assert edited.number == 9
    assert reloaded.match("Public Augusta").student.number == 9
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert csv_path.read_text(encoding="utf-8-sig").splitlines()[0] == (
        "Nummer;Nachname;Forname"
    )


def test_inactive_write_migrates_schema_only_after_backup(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(1, "Doe", "Jane")])
    original = csv_path.read_bytes()
    registry = StudentRegistry(csv_path)

    student = registry.add_student("Archive", "Alex", active=False)
    backups = list(tmp_path.glob("Schuler.csv.bak*"))

    assert student.active is False
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert csv_path.read_text(encoding="utf-8-sig").splitlines()[0].endswith(";Aktiv")
    assert StudentRegistry(csv_path).match("Archive Alex").student.active is False


def test_validation_failure_does_not_change_files(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    aliases_path = tmp_path / "student_aliases.json"
    _write_csv(csv_path, [(1, "Doe", "Jane"), (2, "Roe", "John")])
    aliases_path.write_text(
        json.dumps({"version": 1, "aliases": {"OCR Jane": 1}}),
        encoding="utf-8",
    )
    registry = StudentRegistry(csv_path, aliases_path)
    original_csv = csv_path.read_bytes()
    original_aliases = aliases_path.read_bytes()

    with pytest.raises(RegistryValidationError):
        registry.update_student(2, last_name="Doe", first_name="Jane")
    with pytest.raises(RegistryValidationError):
        registry.confirm_existing("OCR Jane", 2)

    assert csv_path.read_bytes() == original_csv
    assert aliases_path.read_bytes() == original_aliases


def test_atomic_replace_failure_keeps_original_and_cleans_temporary_file(
    tmp_path, monkeypatch
):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(1, "Doe", "Jane")])
    registry = StudentRegistry(csv_path)
    original = csv_path.read_bytes()

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(registry_module.os, "replace", fail_replace)
    with pytest.raises(RegistryWriteError):
        registry.add_student("Roe", "John")

    assert csv_path.read_bytes() == original
    assert [student.number for student in registry.students] == [1]
    assert list(tmp_path.glob(".Schuler.csv.*.tmp")) == []


def test_alias_replace_failure_keeps_original_json(tmp_path, monkeypatch):
    csv_path = tmp_path / "Schuler.csv"
    aliases_path = tmp_path / "student_aliases.json"
    _write_csv(csv_path, [(1, "Doe", "Jane")])
    aliases_path.write_text(
        json.dumps({"version": 1, "aliases": []}) + "\n",
        encoding="utf-8",
    )
    registry = StudentRegistry(csv_path, aliases_path)
    original = aliases_path.read_bytes()

    def fail_replace(source, destination):
        raise OSError("simulated alias replace failure")

    monkeypatch.setattr(registry_module.os, "replace", fail_replace)
    with pytest.raises(RegistryWriteError):
        registry.confirm_existing("D0e Jane", 1)

    assert aliases_path.read_bytes() == original
    assert registry.match("D0e Jane").status != MatchStatus.EXACT
    assert list(tmp_path.glob(".student_aliases.json.*.tmp")) == []


def test_lehrer_csv_is_never_consulted(tmp_path):
    students_path = tmp_path / "Schuler.csv"
    teachers_path = tmp_path / "Lehrer.csv"
    _write_csv(students_path, [(1, "Student", "Ada")])
    _write_csv(teachers_path, [(99, "Teacher", "Theo")])

    registry = StudentRegistry(students_path)

    assert registry.match("Student Ada").status == MatchStatus.EXACT
    assert registry.match("Teacher Theo").status != MatchStatus.EXACT


def test_header_migration_creates_backup_and_keeps_data(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(1, "Doe", "Jane")])
    original = csv_path.read_bytes()
    registry = StudentRegistry(csv_path)

    backup = registry.migrate_to_vorname_header()

    assert backup is not None
    assert backup.read_bytes() == original
    assert csv_path.read_text(encoding="utf-8-sig").splitlines()[0] == (
        "Nummer;Nachname;Vorname"
    )
    assert StudentRegistry(csv_path).match("Doe Jane").status == MatchStatus.EXACT


@pytest.mark.parametrize("operation", ["add", "edit", "merge"])
def test_canonical_write_cannot_conflict_with_confirmed_alias(tmp_path, operation):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(1, "Canonical", "Jane"), (2, "Other", "Ada")])
    registry = StudentRegistry(csv_path)
    registry.confirm_existing("OCR Jane", 1)
    original = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    with pytest.raises(RegistryValidationError, match="Alias"):
        if operation == "add":
            registry.confirm_new("OCR", "Jane", active=False)
        elif operation == "edit":
            registry.update_student(2, last_name="Jane", first_name="OCR", active=False)
        else:
            registry.merge_students([registry_module.Student(3, "OCR", "Jane", False)])

    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == original
    assert registry.match("OCR Jane").student.number == 1
    assert StudentRegistry(csv_path).is_available


@pytest.mark.parametrize("bad_number", [True, False, 1.0, 1.9, float("inf"), None, "1.5", 0, -1])
def test_confirmation_rejects_invalid_student_number_without_writing(tmp_path, bad_number):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(1, "Doe", "Jane")])
    original = csv_path.read_bytes()
    registry = StudentRegistry(csv_path)

    with pytest.raises(RegistryValidationError):
        registry.confirm_existing("D0e Jane", bad_number)

    assert csv_path.read_bytes() == original
    assert not registry.aliases_path.exists()
    assert registry.select_existing(" 1 ").number == 1


@pytest.mark.parametrize("payload", [
    '{"aliases": [{"name": "OCR Name", "student_number": true}]}',
    '{"aliases": [{"name": "OCR Name", "student_number": 1.9}]}',
    '{"aliases": [{"name": "OCR Name", "student_number": 1e999}]}',
    '{"aliases": {"OCR Name": 1, "OCR Name": 2}}',
    '{"aliases": {}, "aliases": []}',
    '{"version": true, "aliases": []}',
    '{"version": 1}',
    '{"aliases": {"OCR Name": 99}}',
    '{"aliases": {"OCR Name": 1, " ocr  name ": 2}}',
    '{"aliases": {"Doe Jane": 2}}',
])
def test_damaged_alias_data_requires_review_and_is_not_rewritten(tmp_path, payload):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(1, "Doe", "Jane"), (2, "Roe", "Ada")])
    aliases_path = tmp_path / "student_aliases.json"
    aliases_path.write_text(payload, encoding="utf-8")

    registry = StudentRegistry(csv_path)

    assert registry.match("Doe Jane").status == MatchStatus.ERROR
    assert registry.match_parts("Doe", "Jane").requires_manual_review
    assert registry.match_parts("", "").status == MatchStatus.ERROR
    assert aliases_path.read_text(encoding="utf-8") == payload


def test_match_parts_preserves_field_boundaries_and_raw_name_handles_both_orders(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(1, "Martin", "Thomas"), (2, "Thomas", "Martin"),
                          (3, "Van Sample", "Ada"), (4, "Van", "Sample Ada")])
    registry = StudentRegistry(csv_path)

    assert registry.match("Martin Thomas").status == MatchStatus.AMBIGUOUS
    assert registry.match_parts(" MARTIN ", " thomas ").student.number == 1
    assert registry.match_parts("Thomas", "Martin").student.number == 2
    assert registry.match_parts("Van Sample", "Ada").student.number == 3
    assert registry.match_parts("Van", "Sample Ada").student.number == 4
    assert registry.match_parts("Ada", "Van Sample").status != MatchStatus.EXACT


def test_fuzzy_ambiguity_does_not_depend_on_display_limit(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(2, "Sample", "Anna"), (1, "Sample", "Anne")])
    registry = StudentRegistry(csv_path, suggestion_limit=1)

    match = registry.match("Sample Anni")

    assert match.status == MatchStatus.AMBIGUOUS
    assert match.student is None
    assert [item.number for item in match.suggestions] == [1]


def test_unicode_equivalence_and_confirmed_alias_survive_edit_and_reload(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(4, "Mu\u0308ller", "E\u0301va")])
    registry = StudentRegistry(csv_path)
    original_csv = csv_path.read_bytes()
    assert registry.match("Éva MÜLLER").student.number == 4
    registry.confirm_existing("Mueller Eva", 4, save_alias=False)
    assert not registry.aliases_path.exists()
    registry.confirm_existing("Mueller Eva", 4)
    original_aliases = registry.aliases_path.read_bytes()
    registry.confirm_existing("  MUELLER  eva ", 4)
    assert registry.aliases_path.read_bytes() == original_aliases
    assert csv_path.read_bytes() == original_csv

    registry.update_student(4, last_name="Neu-Müller", active=False)

    match = StudentRegistry(csv_path).match("Mueller Eva")
    assert match.student.number == 4
    assert match.student.full_name == "Neu-Müller Éva"
    assert match.student.active is False


def test_unknown_and_noop_edits_do_not_rewrite_csv_or_aliases(tmp_path, monkeypatch):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(1, " Same ", " Name "), (2, "Same", "Name")])
    registry = StudentRegistry(csv_path)

    def unexpected_write(*args, **kwargs):
        pytest.fail("Read-only or unchanged operation attempted a write")

    monkeypatch.setattr(registry, "_atomic_write_bytes", unexpected_write)
    assert registry.match("Unknown Person").student is None
    assert registry.select_existing(1).number == 1
    assert registry.update_student(1).number == 1
    assert registry.update_student(1, last_name=" Same ", active=True).number == 1


def test_duplicate_names_can_be_marked_inactive_without_deletion(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(1, "Same", "Name"), (2, "Same", "Name")])
    registry = StudentRegistry(csv_path)

    registry.update_student(2, active=False)

    match = StudentRegistry(csv_path).match("Same Name")
    assert match.status == MatchStatus.AMBIGUOUS
    assert [item.active for item in match.suggestions] == [True, False]


@pytest.mark.parametrize("bom", [True, False])
def test_csv_writes_preserve_extra_columns_header_order_and_encoding(tmp_path, bom):
    csv_path = tmp_path / "Schuler.csv"
    csv_path.write_text(
        'Notiz;Forname;Nummer;Nachname\n"keep; this";Jane;5;Doe\n',
        encoding="utf-8-sig" if bom else "utf-8",
    )
    registry = StudentRegistry(csv_path)
    registry.update_student(5, first_name="Janet")
    registry.confirm_new("New", "Ada", active=False)
    before_migration = csv_path.read_bytes()

    backup = registry.migrate_to_vorname_header()

    assert backup.read_bytes() == before_migration
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf") == bom
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        rows = list(reader)
    assert reader.fieldnames == ["Notiz", "Vorname", "Nummer", "Nachname", "Aktiv"]
    assert rows[0] == {"Notiz": "keep; this", "Vorname": "Janet", "Nummer": "5",
                       "Nachname": "Doe", "Aktiv": "1"}
    assert rows[1]["Notiz"] == ""
    assert StudentRegistry(csv_path).match("New Ada").student.number == 6


@pytest.mark.parametrize("contents", [
    b"", b"\xff\xfeinvalid", b"Nummer;Nachname;Vorname\n",
    b"Nummer;Nachname;Vorname;Aktiv\n1;Doe;Jane\n",
    b"Nummer;Nachname;Vorname;Aktiv\n1;Doe;Jane;maybe\n",
    b"Nummer;Nachname;Vorname\n1;Doe;Jane;extra\n",
    b"Nummer;Nachname;Vorname\n1;D\x00oe;Jane\n",
    b"Nummer;Nachname;Vorname;Forname\n1;Doe;Jane;Janet\n",
    b"Nummer;Nachname;Vorname;Vorname\n1;Doe;Jane;Jane\n",
    b"Nummer;Nachname;Vorname;\n1;Doe;Jane;note\n",
])
def test_malformed_csv_returns_error_without_mutating_file(tmp_path, contents):
    csv_path = tmp_path / "Schuler.csv"
    csv_path.write_bytes(contents)
    registry = StudentRegistry(csv_path)

    assert registry.match("Doe Jane").status == MatchStatus.ERROR
    with pytest.raises(registry_module.RegistryUnavailableError):
        registry.confirm_new("New", "Student")
    assert csv_path.read_bytes() == contents


def test_agreeing_first_name_headers_are_kept_in_sync(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    csv_path.write_text("Nummer;Nachname;Forname;Vorname\n1;Doe;Jane; JANE \n", encoding="utf-8")
    registry = StudentRegistry(csv_path)
    registry.update_student(1, first_name="Janet")

    assert StudentRegistry(csv_path).match("Doe Janet").status == MatchStatus.EXACT
    assert csv_path.read_text(encoding="utf-8").splitlines()[1] == "1;Doe;Janet;Janet"


def test_legacy_alias_migration_is_backed_up_once(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(1, "Doe", "Jane")])
    aliases_path = tmp_path / "student_aliases.json"
    aliases_path.write_text('{"aliases": {"OCR Jane": "1"}}', encoding="utf-8-sig")
    original = aliases_path.read_bytes()
    registry = StudentRegistry(csv_path)

    registry.confirm_existing("D0e Jane", 1)
    registry.confirm_existing("Doo Jane", 1)

    backups = list(tmp_path.glob("student_aliases.json.bak*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert StudentRegistry(csv_path).match("OCR Jane").student.number == 1


@pytest.mark.parametrize("failure_stage", ["mkstemp", "fsync"])
@pytest.mark.parametrize("target", ["csv", "aliases"])
def test_atomic_write_setup_or_flush_failure_is_controlled(tmp_path, monkeypatch, failure_stage, target):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(1, "Doe", "Jane")])
    registry = StudentRegistry(csv_path)
    original = csv_path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("simulated write failure")

    owner = registry_module.tempfile if failure_stage == "mkstemp" else registry_module.os
    monkeypatch.setattr(owner, failure_stage, fail)
    with pytest.raises(RegistryWriteError):
        if target == "csv":
            registry.confirm_new("New", "Ada")
        else:
            registry.confirm_existing("D0e Jane", 1)

    assert csv_path.read_bytes() == original
    assert len(registry.students) == 1
    assert not registry.aliases_path.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_backup_failure_prevents_schema_migration(tmp_path, monkeypatch):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(1, "Doe", "Jane")])
    registry = StudentRegistry(csv_path)
    original = csv_path.read_bytes()

    def fail_replace(*args):
        raise OSError("simulated backup failure")

    monkeypatch.setattr(registry_module.os, "replace", fail_replace)
    with pytest.raises(RegistryWriteError):
        registry.update_student(1, active=False)

    assert csv_path.read_bytes() == original
    assert registry.select_existing(1).active is True
    assert list(tmp_path.iterdir()) == [csv_path]


def test_bulk_write_always_creates_backup_and_normalizes_new_rows(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(1, "Doe", "Jane")])
    registry = StudentRegistry(csv_path)
    original = csv_path.read_bytes()

    backup = registry.merge_students([
        registry_module.Student(5, "  New  Name  ", " Ada "),
        registry_module.Student(7, "Other", "Janet"),
    ], create_backup=False)

    assert backup.read_bytes() == original
    assert registry.select_existing(5).full_name == "New Name Ada"
    assert registry.confirm_new("Next", "Student").number == 8


def test_failed_reload_discards_stale_matches_and_can_recover(tmp_path):
    csv_path = tmp_path / "Schuler.csv"
    _write_csv(csv_path, [(1, "Doe", "Jane")])
    registry = StudentRegistry(csv_path)
    original = csv_path.read_bytes()
    csv_path.write_bytes(b"invalid")

    assert registry.reload() is False
    assert registry.match("Doe Jane").status == MatchStatus.ERROR
    assert registry.students == ()
    csv_path.write_bytes(original)
    assert registry.reload() is True
    assert registry.match("Doe Jane").status == MatchStatus.EXACT
