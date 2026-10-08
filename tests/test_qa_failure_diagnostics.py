"""Failures at two filesystem steps must retain both causes and recovery paths."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import file_renamer as renamer_module
from alias_manager import AliasManager
from file_renamer import FileRenamer
from review_queue import ReviewStore
from student_registry import RegistryWriteError, StudentRegistry


@pytest.mark.parametrize("kind", ["aliases", "queue", "registry"])
def test_atomic_replace_and_cleanup_failure_preserve_original_and_both_errors(
    tmp_path, monkeypatch, capsys, kind
):
    target = tmp_path / ("Schuler.csv" if kind == "registry" else f"{kind}.json")
    original = {
        "aliases": b'{"aliases": {}}',
        "queue": b'{"review_items": []}',
        "registry": b"Nummer;Nachname;Vorname\n1;Sample;Student\n",
    }[kind]
    target.write_bytes(original)
    if kind == "aliases":
        store = AliasManager(target)
        store.add_alias("Matematik", "Mathematik")
    elif kind == "queue":
        store = ReviewStore(target)
        assert store.load()

    def fail_replace(*_args):
        raise OSError("primary replace failure")

    real_unlink = Path.unlink

    def fail_cleanup(path, *args, **kwargs):
        if path.suffix == ".tmp":
            raise PermissionError("secondary cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    if kind == "registry":
        with pytest.raises(RegistryWriteError) as failure:
            StudentRegistry._atomic_write_bytes(target, b"replacement")
        diagnostic = str(failure.value)
    else:
        assert store.save() is False
        diagnostic = capsys.readouterr().out

    remaining = list(tmp_path.glob("*.tmp"))
    assert len(remaining) == 1
    assert target.read_bytes() == original
    assert "primary replace failure" in diagnostic
    assert "secondary cleanup failure" in diagnostic
    assert str(remaining[0]) in diagnostic


@pytest.mark.parametrize("method", ["copy", "link"])
@pytest.mark.parametrize("operation", ["rename_file", "move_file"])
def test_move_and_rollback_failure_report_both_files_and_reset_error_after_retry(
    tmp_path, monkeypatch, method, operation
):
    source, target = tmp_path / "source.pdf", tmp_path / "target.pdf"
    source.write_bytes(b"unchanged PDF")
    renamer = FileRenamer()
    real_unlink = Path.unlink

    def fail_unlink(path, *args, **kwargs):
        if path == source:
            raise PermissionError("source unlink failure")
        if path == target:
            raise PermissionError("target cleanup failure")
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_unlink)
        if method == "copy":
            patch.setattr(FileRenamer, "_move_no_clobber", staticmethod(FileRenamer._copy_no_clobber))
        else:
            # Exercise the actual link/unlink fallback even on Windows without
            # changing os.name globally (which would alter pathlib itself).
            platform_os = SimpleNamespace(**vars(os))
            platform_os.name = "posix"
            patch.setattr(renamer_module, "os", platform_os)

        assert getattr(renamer, operation)(source, target) is False
        assert "source unlink failure" in renamer.last_error
        assert "target cleanup failure" in renamer.last_error
        assert str(source) in renamer.last_error and str(target) in renamer.last_error
        assert source.read_bytes() == target.read_bytes() == b"unchanged PDF"

    target.unlink()
    assert getattr(renamer, operation)(source, target) is True
    assert renamer.last_error is None
    assert not source.exists() and target.read_bytes() == b"unchanged PDF"
