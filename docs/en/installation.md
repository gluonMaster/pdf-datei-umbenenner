# Installation and operation

**English** · [Deutsch](../de/installation.md) · [Русский](../ru/installation.md) · [← README](../../README.md)

PDF Datei Umbenenner runs on ordinary Windows 10 and 11 office PCs. Installation needs no administrator rights: the program lives in its own folder with a private Python environment (`.venv`). Only Tesseract OCR is installed as a separate program.

- [Requirements](#requirements)
- [1. Choose a location](#1-choose-a-location)
- [2. Install Tesseract OCR](#2-install-tesseract-ocr)
- [3. Run setup.bat](#3-run-setupbat)
- [4. Daily start](#4-daily-start)
- [5. Update an installation](#5-update-an-installation)
- [Exit codes](#exit-codes)
- [Troubleshooting](#troubleshooting)

## Requirements

| Component | Details |
| --- | --- |
| Windows | 10 or 11. Automatic Python installation is offered on 64-bit Windows for Intel/AMD processors. |
| Internet | Only during installation (Python, Python packages, Tesseract). |
| Python | 3.10–3.15 with Tcl/Tk. If none is found, `setup.bat` installs Python 3.14 after asking. |
| Tesseract OCR | Version 5 with the German language data `deu` (`eng` is part of the standard installation, `osd` is recommended). |
| Student registry | Your `Schuler.csv` in the program folder (see the [user guide](user-guide.md#student-registry)). |

Without Tesseract the program starts and the registry and review tabs work, but processing — including dry runs — stays locked.

## 1. Choose a location

1. **Unblock the ZIP before extracting it:** right-click the downloaded file → *Properties* → tick *Unblock* → *OK*. Otherwise Windows marks every extracted file as "downloaded from the internet" and shows a security warning for each BAT file. `setup.bat` removes this mark from the program files itself, but the very first start of `setup.bat` would still ask for confirmation.
2. **Recommended folder:** a local folder of the user, for example `C:\Users\<Name>\PDF-Datei-Umbenenner` (`%USERPROFILE%\PDF-Datei-Umbenenner`).
3. **No network paths.** A folder like `\\server\share\…` cannot be used as the working directory of `cmd.exe`; all launchers stop immediately with exit code `51` and suggest copying the program to the PC.
4. **No OneDrive folders** (this includes a desktop redirected to OneDrive). Synchronising the thousands of files in `.venv` causes locked files and failed package installations. `setup.bat` detects a location inside `%OneDrive%`, `%OneDriveCommercial%` or `%OneDriveConsumer%`, explains the problem and asks whether to continue; answering *no* ends setup with code `52` before anything is installed.
5. Put your `Schuler.csv` into the program folder. Without it, the check at the end of setup fails with code `24`. To try the program first, you can start with a copy of [`demo/Schuler.csv`](../../demo/Schuler.csv).

## 2. Install Tesseract OCR

The [Tesseract documentation](https://tesseract-ocr.github.io/tessdoc/Installation.html) refers Windows users to the installer of the UB Mannheim:

1. Open the [UB Mannheim page](https://github.com/UB-Mannheim/tesseract/wiki) and download the current 64-bit installer `tesseract-ocr-w64-setup-….exe`.
2. Run it and keep the suggested installation folder.
3. In the component selection, open *Additional language data* and tick **German**. English and `osd` are part of the standard selection.
4. If Tesseract is already installed without German: run the installer again and add *German*, or copy `deu.traineddata` from [tessdata](https://github.com/tesseract-ocr/tessdata) into the `tessdata` folder next to `tesseract.exe`.

The program looks for `tesseract.exe` in `TESSERACT_CMD`, on `PATH`, in `%ProgramFiles%\Tesseract-OCR`, `%ProgramFiles(x86)%\Tesseract-OCR`, `%LOCALAPPDATA%\Programs\Tesseract-OCR` (installation for the current user only) and `%LOCALAPPDATA%\Tesseract-OCR`. For any other folder, set the environment variable `TESSERACT_CMD` to the full path of `tesseract.exe`. Without `osd`, scans lying on their side are recognised less reliably; the check only shows a warning.

`setup.bat` does not run the Tesseract installer by itself: the official documentation only describes a manual installation, and the installer's signature cannot be verified the way the Python and Microsoft packages are.

## 3. Run setup.bat

Double-click `setup.bat`. The interactive setup:

1. **Finds Python** in this order: `py -3.14`, `%LOCALAPPDATA%\Programs\Python\Python314\python.exe`, `py -3`, `python` on `PATH`. A candidate counts only if it really runs and reports version 3.10–3.15 — the Microsoft Store placeholder `WindowsApps\python.exe` (exit code 9009) is ignored. If only a newer Python exists, setup says so and offers the pinned version.
2. **Installs Python if necessary** (only after an explicit *j*): see [Automatic Python installation](#automatic-python-installation).
3. **Creates the private environment** `.venv` (no administrator rights needed).
4. **Installs the packages** from `requirements.txt` (`pdfplumber`, `PyMuPDF`, `Pillow`) as pre-built wheels only (`--only-binary=:all:`) — a source build would fail on office PCs without a compiler anyway. It then checks the packages, the application imports, `subjects_config.json` and `Schuler.csv`. Damaged packages are reinstalled once without the cache. Only after a successful check does setup write the stamp `.venv\.requirements.sha256`.
5. **Checks Tesseract** with `deu` and `eng`. If something is missing, it shows a German step-by-step guide, opens the download page after your consent, waits for *Enter* and checks again. If Tesseract is still not ready, setup ends with code `31`, `32` or `33` and the message `SETUP UNVOLLSTAENDIG: VERARBEITUNG NICHT VERFUEGBAR`.
6. **Offers comfort functions:** shortcuts on the desktop and in the Start menu, and starting the program.
7. **Waits for a key at the end** when it was started by double-click or shortcut, so that the result and any errors can be read. Started from an open console, it does not wait.

If PyMuPDF cannot load because the Microsoft Visual C++ runtime is missing (code `34`), the packages are first reinstalled once. If that does not help, the interactive setup separately asks whether to install the Microsoft VC++ Redistributable; its digital signature is checked, and the console must have been started as administrator (no elevation is requested). Declining or a failure ends with code `34`.

### Automatic Python installation

When no suitable Python is found, setup offers to install it. After your consent, `install_python.ps1`:

- downloads the pinned installer **Python 3.14.8 (64-bit)** from python.org: `https://www.python.org/ftp/python/3.14.8/python-3.14.8-amd64.exe`;
- checks the Authenticode signature before running it: status *Valid* and publisher `CN=Python Software Foundation`. Otherwise the file is deleted and never executed;
- installs silently for the current user into `%LOCALAPPDATA%\Programs\Python\Python314` with Tcl/Tk: `/quiet InstallAllUsers=0 PrependPath=0 Include_launcher=0 AssociateFiles=0 Shortcuts=0 Include_tcltk=1 Include_pip=1 Include_doc=0 Include_test=0`. No administrator rights are needed and `PATH` stays unchanged. The installer log goes to `.launcher-logs\python-installer.log`;
- after that, setup finds the interpreter at this path and continues.

The `py` launcher is deliberately not installed: its per-user variant would put its folder at the front of the user's `PATH` and hide the `py` command of the new [Python install manager](https://docs.python.org/3/using/windows.html#python-install-manager). The classic installer exists for Python 3.14 and 3.15; from 3.16 on, python.org only ships the install manager. Implicit runtime downloads by the install manager are disabled in all launchers (`PYTHON_MANAGER_AUTOMATIC_INSTALL=false`).

Declining, a download error, an invalid signature or a failed installation leads back to the manual instructions, the offer to open the [Python download page](https://www.python.org/downloads/windows/) and exit code `10`. On 32-bit Windows and on ARM64, setup explains that automatic installation is not available there. `--unattended` and `--core-only` never install Python.

The required wheels exist for Python 3.10, 3.14 and 3.15 on `win_amd64`, and for 3.14 also on `win32` and `win_arm64` (verified with `pip download --only-binary=:all:`).

### Modes without questions

```cmd
setup.bat --unattended
setup.bat --core-only
setup.bat --no-pause
```

- `--unattended` — no questions, no system installations (Python, Tesseract, VC++ runtime) and no waiting for a key. Only Python packages are installed into `.venv`. Exit code `0` only if packages, Tesseract and `deu` are ready; otherwise a specific non-zero code.
- `--core-only` — Python packages only; Tesseract is not checked, and the summary reminds you that processing needs it. Exit code `0` when the packages are ready.
- `--no-pause` — for automation: do not wait for a key at the end (can be combined with the other options).

## 4. Daily start

Use the shortcut **PDF Datei Umbenenner** or `start_app.bat`. The launcher:

- uses only the private `.venv`, compares the `requirements.txt` stamp and checks packages and data;
- repairs the environment only for Python package problems: after a changed `requirements.txt` one normal installation and a new stamp; for a damaged package (`20`, `22`, `35`) one forced reinstallation with a new check. Problems with `subjects_config.json`, `Schuler.csv` or tkinter/Tcl never start `pip`; a missing VC++ runtime (`34`) points to `setup.bat`;
- never starts `pip` because of a missing Tesseract: it shows a warning, the program opens, and the *Verarbeitung* tab explains that processing is locked. After installing Tesseract no restart is needed;
- starts the program as a separate process via `.venv\Scripts\pythonw.exe` and closes immediately — the console is only visible during checks and repairs, and closing it no longer ends the program. Program output and start errors are written to `.launcher-logs\gui.log`; if the program cannot start, a message box appears as well;
- waits for a key if it fails while started by double-click or shortcut (`--no-pause` disables this).

A second start while the window is open (for example a second double-click on the shortcut) does not open a second instance working on the same files: the message *PDF Datei Umbenenner ist bereits geöffnet* appears, and after *OK* the open window is restored and brought to the front. The lock is a named Windows mutex for the program folder, so a crash leaves nothing to clean up; copies of the program in other folders do not block each other.

The window opens at 1200×760 in the centre of the screen. If it does not fit — for example on 1366×768 or on 1920×1080 with 150 % scaling (about 1280×720 logical pixels) — it opens maximised, the decorative heading is hidden and the student list in the *Prüfung* tab shrinks so that all fields and buttons stay visible. The program does not declare high-DPI support: at 125/150 % Windows scales the whole window (slightly softer text, same layout).

Check the launcher without opening the program (code `0` if the program can start; missing OCR is only a warning):

```cmd
start_app.bat --health-only
```

`quick_start.bat` installs and repairs nothing. It only checks that `.venv` exists (otherwise code `2`) and that the required packages import (`bootstrap.py check-packages`, about 0.3 s; otherwise a clear error and code `20`, `34` or `35`), then starts the program the same way. If the environment is damaged, use `start_app.bat`.

`create_desktop_shortcut.bat` (offered by setup) creates the shortcut **PDF Datei Umbenenner** on the desktop — following a desktop redirected to OneDrive — and in the user's Start menu. It points to `start_app.bat`, keeps the program folder as working directory and uses the program icon. If PowerShell or Windows Script Host is blocked by policy, or the folder is not writable, it explains the manual way (*Send to → Desktop*).

## 5. Update an installation

1. Close the program.
2. Replace the program files (`*.py`, `*.bat`, `*.ps1`, `requirements*.txt`, `app_icon.*`, `README*.md`, `docs\`). Do not overwrite your data: `Schuler.csv`, `Lehrer.csv`, `student_aliases.json`, `subjects_aliases.json` and the `manual_review.json` files next to your scans. If you changed the subject list in `subjects_config.json`, keep your file and add new entries manually or with *Neues Fach übernehmen* in the review tab.
3. Start the program as usual. A changed `requirements.txt` is detected by its stamp and the packages are installed once. Launchers and setup never change CSV, JSON, the review queue or scans.
4. If Tesseract with `deu` is not installed yet, install it and check with `setup.bat` or `.venv\Scripts\python.exe bootstrap.py check-ocr`.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Ready / success |
| `2` | `quick_start.bat`: `.venv` missing |
| `10` | No supported Python found (and none installed) |
| `20` | Required package missing or damaged (`pdfplumber`, `PyMuPDF`, `Pillow`) |
| `21` | tkinter/Tcl not available |
| `22` | Application import failed |
| `23` | `subjects_config.json` unreadable — the file is not changed, packages are not reinstalled |
| `24` | `Schuler.csv` missing or unreadable — the file is not changed, packages are not reinstalled |
| `31` | Tesseract not found |
| `32` | Language data `deu` or `eng` missing |
| `33` | OCR check failed |
| `34` | PyMuPDF cannot load: Windows runtime/DLL (Microsoft VC++) |
| `35` | Other PyMuPDF import error |
| `40` | `requirements.txt` changed or stamp missing (internal) |
| `50` | Bootstrap, environment or diagnostics error, including a failed start of the program process |
| `51` | Program located on a network (UNC) path |
| `52` | Setup cancelled because of a OneDrive location |
| `64` | Invalid launcher argument |

Setup returns `31`–`33` when the packages are ready but OCR is not; `start_app.bat` and `quick_start.bat` start the program in that case. `pip` errors are passed through unchanged. The launchers no longer pass on the exit code of the program itself, because it runs as a separate process. The internal codes of `install_python.ps1` (`70` download, `71` signature, `72` installer, `73` no `python.exe` afterwards) are only written to the setup log; setup itself returns `10`. Code `30` (formerly "optional PyMuPDF missing") is retired and will not be reused.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Windows shows a security warning for every BAT file | The ZIP was not unblocked. Run `setup.bat` once (it removes the mark) or unblock the ZIP and extract it again. |
| Setup says Python is missing | Answer *j* to install Python 3.14 automatically, or install Python from python.org with *tcl/tk and IDLE* and run setup again. |
| *OCR nicht verfügbar – Verarbeitung gesperrt* | Install Tesseract with German data (section 2), then check with `setup.bat`. |
| Code `34` (Visual C++) | Run `setup.bat` without options in a console started as administrator and accept the VC++ runtime. |
| The program does not start | Read `.launcher-logs\gui.log`; for launcher problems `.launcher-logs\start_app.log` or `setup.log`. |
| The environment is broken | Close the program and run `start_app.bat`. An unusable `.venv` is kept as `.bootstrap\venv-backup-<id>` and a new one is created. For a complete reset delete only `.venv` and run `setup.bat` again — Tesseract stays installed. |
| `manual_review.json` is damaged | The program refuses to overwrite it. Close the program, copy the file and the `manual_check` folder to a safe place, match each PDF to its queue entry, then repair the JSON or replace it with `{"review_items": []}`. |

All diagnostic logs are UTF-8 files in `.launcher-logs\`: `setup.log`, `start_app.log`, `gui.log` and `python-installer.log`.
