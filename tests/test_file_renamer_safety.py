"""Focused safety tests for collision planning and file moves."""

import errno
import os
import stat
from pathlib import Path

import pytest

import file_renamer as file_renamer_module
from file_renamer import FileRenamer


@pytest.mark.parametrize("operation_name", ["rename_file", "move_file"])
def test_target_claimed_after_planning_is_never_overwritten_and_releases_reservation(
    tmp_path, monkeypatch, operation_name
):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    desired = tmp_path / "canonical.pdf"
    renamer = FileRenamer()
    planned = renamer.reserve_target_path(desired, source_path=source)
    claim_name = "rename" if os.name == "nt" else "link"
    real_claim = getattr(os, claim_name)

    def claim_target_then_move(move_source, move_target):
        Path(move_target).write_bytes(b"independent")
        return real_claim(move_source, move_target)

    monkeypatch.setattr(
        file_renamer_module.os,
        claim_name,
        claim_target_then_move,
    )

    assert not getattr(renamer, operation_name)(source, planned)
    assert source.read_bytes() == b"source"
    assert desired.read_bytes() == b"independent"

    # The failed operation must not leave an in-memory phantom collision.
    desired.unlink()
    assert renamer.reserve_target_path(desired, source_path=source) == desired


def test_cross_filesystem_fallback_uses_exclusive_destination(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    target = tmp_path / "nested" / "target.pdf"
    renamer = FileRenamer()
    planned = renamer.reserve_target_path(target, source_path=source)

    def cross_device_move(_source, _target):
        raise OSError(errno.EXDEV, "synthetic cross-device move")

    claim_name = "rename" if os.name == "nt" else "link"
    monkeypatch.setattr(file_renamer_module.os, claim_name, cross_device_move)

    assert renamer.move_file(source, planned)
    assert not source.exists()
    assert target.read_bytes() == b"source"


def test_case_only_rename_changes_the_actual_directory_entry(tmp_path):
    source = tmp_path / "canonical.pdf"
    target = tmp_path / "CANONICAL.pdf"
    source.write_bytes(b"source")
    if not target.exists():
        pytest.skip("case-only rename requires a case-insensitive filesystem")

    renamer = FileRenamer()
    planned = renamer.reserve_target_path(target, source_path=source)

    assert renamer.rename_file(source, planned)
    assert [item.name for item in tmp_path.iterdir()] == ["CANONICAL.pdf"]
    assert target.read_bytes() == b"source"


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only rename semantics")
def test_read_only_source_uses_native_atomic_rename(tmp_path):
    source = tmp_path / "source.pdf"
    target = tmp_path / "target.pdf"
    source.write_bytes(b"source")
    source.chmod(stat.S_IREAD)
    renamer = FileRenamer()
    planned = renamer.reserve_target_path(target, source_path=source)

    try:
        assert renamer.rename_file(source, planned)
        assert [item.name for item in tmp_path.iterdir()] == ["target.pdf"]
        assert target.read_bytes() == b"source"
    finally:
        if target.exists():
            target.chmod(stat.S_IWRITE)
        if source.exists():
            source.chmod(stat.S_IWRITE)


def test_dry_run_keeps_current_batch_reservation(tmp_path):
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    desired = tmp_path / "canonical.pdf"
    renamer = FileRenamer()

    first_target = renamer.reserve_target_path(desired, source_path=first)
    assert renamer.rename_file(first, first_target, dry_run=True)
    second_target = renamer.reserve_target_path(desired, source_path=second)

    assert first_target == desired
    assert second_target == tmp_path / "canonical_1.pdf"
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"


def test_generate_new_filename_requires_period_and_keeps_legacy_suffixing():
    renamer = FileRenamer()

    with pytest.raises(ValueError, match="ausdrücklich ausgewählt"):
        renamer.generate_new_filename("Sample", "Student", "Physik")
    with pytest.raises(ValueError, match="ausdrücklich ausgewählt"):
        renamer.generate_new_filename("Sample", "Student", "Physik", 4, None)

    first = renamer.generate_new_filename("Sample", "Student", "Physik", 4, 2030)
    second = renamer.generate_new_filename("Sample", "Student", "Physik", 4, 2030)

    assert first == "Sample_Student_Physik_April_2030.pdf"
    assert second == "Sample_Student_Physik_April_2030_1.pdf"


@pytest.mark.parametrize("month", [True, 0, 13, 1.0])
def test_build_filename_rejects_non_integer_or_out_of_range_month(month):
    with pytest.raises(ValueError, match="Monat"):
        FileRenamer().build_filename("Sample", "Student", "Physik", month, 2030)
