"""Exercise real cmd.exe control flow without network/system installations."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

import pytest

import bootstrap


ROOT = Path(__file__).resolve().parents[1]
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Windows BAT launchers")
# Synthetic user data. Launchers must never rewrite it, also on updates.
USER_DATA = {
    "Schuler.csv": "Nummer;Nachname;Vorname\n1;Beispiel;Lena\n",
    "student_aliases.json": '{"aliases": []}',
    "subjects_config.json": '{"subjects": ["Mathematik"], "aliases": {}}',
    "subjects_aliases.json": "{}",
    "manual_review.json": '{"review_items": []}',
}


@pytest.fixture
def launcher_project(tmp_path):
    root = tmp_path / "App space & (test)! O'Brien \u00dcbung \u0141\u00f3d\u017a"
    root.mkdir()
    for name in ("setup.bat", "start_app.bat", "quick_start.bat"):
        shutil.copy2(ROOT / name, root / name)
    for name, content in USER_DATA.items():
        (root / name).write_text(content, encoding="utf-8")
    venv.EnvBuilder(with_pip=False).create(root / ".venv")
    # Keep the actual BAT/parser and Python executable; replace only the
    # external operations so failures and optional branches are deterministic.
    (root / "probe.py").write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "root = Path(__file__).parent\n"
        "state = json.loads((root / 'state.json').read_text())\n"
        "def save():\n"
        "    (root / 'state.json').write_text(json.dumps(state))\n"
        "def record(kind):\n"
        "    with (root / 'calls.jsonl').open('a', encoding='utf-8') as f:\n"
        "        f.write(json.dumps([kind, sys.argv[1:], sys.prefix, str(Path.cwd())]) + '\\n')\n",
        encoding="utf-8",
    )
    (root / "bootstrap.py").write_text(
        "from probe import *\n"
        "command = sys.argv[-1]\nrecord(command)\n"
        "if command == 'prepare-venv':\n"
        "    import importlib.util\n"
        f"    spec = importlib.util.spec_from_file_location('real_bootstrap', {str(ROOT / 'bootstrap.py')!r})\n"
        "    module = importlib.util.module_from_spec(spec)\n"
        "    spec.loader.exec_module(module)\n"
        "    sys.exit(module.prepare_venv(root))\n"
        "if command == 'write-requirements-stamp':\n"
        "    state['requirements-current'] = 0\n    save()\n"
        # The operator installs Tesseract while setup waits for ENTER.
        "if command == 'open-tesseract-download' and state.get('installer-adds-ocr'):\n"
        "    state['check-ocr'] = 0\n    save()\n"
        "print(command + ': simulated diagnostic')\n"
        "sys.exit(state.get(command, 0))\n",
        encoding="utf-8",
    )
    (root / "pip.py").write_text(
        "from probe import *\nrecord('pip')\n"
        "code = state.get('pip-exit', 0)\n"
        # A forced reinstall repairs broken wheels, not a missing VC++ runtime.
        "if not code and '--force-reinstall' in sys.argv and state.get('check-core') in (20, 22, 35):\n"
        "    state['check-core'] = 0\n    save()\n"
        "print('pip: simulated install')\nsys.exit(code)\n",
        encoding="utf-8",
    )
    (root / "ensurepip.py").write_text(
        "from probe import *\nrecord('ensurepip')\nsys.exit(0)\n", encoding="ascii"
    )
    (root / "start_gui.py").write_text(
        "from probe import *\nrecord('gui')\nsys.exit(state.get('gui-exit', 0))\n",
        encoding="ascii",
    )
    # Healthy by default: packages, data, Tesseract with deu.
    (root / "state.json").write_text("{}", encoding="ascii")
    return root


def run_bat(root, name, args="", answers="", state=None, env=None):
    if state is not None:
        (root / "state.json").write_text(json.dumps(state), encoding="ascii")
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    # cmd.exe parses /c using its own quoting rules, not C argv escaping.
    result = subprocess.run(
        f'"{comspec}" /d /v:off /s /c ""{root / name}" {args}"',
        cwd=root.parent,  # Double-click/shortcut callers need not be in the app.
        input=answers, text=True, encoding="utf-8", errors="replace",
        capture_output=True, env=env, timeout=60,
    )
    return result


def calls(root):
    path = root / "calls.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def kinds(root):
    return [c[0] for c in calls(root)]


def user_data(root):
    return {name: (root / name).read_bytes() for name in USER_DATA}


def assert_local_environment(root):
    for kind, _, prefix, cwd in calls(root):
        if kind != "prepare-venv":  # Runs with the base Python by design.
            assert Path(prefix) == root / ".venv", kind
        assert Path(cwd) == root, kind


@WINDOWS
def test_healthy_repeated_start_uses_only_venv_and_never_pip(launcher_project):
    root = launcher_project
    for _ in range(2):
        result = run_bat(root, "start_app.bat")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "OK: Tesseract OCR" in result.stdout
        assert "WARNUNG" not in result.stdout
    assert kinds(root).count("gui") == 2
    assert not any(k in ("pip", "ensurepip", "prepare-venv") for k in kinds(root))
    assert_local_environment(root)


@WINDOWS
@pytest.mark.parametrize("state,force", [
    ({"requirements-current": 40}, False),
    ({"check-core": 20}, True),
    ({"check-core": 35}, True),
    ({"requirements-current": 40, "check-core": 20}, True),
])
def test_smart_repairs_changed_or_broken_packages_once(launcher_project, state, force):
    root = launcher_project
    before = user_data(root)
    result = run_bat(root, "start_app.bat", "--health-only", state=state)
    assert result.returncode == 0, result.stdout + result.stderr
    installs = [c for c in calls(root) if c[0] == "pip"]
    assert installs
    assert any("--force-reinstall" in c[1] for c in installs) == force
    order = kinds(root)
    last_core_check = max(i for i, kind in enumerate(order) if kind == "check-core")
    assert order.index("write-requirements-stamp") > last_core_check
    result = run_bat(root, "start_app.bat", "--health-only")
    assert result.returncode == 0
    assert len([c for c in calls(root) if c[0] == "pip"]) == len(installs)
    assert not any(c[0] == "gui" for c in calls(root))
    assert user_data(root) == before


@WINDOWS
def test_updated_requirements_install_once_and_keep_user_data(launcher_project):
    root = launcher_project
    before = user_data(root)
    result = run_bat(root, "start_app.bat", state={"requirements-current": 40})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "requirements.txt wurde geaendert" in result.stdout
    pip_calls = [c for c in calls(root) if c[0] == "pip"]
    assert len(pip_calls) == 1 and "--force-reinstall" not in pip_calls[0][1]
    assert any("requirements.txt" in arg for arg in pip_calls[0][1])
    order = kinds(root)
    assert order.index("pip") < order.index("write-requirements-stamp") < order.index("gui")
    assert user_data(root) == before
    assert_local_environment(root)


@WINDOWS
@pytest.mark.parametrize("launcher,args", [("setup.bat", "--core-only"), ("start_app.bat", "")])
def test_pip_failure_preserves_code_and_never_reports_success(launcher_project, launcher, args):
    root = launcher_project
    result = run_bat(root, launcher, args, state={"requirements-current": 40, "pip-exit": 17})
    assert result.returncode == 17, result.stdout + result.stderr
    assert "FEHLER" in result.stdout and "Diagnoselog:" in result.stdout
    assert "SETUP ABGESCHLOSSEN" not in result.stdout
    assert not any(c[0] in ("gui", "write-requirements-stamp") for c in calls(root))


@WINDOWS
@pytest.mark.parametrize("code", [21, 23, 24])
def test_tkinter_or_data_failure_does_not_install(launcher_project, code):
    before = user_data(launcher_project)
    result = run_bat(launcher_project, "start_app.bat", state={"check-core": code})
    assert result.returncode == code
    assert not any(c[0] in ("pip", "gui") for c in calls(launcher_project))
    assert user_data(launcher_project) == before


@WINDOWS
@pytest.mark.parametrize("code", [23, 24])
def test_setup_reports_data_errors_without_reinstall_or_stamp(launcher_project, code):
    root = launcher_project
    before = user_data(root)
    result = run_bat(root, "setup.bat", "--unattended", state={"check-core": code})
    assert result.returncode == code, result.stdout + result.stderr
    assert "nicht veraendert" in result.stdout
    assert kinds(root).count("pip") == 1
    assert not any(k in ("write-requirements-stamp", "check-ocr") for k in kinds(root))
    assert user_data(root) == before


@WINDOWS
def test_clean_setup_with_tesseract_succeeds_and_stamps_after_checks(launcher_project):
    root = launcher_project
    shutil.rmtree(root / ".venv")
    result = run_bat(root, "setup.bat", answers="N\nN\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SETUP ABGESCHLOSSEN" in result.stdout
    assert "Tesseract mit deutschen Sprachdaten sind bereit" in result.stdout
    assert kinds(root) == [
        "prepare-venv", "ensurepip", "pip", "check-core",
        "write-requirements-stamp", "check-ocr",
    ]
    assert (root / ".venv" / "pyvenv.cfg").exists()
    assert_local_environment(root)


@WINDOWS
@pytest.mark.parametrize("args", ["", "--unattended"])
def test_setup_with_ready_environment_reports_success(launcher_project, args):
    result = run_bat(launcher_project, "setup.bat", args, answers="N\nN\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SETUP ABGESCHLOSSEN" in result.stdout
    assert kinds(launcher_project).count("check-ocr") == 1


@WINDOWS
@pytest.mark.parametrize("code,reason", [(31, "nicht gefunden"), (32, "Sprachdaten deu")])
def test_interactive_setup_declined_tesseract_ends_with_distinct_code(launcher_project, code, reason):
    root = launcher_project
    result = run_bat(root, "setup.bat", answers="N\nN\nN\n", state={"check-ocr": code})
    assert result.returncode == code, result.stdout + result.stderr
    assert reason in result.stdout
    assert "German" in result.stdout and "UB-Mannheim" in result.stdout
    assert "VERARBEITUNG NICHT VERFUEGBAR" in result.stdout
    assert "SETUP ABGESCHLOSSEN" not in result.stdout
    assert kinds(root).count("check-ocr") == 1
    assert "open-tesseract-download" not in kinds(root)
    # Packages are ready, so start_app must not reinstall them later.
    assert "write-requirements-stamp" in kinds(root)


@WINDOWS
def test_interactive_setup_rechecks_after_consented_guide(launcher_project):
    root = launcher_project
    result = run_bat(root, "setup.bat", answers="J\n\nN\nN\n", state={"check-ocr": 31})
    assert result.returncode == 31, result.stdout + result.stderr
    order = kinds(root)
    assert order.count("check-ocr") == 2
    assert order.index("check-ocr") < order.index("open-tesseract-download")
    assert "erneut geprueft" in result.stdout

    (root / "calls.jsonl").unlink()
    result = run_bat(
        root, "setup.bat", answers="J\n\nN\nN\n",
        state={"check-ocr": 32, "installer-adds-ocr": True},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SETUP ABGESCHLOSSEN" in result.stdout
    assert kinds(root).count("check-ocr") == 2


@WINDOWS
@pytest.mark.parametrize("code", [31, 32, 33])
def test_unattended_setup_without_tesseract_fails_without_questions(launcher_project, code):
    root = launcher_project
    result = run_bat(root, "setup.bat", "--unattended", state={"check-ocr": code})
    assert result.returncode == code, result.stdout + result.stderr
    assert "[j/N]" not in result.stdout
    assert "SETUP ABGESCHLOSSEN" not in result.stdout
    assert "VERARBEITUNG NICHT VERFUEGBAR" in result.stdout
    assert not any(k in ("open-tesseract-download", "gui") for k in kinds(root))
    assert kinds(root).count("pip") == 1


@WINDOWS
def test_core_only_installs_packages_and_warns_about_processing(launcher_project):
    root = launcher_project
    result = run_bat(root, "setup.bat", "--core-only", state={"check-ocr": 31})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SETUP ABGESCHLOSSEN" in result.stdout
    assert "benoetigt Tesseract OCR" in result.stdout
    assert "[j/N]" not in result.stdout
    order = kinds(root)
    assert order.count("pip") == 1 and "write-requirements-stamp" in order
    assert "check-ocr" not in order and "gui" not in order


@WINDOWS
def test_pymupdf_runtime_failure_differs_from_missing_package(launcher_project):
    root = launcher_project
    # Missing package: one forced reinstall repairs it.
    result = run_bat(root, "start_app.bat", "--health-only", state={"check-core": 20})
    assert result.returncode == 0, result.stdout + result.stderr
    assert any("--force-reinstall" in c[1] for c in calls(root) if c[0] == "pip")

    # Broken VC++ runtime: pip cannot help, setup.bat is named instead.
    (root / "calls.jsonl").unlink()
    result = run_bat(root, "start_app.bat", state={"check-core": 34})
    assert result.returncode == 34, result.stdout + result.stderr
    assert "Visual C++" in result.stdout and "setup.bat" in result.stdout
    assert not any(k in ("pip", "gui") for k in kinds(root))


@WINDOWS
@pytest.mark.parametrize("args,answers,prompted", [("--unattended", "", False), ("", "N\n", True)])
def test_setup_pymupdf_runtime_failure_reinstalls_once_then_stops(launcher_project, args, answers, prompted):
    root = launcher_project
    result = run_bat(root, "setup.bat", args, answers, state={"check-core": 34})
    assert result.returncode == 34, result.stdout + result.stderr
    pip_calls = [c for c in calls(root) if c[0] == "pip"]
    assert len(pip_calls) == 2 and "--force-reinstall" in pip_calls[1][1]
    assert ("VC++ Redistributable automatisch installieren" in result.stdout) == prompted
    assert not any(k in ("write-requirements-stamp", "check-ocr") for k in kinds(root))


@WINDOWS
def test_existing_venv_does_not_require_python_on_path(launcher_project):
    env = dict(os.environ, PATH=str(Path(os.environ["SystemRoot"]) / "System32"))
    result = run_bat(launcher_project, "setup.bat", "--core-only", env=env)
    assert result.returncode == 0, result.stdout + result.stderr


@WINDOWS
@pytest.mark.parametrize("launcher,args", [("setup.bat", "--core-only"), ("start_app.bat", "--health-only")])
def test_missing_python_reports_failure_without_system_install(launcher_project, launcher, args):
    root = launcher_project
    (root / ".venv").rename(root / "saved-venv")
    env = dict(os.environ, PATH=str(Path(os.environ["SystemRoot"]) / "System32"))
    result = run_bat(root, launcher, args, env=env)
    assert result.returncode == 10, result.stdout + result.stderr
    assert "Diagnoselog:" in result.stdout
    assert not (root / ".venv").exists()
    assert not calls(root)


@WINDOWS
def test_smart_recovers_unlaunchable_python_and_keeps_backup(launcher_project):
    root = launcher_project
    (root / ".venv" / "pyvenv.cfg").write_text("home = C:\\missing-bootstrap-test-python\n", encoding="ascii")
    result = run_bat(root, "start_app.bat", "--health-only")
    assert result.returncode == 0, result.stdout + result.stderr
    assert list((root / ".bootstrap").glob("venv-backup-*/pyvenv.cfg"))
    assert any(c[0] == "prepare-venv" for c in calls(root))


@WINDOWS
@pytest.mark.parametrize("launcher", ["start_app.bat", "quick_start.bat"])
def test_launchers_propagate_gui_exit(launcher_project, launcher):
    result = run_bat(launcher_project, launcher, state={"gui-exit": 19})
    assert result.returncode == 19, result.stdout + result.stderr


@WINDOWS
@pytest.mark.parametrize("launcher", ["start_app.bat", "quick_start.bat"])
def test_launchers_start_gui_without_tesseract_and_without_pip(launcher_project, launcher):
    root = launcher_project
    result = run_bat(root, launcher, state={"check-ocr": 31})
    assert result.returncode == 0, result.stdout + result.stderr
    order = kinds(root)
    assert order.count("gui") == 1
    assert not any(k in ("pip", "ensurepip", "prepare-venv") for k in order)
    if launcher == "start_app.bat":
        assert "WARNUNG: Tesseract OCR" in result.stdout
        assert "Verarbeitung von PDF-Dateien bleibt gesperrt" in result.stdout
    else:
        # quick_start stays fast: the GUI itself reports the missing OCR.
        assert order == ["check-packages", "gui"]
    assert_local_environment(root)


@WINDOWS
def test_quick_start_missing_environment_never_installs(launcher_project):
    (launcher_project / ".venv").rename(launcher_project / "saved-venv")
    result = run_bat(launcher_project, "quick_start.bat")
    assert result.returncode == 2
    assert "start_app.bat" in result.stdout
    assert calls(launcher_project) == []


@WINDOWS
@pytest.mark.parametrize("code", [20, 34, 35])
def test_quick_start_missing_package_fails_without_repair(launcher_project, code):
    root = launcher_project
    result = run_bat(root, "quick_start.bat", state={"check-packages": code})
    assert result.returncode == code, result.stdout + result.stderr
    assert "Python-Paket fehlt oder ist beschaedigt" in result.stdout
    assert "start_app.bat" in result.stdout
    assert kinds(root) == ["check-packages"]


def test_prepare_venv_preserves_existing_files_on_creation_failure(tmp_path, monkeypatch):
    old = tmp_path / ".venv"
    old.mkdir()
    (old / "marker.txt").write_text("preserved")

    def fail_create(self, path):
        raise OSError("simulated disk error")

    monkeypatch.setattr(bootstrap.venv.EnvBuilder, "create", fail_create)
    assert bootstrap.prepare_venv(tmp_path) == bootstrap.ExitCode.BOOTSTRAP_ERROR
    backups = list((tmp_path / ".bootstrap").glob("venv-backup-*/marker.txt"))
    assert len(backups) == 1 and backups[0].read_text() == "preserved"


def test_core_reports_broken_tcl_installation(tmp_path, monkeypatch):
    import tkinter

    def fail_tcl():
        raise tkinter.TclError("Can't find a usable init.tcl")

    monkeypatch.setattr(tkinter, "Tcl", fail_tcl)
    assert bootstrap.check_core(tmp_path) == bootstrap.ExitCode.TKINTER_UNAVAILABLE


def test_gui_startup_failure_is_available_in_launcher_stderr(monkeypatch, capsys):
    import gui_main

    def fail_gui():
        raise OSError("simulated GUI startup failure")

    monkeypatch.setattr(gui_main, "PDFRenamerGUI", fail_gui)
    monkeypatch.setattr(gui_main.messagebox, "showerror", lambda *args: None)
    with pytest.raises(SystemExit) as error:
        gui_main.main()
    assert error.value.code == 1
    assert "simulated GUI startup failure" in capsys.readouterr().err


@WINDOWS
def test_shortcut_powershell_preserves_target_and_working_directory(tmp_path):
    root = tmp_path / "Shortcut app & (test)! O'Brien"
    desktop = tmp_path / "isolated desktop"
    root.mkdir()
    desktop.mkdir()
    target = root / "start_app.bat"
    target.write_text("@exit /b 0\n", encoding="ascii")
    line = next(line for line in (ROOT / "create_desktop_shortcut.bat").read_text(encoding="ascii").splitlines()
                if line.startswith("powershell.exe "))
    script = line.split('-Command "', 1)[1][:-1]
    # Execute the actual COM shortcut code, changing only the desktop location.
    script = script.replace("[Environment]::GetFolderPath('DesktopDirectory')", "$env:LAUNCHER_TEST_DESKTOP")
    env = dict(os.environ, LAUNCHER_TEST_DESKTOP=str(desktop),
               PDF_RENAMER_SHORTCUT_TARGET=str(target), PDF_RENAMER_SHORTCUT_WORKDIR=str(root))
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    verify = "$s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $env:LAUNCHER_TEST_DESKTOP 'PDF Datei Umbenenner.lnk')); @($s.TargetPath, $s.WorkingDirectory) | ConvertTo-Json -Compress"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", verify],
                            capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode == 0, result.stderr
    actual_target, actual_workdir = json.loads(result.stdout)
    assert Path(actual_target) == target
    assert Path(actual_workdir) == root
