"""Exercise real cmd.exe control flow without network/system installations."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import venv

import pytest

import bootstrap


ROOT = Path(__file__).resolve().parents[1]
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Windows BAT launchers")
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
PAUSE_TEXT = "eine beliebige Taste druecken"
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
    # The GUI stub can stay open until the test writes release-gui.
    (root / "start_gui.py").write_text(
        "from probe import *\nimport time\nrecord('gui')\n"
        "deadline = time.time() + 30\n"
        "while state.get('gui-wait') and not (root / 'release-gui').exists() and time.time() < deadline:\n"
        "    time.sleep(0.05)\n"
        "record('gui-closed')\n"
        "sys.exit(state.get('gui-exit', 0))\n",
        encoding="ascii",
    )
    # Consented Python installation: copies a prepared stand-in interpreter.
    (root / "install_python.ps1").write_text(
        "param([string]$TargetDirectory, [string]$InstallerLog)\n"
        "$root = Split-Path -Parent $MyInvocation.MyCommand.Path\n"
        "$state = Get-Content -LiteralPath (Join-Path $root 'state.json') -Raw | ConvertFrom-Json\n"
        "$line = '[\"python-install\", [' + (ConvertTo-Json $TargetDirectory) + '], \"\", ' + (ConvertTo-Json (Get-Location).Path) + ']'\n"
        "Add-Content -LiteralPath (Join-Path $root 'calls.jsonl') -Value $line -Encoding UTF8\n"
        "$code = 0\n"
        "if ($state.PSObject.Properties['python-install']) { $code = [int]$state.'python-install' }\n"
        "if ($code -eq 0) { Copy-Item -LiteralPath (Join-Path $root 'prepared-python') -Destination $TargetDirectory -Recurse }\n"
        "exit $code\n",
        encoding="ascii",
    )
    # Healthy by default: packages, data, Tesseract with deu.
    (root / "state.json").write_text("{}", encoding="ascii")
    return root


def run_bat(root, name, args="", answers="", state=None, env=None):
    if state is not None:
        (root / "state.json").write_text(json.dumps(state), encoding="ascii")
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    # Output goes to files, not pipes: the detached GUI process inherits the
    # handles, and a pipe would stay open until the GUI closes.
    stamp = time.time_ns()
    in_path, out_path, err_path = (root.parent / f"bat-{stream}-{stamp}.txt" for stream in ("in", "out", "err"))
    # Answers come from a file: on a pipe, set /p swallows all buffered
    # lines at once, on a file it reads exactly one CRLF-terminated line.
    in_path.write_bytes(answers.replace("\n", "\r\n").encode("ascii"))
    with open(in_path, "rb") as answers_file, open(out_path, "wb") as out, open(err_path, "wb") as err:
        # cmd.exe parses /c using its own quoting rules, not C argv escaping.
        result = subprocess.run(
            f'"{comspec}" /d /v:off /s /c ""{root / name}" {args}"',
            cwd=root.parent,  # Double-click/shortcut callers need not be in the app.
            stdin=answers_file, stdout=out, stderr=err, env=env, timeout=60,
        )
    result.stdout = out_path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")
    result.stderr = err_path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")
    return result


def calls(root):
    path = root / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def kinds(root):
    return [c[0] for c in calls(root)]


def wait_for(root, kind, count=1, timeout=20):
    """The GUI runs as its own process: its record may follow the BAT exit."""

    deadline = time.time() + timeout
    while kinds(root).count(kind) < count:
        assert time.time() < deadline, f"{kind} not recorded: {kinds(root)}"
        time.sleep(0.05)


def user_data(root):
    return {name: (root / name).read_bytes() for name in USER_DATA}


def assert_local_environment(root):
    for kind, _, prefix, cwd in calls(root):
        # The base Python creates .venv; the installer runs in PowerShell.
        if kind not in ("prepare-venv", "python-install"):
            assert Path(prefix) == root / ".venv", kind
        assert Path(cwd) == root, kind


def without_python_env(root, **extra):
    """No Python on PATH; the per-user install location points into tmp."""

    system32 = Path(os.environ["SystemRoot"]) / "System32"
    env = dict(os.environ, PATH=str(system32), LOCALAPPDATA=str(root.parent / "localappdata"))
    env.update(extra)
    return env


def prepare_stand_in_python(root):
    """A working interpreter at <dir>\\python.exe, like a fresh installation."""

    prepared = root / "prepared-python"
    venv.EnvBuilder(with_pip=False).create(prepared)
    shutil.copy2(prepared / "Scripts" / "python.exe", prepared / "python.exe")
    return root.parent / "localappdata" / "Programs" / "Python" / "Python314"


@WINDOWS
def test_healthy_repeated_start_uses_only_venv_and_never_pip(launcher_project):
    root = launcher_project
    for _ in range(2):
        result = run_bat(root, "start_app.bat")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "OK: Tesseract OCR" in result.stdout
        assert "WARNUNG" not in result.stdout
    wait_for(root, "gui", 2)
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
    wait_for(root, "gui")
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
@pytest.mark.parametrize("launcher,args", [
    ("setup.bat", "--core-only"), ("setup.bat", "--unattended"), ("start_app.bat", "--health-only"),
])
def test_missing_python_reports_failure_without_system_install(launcher_project, launcher, args):
    root = launcher_project
    (root / ".venv").rename(root / "saved-venv")
    result = run_bat(root, launcher, args, env=without_python_env(root))
    assert result.returncode == 10, result.stdout + result.stderr
    assert "Diagnoselog:" in result.stdout
    assert "[j/N]" not in result.stdout
    assert not (root / ".venv").exists()
    assert not calls(root)


@WINDOWS
def test_consented_python_install_continues_setup(launcher_project):
    root = launcher_project
    (root / ".venv").rename(root / "saved-venv")
    target = prepare_stand_in_python(root)
    result = run_bat(root, "setup.bat", answers="J\nN\nN\n", env=without_python_env(root))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "nur fuer Ihr Benutzerkonto, ohne Administratorrechte" in result.stdout
    assert "Python 3.14 wurde fuer Ihr Benutzerkonto installiert" in result.stdout
    assert "SETUP ABGESCHLOSSEN" in result.stdout
    installs = [c for c in calls(root) if c[0] == "python-install"]
    assert len(installs) == 1 and Path(installs[0][1][0]) == target
    order = kinds(root)
    assert order[:3] == ["python-install", "prepare-venv", "ensurepip"]
    # The new .venv was built by the freshly installed interpreter.
    prepare = next(c for c in calls(root) if c[0] == "prepare-venv")
    assert Path(prepare[2]) == target
    assert (root / ".venv" / "Scripts" / "python.exe").exists()


@WINDOWS
@pytest.mark.parametrize("answers,install_code,message", [
    ("N\nN\n", None, "Python manuell installieren"),
    ("J\nN\n", 70, "konnte nicht heruntergeladen werden"),
    ("J\nN\n", 71, "Signatur des Python-Installers ist ungueltig"),
    ("J\nN\n", 72, "Python-Installation ist fehlgeschlagen. Code: 72"),
])
def test_declined_or_failed_python_install_ends_with_code_10(launcher_project, answers, install_code, message):
    root = launcher_project
    (root / ".venv").rename(root / "saved-venv")
    prepare_stand_in_python(root)
    state = {} if install_code is None else {"python-install": install_code}
    result = run_bat(root, "setup.bat", answers=answers, state=state, env=without_python_env(root))
    assert result.returncode == 10, result.stdout + result.stderr
    assert message in result.stdout
    assert "https://www.python.org/downloads/windows/" in result.stdout
    assert "SETUP ABGESCHLOSSEN" not in result.stdout
    assert kinds(root) == ([] if install_code is None else ["python-install"])
    assert not (root / ".venv").exists()


@WINDOWS
@pytest.mark.parametrize("architecture", ["x86", "ARM64"])
def test_python_install_is_offered_only_on_x64_windows(launcher_project, architecture):
    root = launcher_project
    (root / ".venv").rename(root / "saved-venv")
    env = without_python_env(root, PROCESSOR_ARCHITECTURE=architecture)
    env.pop("PROCESSOR_ARCHITEW6432", None)
    result = run_bat(root, "setup.bat", answers="N\n", env=env)
    assert result.returncode == 10, result.stdout + result.stderr
    assert "nur 64-Bit-Windows" in result.stdout and architecture in result.stdout
    assert "herunterladen und installieren" not in result.stdout
    assert calls(root) == []


@WINDOWS
@pytest.mark.parametrize("customize,unsuitable", [
    # Microsoft Store placeholder: "Python was not found", exit code 9009.
    ("import os\nos._exit(9009)\n", False),
    # A Python newer than the verified wheels.
    ("import sys\nsys.version_info = (3, 16, 0, 'final', 0)\n", True),
])
def test_store_placeholder_or_unsupported_python_is_not_used(launcher_project, customize, unsuitable):
    root = launcher_project
    (root / ".venv").rename(root / "saved-venv")
    fake_dir = root.parent / "WindowsApps"
    venv.EnvBuilder(with_pip=False).create(fake_dir)
    shutil.copy2(fake_dir / "Scripts" / "python.exe", fake_dir / "python.exe")
    (fake_dir / "Lib" / "site-packages" / "sitecustomize.py").write_text(customize, encoding="ascii")
    env = without_python_env(root)
    env["PATH"] += os.pathsep + str(fake_dir)
    result = run_bat(root, "setup.bat", "--unattended", env=env)
    assert result.returncode == 10, result.stdout + result.stderr
    assert ("nicht unterstuetzte Version" in result.stdout) == unsuitable
    assert calls(root) == []


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
def test_launchers_start_gui_as_separate_process_without_console(launcher_project, launcher):
    # Replaces the former exit code passing: the launcher console closes
    # while the GUI (pythonw.exe) keeps running and reports its own errors.
    root = launcher_project
    started = time.time()
    result = run_bat(root, launcher, state={"gui-wait": True, "gui-exit": 19})
    try:
        assert result.returncode == 0, result.stdout + result.stderr
        wait_for(root, "gui")
        assert "gui-closed" not in kinds(root)
        assert time.time() - started < 25
    finally:
        (root / "release-gui").write_text("", encoding="ascii")
    wait_for(root, "gui-closed")
    assert PAUSE_TEXT not in result.stdout
    assert_local_environment(root)


@WINDOWS
def test_gui_is_started_with_pythonw(launcher_project):
    root = launcher_project
    marker = root / "executable.txt"
    (root / "start_gui.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "Path(__file__).with_name('executable.txt').write_text(sys.executable, encoding='utf-8')\n",
        encoding="ascii",
    )
    result = run_bat(root, "quick_start.bat")
    assert result.returncode == 0, result.stdout + result.stderr
    deadline = time.time() + 20
    while not marker.exists() or not marker.read_text(encoding="utf-8"):
        assert time.time() < deadline
        time.sleep(0.05)
    assert Path(marker.read_text(encoding="utf-8")) == root / ".venv" / "Scripts" / "pythonw.exe"


@WINDOWS
@pytest.mark.parametrize("launcher", ["start_app.bat", "quick_start.bat"])
def test_launchers_start_gui_without_tesseract_and_without_pip(launcher_project, launcher):
    root = launcher_project
    result = run_bat(root, launcher, state={"check-ocr": 31})
    assert result.returncode == 0, result.stdout + result.stderr
    wait_for(root, "gui-closed")
    order = kinds(root)
    assert order.count("gui") == 1
    assert not any(k in ("pip", "ensurepip", "prepare-venv") for k in order)
    if launcher == "start_app.bat":
        assert "WARNUNG: Tesseract OCR" in result.stdout
        assert "Verarbeitung von PDF-Dateien bleibt gesperrt" in result.stdout
    else:
        # quick_start stays fast: the GUI itself reports the missing OCR.
        assert order == ["check-packages", "gui", "gui-closed"]
    assert_local_environment(root)


@WINDOWS
def test_setup_starts_the_application_on_request_without_waiting(launcher_project):
    root = launcher_project
    result = run_bat(root, "setup.bat", "--no-pause", answers="N\nJ\n", state={"gui-wait": True})
    try:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "eigenen Fenster" in result.stdout and "SETUP ABGESCHLOSSEN" in result.stdout
        wait_for(root, "gui")
        assert "gui-closed" not in kinds(root)
    finally:
        (root / "release-gui").write_text("", encoding="ascii")
    wait_for(root, "gui-closed")


@WINDOWS
def test_setup_waits_for_a_key_when_started_by_double_click(launcher_project):
    # run_bat starts "cmd.exe /c <bat>" like Explorer and shortcuts do; the
    # closed input ends the wait at once, so tests cannot hang.
    root = launcher_project
    result = run_bat(root, "setup.bat", answers="N\nN\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.rstrip().endswith(PAUSE_TEXT + "...")
    for args in ("--no-pause", "--unattended"):
        result = run_bat(root, "setup.bat", args, answers="N\nN\n")
        assert result.returncode == 0, result.stdout + result.stderr
        assert PAUSE_TEXT not in result.stdout


@WINDOWS
@pytest.mark.parametrize("launcher,state", [
    ("start_app.bat", {"check-core": 23}),
    ("quick_start.bat", {"check-packages": 20}),
])
def test_launchers_wait_for_a_key_only_on_errors(launcher_project, launcher, state):
    root = launcher_project
    result = run_bat(root, launcher, state=state)
    assert result.returncode == state[next(iter(state))]
    assert PAUSE_TEXT in result.stdout
    result = run_bat(root, launcher, "--no-pause", state=state)
    assert PAUSE_TEXT not in result.stdout
    result = run_bat(root, launcher, state={})
    assert result.returncode == 0 and PAUSE_TEXT not in result.stdout
    wait_for(root, "gui-closed")


@WINDOWS
def test_launchers_started_from_an_open_console_do_not_wait(launcher_project):
    root = launcher_project
    (root / "state.json").write_text(json.dumps({"check-core": 23}), encoding="ascii")
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    for name in ("start_app.bat", "setup.bat"):
        # Typed into the console; relative, as cmd reads input in the OEM code page.
        result = subprocess.run(
            [comspec, "/d", "/q"], cwd=root, input=f'call ".\\{name}"\nexit\n',
            text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60,
        )
        assert "Code: 23" in result.stdout, result.stdout + result.stderr
        assert PAUSE_TEXT not in result.stdout


@WINDOWS
@pytest.mark.parametrize("launcher", ["setup.bat", "start_app.bat", "quick_start.bat"])
def test_unc_location_is_rejected_before_changing_directory(launcher_project, launcher):
    root = launcher_project
    drive, rest = os.path.splitdrive(str(root))
    unc_root = Path(rf"\\localhost\{drive[0]}${rest}")
    if not (unc_root / launcher).exists():
        pytest.skip("administrative share \\\\localhost\\<drive>$ not reachable")
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    result = subprocess.run(
        f'"{comspec}" /d /v:off /s /c ""{unc_root / launcher}" --no-pause"',
        cwd=os.environ["SystemRoot"], input="", text=True, encoding="utf-8",
        errors="replace", capture_output=True, timeout=60,
    )
    assert result.returncode == 51, result.stdout + result.stderr
    assert "Netzwerkpfad" in result.stdout
    assert calls(root) == []


@WINDOWS
def test_onedrive_location_asks_before_installing(launcher_project):
    root = launcher_project
    env = dict(os.environ, OneDriveCommercial=str(root.parent))
    result = run_bat(root, "setup.bat", answers="N\n", env=env)
    assert result.returncode == 52, result.stdout + result.stderr
    assert "OneDrive" in result.stdout and "PDF-Datei-Umbenenner" in result.stdout
    assert calls(root) == []

    result = run_bat(root, "setup.bat", answers="J\nN\nN\n", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pip" in kinds(root)

    (root / "calls.jsonl").unlink()
    result = run_bat(root, "setup.bat", "--unattended", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARNUNG" in result.stdout and "[j/N]" not in result.stdout


@WINDOWS
def test_setup_removes_mark_of_the_web_from_program_files_only(launcher_project):
    root = launcher_project
    zone = "[ZoneTransfer]\r\nZoneId=3\r\n"
    marked = ["setup.bat", "start_app.bat", "bootstrap.py", "Schuler.csv",
              "manual_review.json", ".venv/pyvenv.cfg"]
    for name in marked:
        with open(f"{root / name}:Zone.Identifier", "w", encoding="ascii") as stream:
            stream.write(zone)
    result = run_bat(root, "setup.bat", "--unattended")
    assert result.returncode == 0, result.stdout + result.stderr
    still_marked = {name for name in marked if os.path.exists(f"{root / name}:Zone.Identifier")}
    assert still_marked == {"Schuler.csv", "manual_review.json", ".venv/pyvenv.cfg"}


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


@pytest.fixture
def shortcut_project(tmp_path):
    """The real shortcut BAT; only the two target folders point into tmp."""

    root = tmp_path / "Shortcut app & (test)! O'Brien"
    root.mkdir()
    (root / "start_app.bat").write_text("@exit /b 0\r\n", encoding="ascii")
    shutil.copy2(ROOT / "app_icon.ico", root / "app_icon.ico")
    text = (ROOT / "create_desktop_shortcut.bat").read_text(encoding="ascii")
    for folder, variable in (("DesktopDirectory", "TEST_DESKTOP"), ("Programs", "TEST_START_MENU")):
        original = f"[Environment]::GetFolderPath('{folder}')"
        assert text.count(original) == 1
        text = text.replace(original, f"$env:{variable}")
    (root / "create_desktop_shortcut.bat").write_text(text, encoding="ascii", newline="")
    desktop, start_menu = tmp_path / "desktop", tmp_path / "start menu"
    desktop.mkdir()
    start_menu.mkdir()
    env = dict(os.environ, TEST_DESKTOP=str(desktop), TEST_START_MENU=str(start_menu))
    return root, desktop, start_menu, env


def read_shortcut(path):
    verify = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:TEST_LNK); "
        "@($s.TargetPath, $s.WorkingDirectory, $s.IconLocation) | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", verify],
        capture_output=True, text=True, env=dict(os.environ, TEST_LNK=str(path)), timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@WINDOWS
def test_shortcuts_on_desktop_and_start_menu_use_app_icon(shortcut_project):
    root, desktop, start_menu, env = shortcut_project
    result = run_bat(root, "create_desktop_shortcut.bat", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    for folder in (desktop, start_menu):
        target, workdir, icon = read_shortcut(folder / "PDF Datei Umbenenner.lnk")
        assert Path(target) == root / "start_app.bat"
        assert Path(workdir) == root
        assert icon == f"{root / 'app_icon.ico'},0"
    # Started by double click it waits for a key; setup passes --no-pause.
    assert PAUSE_TEXT in result.stdout
    result = run_bat(root, "create_desktop_shortcut.bat", "--no-pause", env=env)
    assert result.returncode == 0 and PAUSE_TEXT not in result.stdout


@WINDOWS
def test_blocked_powershell_or_folder_gives_a_clear_message(shortcut_project, tmp_path):
    root, desktop, start_menu, env = shortcut_project
    # A policy-blocked PowerShell behaves like a missing one for the BAT.
    blocked = dict(env, SystemRoot=str(tmp_path / "no-windows"),
                   PATH=str(Path(os.environ["SystemRoot"]) / "System32"))
    result = run_bat(root, "create_desktop_shortcut.bat", "--no-pause", env=blocked)
    assert result.returncode != 0
    assert "Richtlinie gesperrt" in result.stdout and "Senden an" in result.stdout
    # COM works, but the desktop cannot be written.
    result = run_bat(root, "create_desktop_shortcut.bat", "--no-pause",
                     env=dict(env, TEST_DESKTOP=str(tmp_path / "missing" / "desktop")))
    assert result.returncode != 0
    assert "Verknuepfungen konnten nicht erstellt werden" in result.stdout


def run_python_installer(tmp_path, source, *args):
    url = source.resolve().as_uri() if isinstance(source, Path) else source
    target = tmp_path / "must-stay-empty"
    result = subprocess.run(
        [str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(ROOT / "install_python.ps1"), "-InstallerUrl", url,
         "-TargetDirectory", str(target), *args],
        capture_output=True, text=True, timeout=120,
    )
    assert not target.exists()
    return result


@WINDOWS
def test_python_installer_script_rejects_unverified_downloads(tmp_path):
    temp_before = set(Path(os.environ["TEMP"]).glob("pdf-umbenenner-python-*"))
    result = run_python_installer(tmp_path, tmp_path / "missing" / "python-3.14.8-amd64.exe")
    assert result.returncode == 70, result.stdout + result.stderr
    assert "PYTHON_DOWNLOAD_FAILED" in result.stderr

    unsigned = tmp_path / "unsigned.exe"
    unsigned.write_bytes(b"MZ not a signed installer")
    result = run_python_installer(tmp_path, unsigned)
    assert result.returncode == 71, result.stdout + result.stderr
    assert "PYTHON_SIGNATURE_INVALID" in result.stderr

    # Valid signature, but not from the Python Software Foundation.
    result = run_python_installer(tmp_path, POWERSHELL)
    assert result.returncode == 71, result.stdout + result.stderr
    assert "Microsoft" in result.stderr
    assert set(Path(os.environ["TEMP"]).glob("pdf-umbenenner-python-*")) == temp_before


@WINDOWS
def test_python_installer_script_accepts_python_software_foundation(tmp_path):
    # python.org's own python.exe carries the same publisher signature.
    signed = Path(getattr(sys, "_base_executable", sys.executable))
    result = run_python_installer(tmp_path, signed, "-VerifyOnly")
    if result.returncode == 71:
        pytest.skip("this Python was not installed from python.org: " + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CN=Python Software Foundation" in result.stdout
    assert "PYTHON_VERIFY_ONLY" in result.stdout
