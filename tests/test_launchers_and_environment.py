"""Health helper and static launcher safety tests."""

import json
from pathlib import Path

import bootstrap
import ocr
from ocr import OCRAvailability


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BAT_FILES = (
    "setup.bat",
    "start_app.bat",
    "quick_start.bat",
    "create_desktop_shortcut.bat",
)


def _write_health_data(root):
    (root / "subjects_config.json").write_text(
        json.dumps(
            {
                "subjects": ["Mathematik"],
                "aliases": {"Mathe": "Mathematik"},
            }
        ),
        encoding="utf-8",
    )
    (root / "Schuler.csv").write_text(
        "Nummer;Nachname;Forname\n1;Sample;Student\n",
        encoding="utf-8",
    )


def test_python_minimum_has_a_stable_exit_code(capsys):
    assert bootstrap.check_python_version((3, 9, 9)) == bootstrap.ExitCode.UNSUPPORTED_PYTHON
    assert "Python 3.10 oder neuer" in capsys.readouterr().out
    assert bootstrap.check_python_version((3, 10, 0), emit=False) == bootstrap.ExitCode.OK


def test_core_health_reads_configuration_and_registry(tmp_path):
    _write_health_data(tmp_path)

    assert bootstrap.check_core(tmp_path) == bootstrap.ExitCode.OK


def test_core_health_reports_invalid_configuration(tmp_path, capsys):
    _write_health_data(tmp_path)
    (tmp_path / "subjects_config.json").write_text("[]", encoding="utf-8")

    assert bootstrap.check_core(tmp_path) == bootstrap.ExitCode.CONFIG_UNREADABLE
    assert "CONFIG_UNREADABLE" in capsys.readouterr().out


def test_ocr_health_uses_structured_unavailability_reasons(monkeypatch, capsys):
    class Provider:
        def __init__(self, availability):
            self.availability = availability

        def check_availability(self):
            return self.availability

    monkeypatch.setattr(bootstrap, "_load_pymupdf", lambda: object())
    cases = (
        (
            OCRAvailability(False, "Tesseract wurde nicht gefunden."),
            bootstrap.ExitCode.TESSERACT_UNAVAILABLE,
            "TESSERACT_UNAVAILABLE",
        ),
        (
            OCRAvailability(
                False,
                "Tesseract-Sprachdaten fehlen: deu.",
                Path("tesseract.exe"),
                ("eng",),
                ("deu",),
            ),
            bootstrap.ExitCode.OCR_LANGUAGE_DATA_UNAVAILABLE,
            "OCR_LANGUAGE_DATA_UNAVAILABLE",
        ),
        (
            OCRAvailability(
                False,
                "Tesseract konnte nicht abgefragt werden.",
                Path("tesseract.exe"),
            ),
            bootstrap.ExitCode.OCR_CHECK_FAILED,
            "OCR_CHECK_FAILED",
        ),
    )

    for availability, expected_code, expected_category in cases:
        monkeypatch.setattr(
            ocr,
            "TesseractOCRProvider",
            lambda current=availability: Provider(current),
        )
        assert bootstrap.check_ocr() == expected_code
        assert expected_category in capsys.readouterr().out


def test_ocr_health_distinguishes_pymupdf_failure_classes(monkeypatch, capsys):
    cases = (
        (
            ModuleNotFoundError("PyMuPDF ist nicht installiert"),
            bootstrap.ExitCode.OCR_PYTHON_DEPENDENCY_UNAVAILABLE,
            "OCR_PYTHON_DEPENDENCY_UNAVAILABLE",
        ),
        (
            bootstrap.PyMuPDFImportFailure(
                "DLL load failed while importing _extra", runtime_related=True
            ),
            bootstrap.ExitCode.PYMUPDF_RUNTIME_UNAVAILABLE,
            "PYMUPDF_RUNTIME_UNAVAILABLE",
        ),
        (
            bootstrap.PyMuPDFImportFailure(
                "unerwarteter Importfehler", runtime_related=False
            ),
            bootstrap.ExitCode.PYMUPDF_IMPORT_FAILED,
            "PYMUPDF_IMPORT_FAILED",
        ),
    )

    for error, expected_code, expected_category in cases:
        def fail_import(current=error):
            raise current

        monkeypatch.setattr(bootstrap, "_load_pymupdf", fail_import)
        assert bootstrap.check_ocr() == expected_code
        assert expected_category in capsys.readouterr().out


def test_pymupdf_loader_classifies_windows_runtime_errors(monkeypatch):
    monkeypatch.setattr(
        bootstrap.importlib.util,
        "find_spec",
        lambda name: object() if name == "pymupdf" else None,
    )

    def fail_import(name):
        raise ImportError("DLL load failed while importing _extra: MSVCP140.dll")

    monkeypatch.setattr(bootstrap.importlib, "import_module", fail_import)

    try:
        bootstrap._load_pymupdf()
    except bootstrap.PyMuPDFImportFailure as error:
        assert error.runtime_related is True
    else:
        raise AssertionError("A broken PyMuPDF import must fail the health check")


def test_requirements_stamp_detects_changes(tmp_path):
    (tmp_path / "requirements.txt").write_text("example>=1\n", encoding="ascii")

    assert (
        bootstrap.requirements_current(tmp_path)
        == bootstrap.ExitCode.REQUIREMENTS_CHANGED
    )
    assert bootstrap.write_requirements_stamp(tmp_path) == bootstrap.ExitCode.OK
    assert bootstrap.requirements_current(tmp_path) == bootstrap.ExitCode.OK

    (tmp_path / "requirements.txt").write_text("example>=2\n", encoding="ascii")
    assert (
        bootstrap.requirements_current(tmp_path)
        == bootstrap.ExitCode.REQUIREMENTS_CHANGED
    )


def test_missing_requirements_is_a_bootstrap_error(tmp_path, capsys):
    assert bootstrap.requirements_current(tmp_path) == bootstrap.ExitCode.BOOTSTRAP_ERROR
    assert "requirements.txt fehlt" in capsys.readouterr().out


def test_batch_files_are_ascii_and_use_the_local_environment():
    for name in BAT_FILES:
        raw = (PROJECT_ROOT / name).read_bytes()
        assert all(byte < 128 for byte in raw), name
        text = raw.decode("ascii").casefold()
        if name != "create_desktop_shortcut.bat":
            assert 'cd /d "%project_dir%"' in text

    setup = (PROJECT_ROOT / "setup.bat").read_text(encoding="ascii").casefold()
    smart = (PROJECT_ROOT / "start_app.bat").read_text(encoding="ascii").casefold()
    shortcut = (PROJECT_ROOT / "create_desktop_shortcut.bat").read_text(
        encoding="ascii"
    ).casefold()

    assert '"%venv_python%" -m pip install' in setup
    assert "--unattended" in setup
    assert "--core-only" in setup
    assert "--force-reinstall --no-cache-dir" in setup
    assert "install_vc_runtime.ps1" in setup
    assert 'if "%ocr_required%"=="1"' in setup
    assert '"%venv_python%" -m pip install' in smart
    assert "requirements-current" in smart
    assert "--health-only" in smart
    assert smart.index("requirements-current") < smart.index(":repair_environment")
    assert "%~dp0start_app.bat" in shortcut


def test_quick_start_never_installs_or_repairs_packages():
    quick = (PROJECT_ROOT / "quick_start.bat").read_text(encoding="ascii").casefold()

    assert " pip " not in quick
    assert "requirements" not in quick
    assert "setup.bat" not in quick
    assert '"%venv_python%" "%project_dir%start_gui.py"' in quick


def test_dependency_groups_are_separate():
    core = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
    ocr = (PROJECT_ROOT / "requirements-ocr.txt").read_text(encoding="utf-8")
    dev = (PROJECT_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")

    assert "pdfplumber" in core
    assert "PyInstaller" not in core
    assert "six>=" not in core
    assert "PyMuPDF" not in core
    assert "PyMuPDF" in ocr
    assert "PyMuPDF>=1.27,<1.29" in ocr
    assert "pytest" in dev


def test_vc_runtime_installer_verifies_publisher_before_execution():
    script = (PROJECT_ROOT / "install_vc_runtime.ps1").read_text(encoding="utf-8")

    assert "https://aka.ms/vc14/vc_redist.x64.exe" in script
    assert "Get-AuthenticodeSignature" in script
    assert "CN=Microsoft Corporation" in script
    assert script.index("Get-AuthenticodeSignature") < script.index("Start-Process")
    assert '"/quiet", "/norestart"' in script
    assert "WindowsBuiltInRole" in script
    assert "Invoke-Expression" not in script
