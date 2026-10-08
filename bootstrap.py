"""Environment health checks shared by the Windows launchers.

The file intentionally uses syntax supported by older Python versions so it can
print a useful error before the application (which requires Python 3.10) is
imported. It can create a local venv, but never installs system components.
Processing needs Tesseract with German language data, a separate system
install that only ``check-ocr`` verifies; the GUI starts without it.
"""

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
from enum import IntEnum
from pathlib import Path
import sys
import uuid
import venv


MINIMUM_PYTHON = (3, 10)
PROJECT_ROOT = Path(__file__).resolve().parent
# Mandatory packages from requirements.txt besides PyMuPDF, whose import
# failures are classified separately by _load_pymupdf().
CORE_DEPENDENCIES = ("pdfplumber", "PIL.Image")
CORE_APPLICATION_MODULES = (
    "pdf_extractor",
    "form_parser",
    "processing_service",
    "student_registry_service",
    "gui_main",
)
DEFAULT_REQUIREMENTS = "requirements.txt"
DEFAULT_STAMP = ".venv/.requirements.sha256"
# Official Windows installer linked from the Tesseract documentation
# (https://tesseract-ocr.github.io/tessdoc/Installation.html).
TESSERACT_DOWNLOAD_URL = "https://github.com/UB-Mannheim/tesseract/wiki"


class ExitCode(IntEnum):
    """Stable process exit codes consumed by the BAT launchers.

    30 is retired: it meant "optional OCR Python profile missing". PyMuPDF is
    a mandatory package now and reports 20/34/35; do not reuse 30.
    """

    OK = 0
    UNSUPPORTED_PYTHON = 10
    CORE_DEPENDENCY_UNAVAILABLE = 20
    TKINTER_UNAVAILABLE = 21
    APPLICATION_IMPORT_FAILED = 22
    CONFIG_UNREADABLE = 23
    REGISTRY_UNREADABLE = 24
    TESSERACT_UNAVAILABLE = 31
    OCR_LANGUAGE_DATA_UNAVAILABLE = 32
    OCR_CHECK_FAILED = 33
    PYMUPDF_RUNTIME_UNAVAILABLE = 34
    PYMUPDF_IMPORT_FAILED = 35
    REQUIREMENTS_CHANGED = 40
    BOOTSTRAP_ERROR = 50


class PyMuPDFImportFailure(ImportError):
    """Installed PyMuPDF cannot be imported by the current interpreter."""

    def __init__(self, detail, runtime_related=False):
        super().__init__(detail)
        self.runtime_related = bool(runtime_related)


def _version_text(version_info=None):
    version = version_info or sys.version_info
    return ".".join(str(part) for part in version[:3])


def check_python_version(version_info=None, emit=True):
    """Check the application minimum without importing application modules."""

    version = version_info or sys.version_info
    if tuple(version[:2]) < MINIMUM_PYTHON:
        if emit:
            print(
                "PYTHON_UNSUPPORTED: Python 3.10 oder neuer ist erforderlich; gefunden: "
                + _version_text(version)
                + "."
            )
        return ExitCode.UNSUPPORTED_PYTHON
    if emit:
        print("PYTHON_OK: Python " + _version_text(version) + " wird unterstuetzt.")
    return ExitCode.OK


def _import_modules(module_names, failure_code, category):
    for module_name in module_names:
        try:
            importlib.import_module(module_name)
        except Exception as error:
            detail = str(error).strip() or error.__class__.__name__
            print(
                category
                + ": Import fehlgeschlagen: "
                + module_name
                + " ("
                + detail
                + ")."
            )
            return failure_code
    return ExitCode.OK


def _check_subjects_config(project_root):
    config_path = project_root / "subjects_config.json"
    try:
        with config_path.open("r", encoding="utf-8-sig") as config_file:
            config = json.load(config_file)
        if not isinstance(config, dict):
            raise ValueError("JSON-Wurzel muss ein Objekt sein")
        subjects = config.get("subjects")
        aliases = config.get("aliases")
        if not isinstance(subjects, list) or not subjects:
            raise ValueError("'subjects' muss eine nichtleere Liste sein")
        if not all(isinstance(item, str) and item.strip() for item in subjects):
            raise ValueError("jedes Fach muss nichtleerer Text sein")
        if not isinstance(aliases, dict):
            raise ValueError("'aliases' muss ein Objekt sein")
        if not all(
            isinstance(alias, str)
            and alias.strip()
            and isinstance(subject, str)
            and subject.strip()
            for alias, subject in aliases.items()
        ):
            raise ValueError("Alias und Ziel muessen jeweils nichtleerer Text sein")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        detail = str(error).strip() or error.__class__.__name__
        print("CONFIG_UNREADABLE: Konfiguration unlesbar: " + str(config_path) + " (" + detail + ").")
        return ExitCode.CONFIG_UNREADABLE
    return ExitCode.OK


def _check_student_registry(project_root):
    registry_path = project_root / "Schuler.csv"
    aliases_path = project_root / "student_aliases.json"
    try:
        from student_registry import StudentRegistry

        registry = StudentRegistry(registry_path, aliases_path)
        if not registry.is_available:
            raise ValueError(registry.load_error or "unbekannter Verzeichnisfehler")
        if not registry.students:
            raise ValueError("the registry contains no students")
    except Exception as error:
        detail = str(error).strip() or error.__class__.__name__
        print("REGISTRY_UNREADABLE: Schuelerverzeichnis unlesbar: " + str(registry_path) + " (" + detail + ").")
        return ExitCode.REGISTRY_UNREADABLE
    return ExitCode.OK


def _check_pymupdf():
    """Report a missing PyMuPDF apart from an installed one that cannot load."""

    try:
        _load_pymupdf()
    except ModuleNotFoundError as error:
        detail = str(error).strip() or error.__class__.__name__
        print(
            "CORE_DEPENDENCY_UNAVAILABLE: Pflichtpaket fehlt: PyMuPDF ("
            + detail
            + ")."
        )
        return ExitCode.CORE_DEPENDENCY_UNAVAILABLE
    except PyMuPDFImportFailure as error:
        detail = str(error).strip() or error.__class__.__name__
        if error.runtime_related:
            print(
                "PYMUPDF_RUNTIME_UNAVAILABLE: PyMuPDF ist installiert, aber die "
                "Windows-Laufzeit (Microsoft Visual C++ Redistributable) fehlt "
                "oder ist beschaedigt (" + detail + ")."
            )
            return ExitCode.PYMUPDF_RUNTIME_UNAVAILABLE
        print(
            "PYMUPDF_IMPORT_FAILED: PyMuPDF ist installiert, kann aber nicht "
            "importiert werden (" + detail + ")."
        )
        return ExitCode.PYMUPDF_IMPORT_FAILED
    return ExitCode.OK


def _check_package_imports():
    code = _import_modules(
        CORE_DEPENDENCIES,
        ExitCode.CORE_DEPENDENCY_UNAVAILABLE,
        "CORE_DEPENDENCY_UNAVAILABLE",
    )
    if code != ExitCode.OK:
        return code
    return _check_pymupdf()


def check_packages(project_root=PROJECT_ROOT):
    """Check only the mandatory Python packages (used by quick_start.bat)."""

    del project_root  # Kept in the public signature for symmetry and tests.
    code = check_python_version(emit=False)
    if code != ExitCode.OK:
        check_python_version()
        return code
    code = _check_package_imports()
    if code != ExitCode.OK:
        return code
    print("PACKAGES_OK: pdfplumber, Pillow und PyMuPDF sind bereit.")
    return ExitCode.OK


def check_core(project_root=PROJECT_ROOT):
    """Validate everything required to start the application (OCR aside)."""

    project_root = Path(project_root).resolve()
    code = check_python_version()
    if code != ExitCode.OK:
        return code

    code = _check_package_imports()
    if code != ExitCode.OK:
        return code

    code = _import_modules(
        ("tkinter",),
        ExitCode.TKINTER_UNAVAILABLE,
        "TKINTER_UNAVAILABLE",
    )
    if code != ExitCode.OK:
        return code

    try:
        import tkinter

        # An import alone does not detect a missing/broken Tcl installation.
        # Tcl() does not create a visible window.
        tkinter.Tcl().eval("info patchlevel")
    except Exception as error:
        print("TKINTER_UNAVAILABLE: Tcl kann nicht initialisiert werden (" + str(error) + ").")
        return ExitCode.TKINTER_UNAVAILABLE

    code = _import_modules(
        CORE_APPLICATION_MODULES,
        ExitCode.APPLICATION_IMPORT_FAILED,
        "APPLICATION_IMPORT_FAILED",
    )
    if code != ExitCode.OK:
        return code

    code = _check_subjects_config(project_root)
    if code != ExitCode.OK:
        return code

    code = _check_student_registry(project_root)
    if code != ExitCode.OK:
        return code

    print(
        "CORE_OK: Pakete, tkinter, Anwendungsimporte, Konfiguration und "
        "Schuelerverzeichnis sind bereit."
    )
    return ExitCode.OK


def _load_pymupdf():
    """Load PyMuPDF and distinguish an absent wheel from a broken runtime."""

    available_modules = []
    for module_name in ("pymupdf", "fitz"):
        try:
            if importlib.util.find_spec(module_name) is not None:
                available_modules.append(module_name)
        except (ImportError, AttributeError, ValueError):
            continue

    if not available_modules:
        raise ModuleNotFoundError("PyMuPDF ist nicht installiert")

    errors = []
    for module_name in available_modules:
        try:
            module = importlib.import_module(module_name)
            if not all(hasattr(module, attribute) for attribute in ("open", "Matrix")):
                errors.append(module_name + ": Modul ist nicht PyMuPDF-kompatibel")
                continue
            return module
        except Exception as error:
            detail = str(error).strip() or error.__class__.__name__
            errors.append(module_name + ": " + detail)

    combined = "; ".join(errors)
    runtime_markers = (
        "dll load failed",
        "msvcp140",
        "vcruntime140",
        "pyinit__extra",
        "while importing _extra",
        "winerror 126",
        "winerror 127",
        "specified module could not be found",
        "modul wurde nicht gefunden",
        "dynamic link library",
    )
    runtime_related = any(
        marker in combined.casefold() for marker in runtime_markers
    )
    raise PyMuPDFImportFailure(combined, runtime_related=runtime_related)


def check_ocr(project_root=PROJECT_ROOT):
    """Check what processing needs: PyMuPDF, Tesseract and deu/eng data.

    Missing ``osd`` data is only a warning: orientation detection merely
    orders a single retry of an unrecognised page.
    """

    del project_root  # Kept in the public signature for symmetry and tests.
    code = check_python_version(emit=False)
    if code != ExitCode.OK:
        check_python_version()
        return code

    code = _check_pymupdf()
    if code != ExitCode.OK:
        return code

    try:
        from ocr import TesseractOCRProvider

        availability = TesseractOCRProvider().check_availability()
    except Exception as error:
        detail = str(error).strip() or error.__class__.__name__
        print("OCR_CHECK_FAILED: OCR-Pruefung fehlgeschlagen (" + detail + ").")
        return ExitCode.OCR_CHECK_FAILED

    if availability.available:
        print(
            "OCR_OK: Tesseract "
            + str(availability.executable)
            + " ist bereit; installierte Sprachdaten: "
            + ", ".join(availability.languages)
            + "."
        )
        if "osd" not in availability.languages:
            print(
                "OCR_WARNING_OSD_MISSING: Die Sprachdaten osd fehlen; seitlich "
                "gedrehte Scans werden schlechter erkannt. Die Verarbeitung "
                "ist trotzdem moeglich."
            )
        return ExitCode.OK

    message = availability.message
    if availability.executable is None:
        code = ExitCode.TESSERACT_UNAVAILABLE
        category = "TESSERACT_UNAVAILABLE"
    elif availability.missing_languages:
        code = ExitCode.OCR_LANGUAGE_DATA_UNAVAILABLE
        category = "OCR_LANGUAGE_DATA_UNAVAILABLE"
    else:
        code = ExitCode.OCR_CHECK_FAILED
        category = "OCR_CHECK_FAILED"
    print(category + ": OCR ist nicht verfuegbar. " + message)
    return code


def requirements_digest(project_root=PROJECT_ROOT):
    """Return a deterministic digest of the mandatory requirements input."""

    requirements_path = Path(project_root).resolve() / DEFAULT_REQUIREMENTS
    digest = hashlib.sha256()
    digest.update(DEFAULT_REQUIREMENTS.encode("ascii"))
    digest.update(b"\0")
    digest.update(requirements_path.read_bytes())
    return digest.hexdigest()


def requirements_current(project_root=PROJECT_ROOT):
    project_root = Path(project_root).resolve()
    stamp_path = project_root / DEFAULT_STAMP
    try:
        expected = requirements_digest(project_root)
    except FileNotFoundError:
        print("BOOTSTRAP_ERROR: requirements.txt fehlt.")
        return ExitCode.BOOTSTRAP_ERROR
    except OSError as error:
        print("BOOTSTRAP_ERROR: requirements.txt kann nicht gelesen werden (" + str(error) + ").")
        return ExitCode.BOOTSTRAP_ERROR

    try:
        actual = stamp_path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        print("REQUIREMENTS_CHANGED: Der Requirements-Stempel fehlt.")
        return ExitCode.REQUIREMENTS_CHANGED
    except (OSError, UnicodeError) as error:
        print("BOOTSTRAP_ERROR: Requirements-Stand ist unlesbar (" + str(error) + ").")
        return ExitCode.BOOTSTRAP_ERROR

    if actual != expected:
        print("REQUIREMENTS_CHANGED: requirements.txt wurde seit der Installation geaendert.")
        return ExitCode.REQUIREMENTS_CHANGED
    print("REQUIREMENTS_OK: Installierte Kernabhaengigkeiten sind aktuell.")
    return ExitCode.OK


def write_requirements_stamp(project_root=PROJECT_ROOT):
    project_root = Path(project_root).resolve()
    stamp_path = project_root / DEFAULT_STAMP
    temporary_path = stamp_path.with_suffix(stamp_path.suffix + ".tmp")
    try:
        stamp_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            requirements_digest(project_root) + "\n", encoding="ascii"
        )
        temporary_path.replace(stamp_path)
    except (OSError, UnicodeError) as error:
        print("BOOTSTRAP_ERROR: Requirements-Stand kann nicht gespeichert werden (" + str(error) + ").")
        return ExitCode.BOOTSTRAP_ERROR
    print("REQUIREMENTS_STAMP_WRITTEN: " + str(stamp_path) + ".")
    return ExitCode.OK


def prepare_venv(project_root=PROJECT_ROOT):
    """Create a fresh local environment, preserving an existing broken one.

    Called with a supported base interpreter only when the local interpreter
    is absent or unusable. Never clear a directory or follow a junction outside
    the project; backups remain available for manual recovery.
    """

    code = check_python_version()
    if code != ExitCode.OK:
        return code
    project_root = Path(project_root).resolve()
    target = project_root / ".venv"
    backup_root = project_root / ".bootstrap"
    try:
        if target.resolve() != target or backup_root.resolve() != backup_root:
            raise ValueError(".venv und .bootstrap duerfen keine Umleitungen sein")
        if Path(sys.prefix).resolve() == target:
            raise ValueError("Die aktive Python-Umgebung darf nicht verschoben werden")
        if os.path.lexists(str(target)):
            backup_root.mkdir(exist_ok=True)
            backup = backup_root / ("venv-backup-" + uuid.uuid4().hex)
            target.rename(backup)
            print("VENV_BACKUP: " + str(backup))
        venv.EnvBuilder(with_pip=True).create(str(target))
    except Exception as error:
        print("BOOTSTRAP_ERROR: .venv konnte nicht erstellt werden (" + str(error) + ").")
        return ExitCode.BOOTSTRAP_ERROR
    print("VENV_READY: " + str(target))
    return ExitCode.OK


def open_tesseract_download():
    """Open the official installer page after the user agreed in setup.bat."""

    import webbrowser

    try:
        opened = webbrowser.open(TESSERACT_DOWNLOAD_URL)
    except Exception as error:  # Browser integration is best effort.
        print("TESSERACT_GUIDE_NOT_OPENED: " + TESSERACT_DOWNLOAD_URL + " (" + str(error) + ").")
        return ExitCode.BOOTSTRAP_ERROR
    if not opened:
        print("TESSERACT_GUIDE_NOT_OPENED: " + TESSERACT_DOWNLOAD_URL + ".")
        return ExitCode.BOOTSTRAP_ERROR
    print("TESSERACT_GUIDE_OPENED: " + TESSERACT_DOWNLOAD_URL + ".")
    return ExitCode.OK


def build_argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="Project directory containing configuration and requirements files.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="check-core",
        choices=(
            "check-python",
            "prepare-venv",
            "check-packages",
            "check-core",
            "check-ocr",
            "open-tesseract-download",
            "requirements-current",
            "write-requirements-stamp",
        ),
    )
    return parser


def main(argv=None):
    args = build_argument_parser().parse_args(argv)
    if args.command == "check-python":
        code = check_python_version()
    elif args.command == "prepare-venv":
        code = prepare_venv(args.project_root)
    elif args.command == "check-packages":
        code = check_packages(args.project_root)
    elif args.command == "check-core":
        code = check_core(args.project_root)
    elif args.command == "check-ocr":
        code = check_ocr(args.project_root)
    elif args.command == "open-tesseract-download":
        code = open_tesseract_download()
    elif args.command == "requirements-current":
        code = requirements_current(args.project_root)
    else:
        code = write_requirements_stamp(args.project_root)
    return int(code)


if __name__ == "__main__":
    # Redirected Windows logs must remain readable even for Unicode paths and
    # exception messages. BAT status messages themselves are ASCII.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.exit(main())
