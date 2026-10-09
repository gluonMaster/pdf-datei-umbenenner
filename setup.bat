@echo off
setlocal EnableExtensions DisableDelayedExpansion
title PDF Datei Umbenenner - Setup

set "PROJECT_DIR=%~dp0"
set "SCRIPT_NAME=%~nx0"
set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"
set "VENV_PYTHONW=%~dp0.venv\Scripts\pythonw.exe"
set "LOG_DIR=%~dp0.launcher-logs"
set "LOG_FILE=%~dp0.launcher-logs\setup.log"
set "SETUP_MODE=interactive"
set "PAUSE_AT_END=auto"
set "CORE_REPAIR_ATTEMPTED=0"
set "VC_RUNTIME_ATTEMPTED=0"
set "TESSERACT_GUIDE_OFFERED=0"
set "PYTHON_INSTALL_ATTEMPTED=0"
set "OCR_STATE=unchecked"
set "PYTHONIOENCODING=utf-8"
rem The new Python install manager must not download a runtime implicitly.
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"
rem Python offered by install_python.ps1; wheels of all packages exist for it.
set "PINNED_PYTHON_TAG=3.14"
set "PINNED_PYTHON_DIR=%LOCALAPPDATA%\Programs\Python\Python314"

:parse_arguments
if "%~1"=="" goto arguments_done
if /i "%~1"=="--unattended" goto select_unattended
if /i "%~1"=="--core-only" goto select_core_only
if /i "%~1"=="--no-pause" goto select_no_pause
if /i "%~1"=="--help" goto show_usage
if /i "%~1"=="-h" goto show_usage
echo FEHLER: Unbekannter Parameter: %~1
echo Verwenden Sie setup.bat --help fuer die Aufrufhilfe.
exit /b 64

:select_unattended
set "SETUP_MODE=unattended"
set "PAUSE_AT_END=0"
shift
goto parse_arguments

:select_core_only
set "SETUP_MODE=core-only"
shift
goto parse_arguments

:select_no_pause
set "PAUSE_AT_END=0"
shift
goto parse_arguments

:show_usage
echo Aufruf:
echo   setup.bat                 Interaktiv: Python bei Bedarf nach Rueckfrage installieren,
echo                             Python-Pakete installieren, Tesseract OCR mit deutschen
echo                             Sprachdaten pruefen, bei Bedarf Anleitung.
echo   setup.bat --unattended    Ohne Rueckfragen, ohne Systeminstallation und ohne Warten
echo                             auf eine Taste. Erfolg nur, wenn Pakete, Tesseract und
echo                             Sprachdaten deu bereit sind.
echo   setup.bat --core-only     Nur Python-Pakete; Tesseract wird nicht geprueft.
echo                             Die Verarbeitung von PDF-Dateien benoetigt ihn trotzdem.
echo   setup.bat --no-pause      Am Ende nicht auf eine Taste warten (Automatisierung).
echo.
echo Rueckgabecodes: 0 bereit; 10 Python fehlt; 31 Tesseract fehlt; 32 Sprachdaten deu
echo oder eng fehlen; 33 OCR-Pruefung fehlgeschlagen; 34 Microsoft VC++ Laufzeit fuer
echo PyMuPDF fehlt; 51 Netzwerkpfad; 52 Abbruch wegen OneDrive-Ordner.
echo Alle Codes stehen in README.md.
exit /b 0

:arguments_done
if "%PAUSE_AT_END%"=="auto" call :detect_double_click

rem cmd.exe cannot use a UNC path as the current directory.
if "%PROJECT_DIR:~0,2%"=="\\" goto unc_location

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo FEHLER: Das Anwendungsverzeichnis kann nicht geoeffnet werden.
    set "EXIT_CODE=50"
    goto finish
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
if not exist "%LOG_DIR%" (
    echo FEHLER: Diagnoseverzeichnis kann nicht erstellt werden: "%LOG_DIR%"
    set "EXIT_CODE=50"
    goto finish
)

>"%LOG_FILE%" echo PDF Datei Umbenenner setup diagnostics
>>"%LOG_FILE%" echo Project: "%PROJECT_DIR%"
>>"%LOG_FILE%" echo Mode: %SETUP_MODE%

echo ================================================================
echo   PDF Datei Umbenenner - Setup
echo ================================================================
echo.
echo Dieses Setup erstellt ein privates .venv im Anwendungsverzeichnis und
echo installiert die Python-Pakete; dafuer sind keine Administratorrechte noetig.
echo Fuer die Verarbeitung wird zusaetzlich Tesseract OCR mit deutschen
echo Sprachdaten benoetigt. Das Setup prueft ihn ausser mit --core-only und
echo erklaert die Einrichtung.
echo Diagnoselog: "%LOG_FILE%"
echo.

call :prepare_program_files
if "%PROGRAM_LOCATION%"=="onedrive" goto onedrive_location

:location_checked
echo [1/5] Lokales Python 3.10 oder neuer wird geprueft...
if not exist "%PROJECT_DIR%bootstrap.py" (
    set "FAIL_CODE=50"
    echo FEHLER: bootstrap.py fehlt.
    goto critical_failure
)
if not exist "%VENV_PYTHON%" goto create_venv
"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-python >>"%LOG_FILE%" 2>&1
if not "%ERRORLEVEL%"=="0" goto create_venv
echo OK: Das vorhandene .venv verwendet ein unterstuetztes Python.
goto install_core

:create_venv
echo [2/5] Suche Basis-Python und erstelle .venv...
call :find_supported_python
if errorlevel 1 goto python_unavailable

:create_venv_with_base_python
>>"%LOG_FILE%" echo Base Python: "%BASE_PYTHON%" %BASE_PYTHON_ARGS%
"%BASE_PYTHON%" %BASE_PYTHON_ARGS% "%PROJECT_DIR%bootstrap.py" prepare-venv >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto venv_failed
goto install_core

:venv_failed
set "FAIL_CODE=%ERRORLEVEL%"
echo FEHLER: Das lokale .venv konnte nicht erstellt werden.
goto critical_failure

:install_core
if not exist "%VENV_PYTHON%" (
    set "FAIL_CODE=50"
    echo FEHLER: Nach der Erstellung fehlt Python im .venv.
    goto critical_failure
)

echo [3/5] Erforderliche Python-Pakete werden installiert...
"%VENV_PYTHON%" -m ensurepip --upgrade >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto ensurepip_failed
rem Only wheels: a source build would fail on office PCs without a compiler.
"%VENV_PYTHON%" -m pip install --disable-pip-version-check --only-binary=:all: -r "%PROJECT_DIR%requirements.txt" >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto pip_failed

:run_core_check
echo Paket- und Kernpruefung wird ausgefuehrt...
"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-core >>"%LOG_FILE%" 2>&1
set "CORE_CODE=%ERRORLEVEL%"
if "%CORE_CODE%"=="0" goto stamp_core
if "%CORE_CODE%"=="20" goto repair_core_packages
if "%CORE_CODE%"=="22" goto repair_core_packages
if "%CORE_CODE%"=="34" goto repair_core_packages
if "%CORE_CODE%"=="35" goto repair_core_packages
goto core_failed

:repair_core_packages
if "%CORE_REPAIR_ATTEMPTED%"=="1" goto core_repair_exhausted
set "CORE_REPAIR_ATTEMPTED=1"
echo Beschaedigte Pakete werden einmal ohne Paket-Cache neu installiert...
"%VENV_PYTHON%" -m pip install --disable-pip-version-check --only-binary=:all: --force-reinstall --no-cache-dir -r "%PROJECT_DIR%requirements.txt" >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto pip_failed
goto run_core_check

:core_repair_exhausted
if "%CORE_CODE%"=="34" goto handle_pymupdf_runtime
goto core_failed

:handle_pymupdf_runtime
echo PyMuPDF benoetigt die aktuelle Microsoft Visual C++ Windows-Laufzeit.
if /i not "%SETUP_MODE%"=="interactive" goto pymupdf_runtime_missing
if "%VC_RUNTIME_ATTEMPTED%"=="1" goto pymupdf_runtime_missing
echo Das Microsoft-Paket wird nur nach gueltiger digitaler Signatur ausgefuehrt.
echo Dafuer muss diese Konsole als Administrator gestartet sein.
call :ask_yes_no "VC++ Redistributable automatisch installieren?"
if errorlevel 1 goto pymupdf_runtime_missing

set "VC_RUNTIME_ATTEMPTED=1"
call :find_powershell
if not defined POWERSHELL_COMMAND goto powershell_unavailable
echo Microsoft Visual C++ Redistributable wird sicher installiert...
"%POWERSHELL_COMMAND%" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%PROJECT_DIR%install_vc_runtime.ps1" >>"%LOG_FILE%" 2>&1
set "VC_RUNTIME_CODE=%ERRORLEVEL%"
if not "%VC_RUNTIME_CODE%"=="0" goto vc_runtime_failed
goto run_core_check

:powershell_unavailable
echo FEHLER: PowerShell ist fuer die sichere Windows-Laufzeitinstallation erforderlich.
goto pymupdf_runtime_missing

:vc_runtime_failed
echo FEHLER: Die Microsoft VC++ Laufzeit konnte nicht automatisch eingerichtet werden. Code: %VC_RUNTIME_CODE%.
goto pymupdf_runtime_missing

:pymupdf_runtime_missing
set "FAIL_CODE=34"
echo FEHLER: PyMuPDF kann ohne die Microsoft VC++ Laufzeit nicht geladen werden.
echo Abhilfe: setup.bat ohne Parameter in einer Administrator-Konsole ausfuehren
echo oder das Microsoft Visual C++ Redistributable installieren.
goto critical_failure

:core_failed
set "FAIL_CODE=%CORE_CODE%"
echo FEHLER: Die erforderliche Kernpruefung ist fehlgeschlagen. Code: %CORE_CODE%.
if "%CORE_CODE%"=="21" echo tkinter/Tcl fehlt: Python mit Tcl/Tk-Unterstuetzung reparieren.
if "%CORE_CODE%"=="23" echo subjects_config.json ist unlesbar. Die Datei wird nicht veraendert.
if "%CORE_CODE%"=="24" echo Schuler.csv fehlt oder ist unlesbar. Die Datei wird nicht veraendert.
goto critical_failure

:stamp_core
"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" write-requirements-stamp >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto stamp_failed
echo OK: Python-Pakete und Kernpruefung sind bereit.

if /i "%SETUP_MODE%"=="core-only" goto skip_ocr_check
echo [4/5] Tesseract OCR mit deutschen Sprachdaten wird geprueft...

:run_ocr_check
"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-ocr >>"%LOG_FILE%" 2>&1
set "OCR_CHECK_CODE=%ERRORLEVEL%"
if "%OCR_CHECK_CODE%"=="0" goto ocr_ready
call :describe_ocr_problem
if /i not "%SETUP_MODE%"=="interactive" goto ocr_missing
if "%TESSERACT_GUIDE_OFFERED%"=="1" goto ocr_missing
goto offer_tesseract_setup

:skip_ocr_check
set "OCR_STATE=skipped"
echo [4/5] Tesseract wird im Modus --core-only nicht geprueft.
echo HINWEIS: Die Verarbeitung von PDF-Dateien benoetigt Tesseract OCR mit deutschen
echo Sprachdaten deu. Ohne Tesseract startet die Anwendung, die Verarbeitung bleibt
echo aber gesperrt. Pruefung und Anleitung: setup.bat ohne Parameter.
goto optional_actions

:ocr_ready
set "OCR_STATE=ready"
echo OK: Tesseract OCR mit deutschen Sprachdaten ist bereit.
goto optional_actions

:offer_tesseract_setup
set "TESSERACT_GUIDE_OFFERED=1"
echo.
echo Einrichtung mit dem offiziellen Windows-Installer der UB Mannheim:
echo   1. Download-Seite oeffnen: https://github.com/UB-Mannheim/tesseract/wiki
echo      und den aktuellen 64-Bit-Installer tesseract-ocr-w64-setup herunterladen.
echo   2. Installer starten und den vorgeschlagenen Installationsordner beibehalten.
echo   3. In der Komponentenauswahl unter "Additional language data" den Eintrag
echo      "German" ankreuzen. Englisch und osd sind standardmaessig enthalten.
echo   4. Ist Tesseract schon installiert und fehlt nur deu: Installer erneut starten
echo      und German nachwaehlen oder deu.traineddata aus
echo      https://github.com/tesseract-ocr/tessdata in den Ordner tessdata kopieren.
echo   5. Bei einem anderen Installationsordner die Umgebungsvariable TESSERACT_CMD
echo      auf den vollstaendigen Pfad von tesseract.exe setzen.
echo Die Installation fuehren Sie selbst aus; das Setup installiert nichts still.
call :ask_yes_no "Download-Seite jetzt im Browser oeffnen?"
if errorlevel 1 goto ocr_missing

"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" open-tesseract-download >>"%LOG_FILE%" 2>&1
if errorlevel 1 echo Die Seite konnte nicht geoeffnet werden. Bitte die Adresse oben manuell aufrufen.
set "OCR_CONTINUE="
set /p "OCR_CONTINUE=Nach Abschluss oder Abbruch der Installation ENTER druecken: "
echo Tesseract wird erneut geprueft...
goto run_ocr_check

:ocr_missing
set "OCR_STATE=missing"
set "FAIL_CODE=%OCR_CHECK_CODE%"
echo Die Verarbeitung von PDF-Dateien ist nicht verfuegbar.
goto optional_actions

:optional_actions
echo [5/5] Optionale Komfortfunktionen...
if /i not "%SETUP_MODE%"=="interactive" goto setup_finished
call :ask_yes_no "Verknuepfungen auf dem Desktop und im Startmenue erstellen?"
if not errorlevel 1 call "%PROJECT_DIR%create_desktop_shortcut.bat" --no-pause

call :ask_yes_no "Anwendung jetzt starten?"
if errorlevel 1 goto setup_finished
call :start_application
if errorlevel 1 goto test_failed
echo Die Anwendung wird in einem eigenen Fenster geoeffnet.
goto setup_finished

:ensurepip_failed
set "FAIL_CODE=%ERRORLEVEL%"
echo FEHLER: pip ist im lokalen .venv nicht verfuegbar.
goto critical_failure

:pip_failed
set "FAIL_CODE=%ERRORLEVEL%"
echo FEHLER: Erforderliche Abhaengigkeiten konnten nicht installiert werden.
echo Internetverbindung pruefen; Details stehen im Diagnoselog.
goto critical_failure

:stamp_failed
set "FAIL_CODE=%ERRORLEVEL%"
echo FEHLER: Der Requirements-Stand konnte nicht gespeichert werden.
goto critical_failure

:test_failed
set "FAIL_CODE=50"
echo FEHLER: Die Anwendung konnte nicht gestartet werden.
goto critical_failure

:python_unavailable
set "FAIL_CODE=10"
if "%PYTHON_UNSUITABLE%"=="1" goto python_unsuitable
echo FEHLER: Python 3.10 oder neuer wurde nicht gefunden.
goto offer_python_install

:python_unsuitable
echo FEHLER: Das vorhandene Python hat eine nicht unterstuetzte Version.
echo Unterstuetzt wird Python 3.10 bis 3.15, empfohlen ist Python %PINNED_PYTHON_TAG%.

:offer_python_install
if /i not "%SETUP_MODE%"=="interactive" goto python_manual_instructions
if "%PYTHON_INSTALL_ATTEMPTED%"=="1" goto python_manual_instructions
set "OS_ARCHITECTURE=%PROCESSOR_ARCHITECTURE%"
if defined PROCESSOR_ARCHITEW6432 set "OS_ARCHITECTURE=%PROCESSOR_ARCHITEW6432%"
if /i "%OS_ARCHITECTURE%"=="AMD64" goto ask_python_install
echo Die automatische Python-Installation unterstuetzt nur 64-Bit-Windows auf
echo Intel- oder AMD-Prozessoren. Dieses Windows meldet: %OS_ARCHITECTURE%.
goto python_manual_instructions

:ask_python_install
echo.
echo Das Setup kann Python %PINNED_PYTHON_TAG% 64-Bit von python.org installieren:
echo   - nur fuer Ihr Benutzerkonto, ohne Administratorrechte;
echo   - mit Tcl/Tk fuer die Programmoberflaeche;
echo   - PATH und andere Programme bleiben unveraendert.
echo Der Download hat etwa 30 MB. Der Installer wird nur ausgefuehrt, wenn seine
echo digitale Signatur gueltig ist und von der Python Software Foundation stammt.
call :ask_yes_no "Python jetzt herunterladen und installieren?"
if errorlevel 1 goto python_manual_instructions
set "PYTHON_INSTALL_ATTEMPTED=1"
call :find_powershell
if defined POWERSHELL_COMMAND goto run_python_installer
echo FEHLER: PowerShell wurde nicht gefunden; Python kann nicht automatisch installiert werden.
goto python_manual_instructions

:run_python_installer
echo Python wird heruntergeladen und installiert. Das kann einige Minuten dauern...
"%POWERSHELL_COMMAND%" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%PROJECT_DIR%install_python.ps1" -TargetDirectory "%PINNED_PYTHON_DIR%" -InstallerLog "%LOG_DIR%\python-installer.log" >>"%LOG_FILE%" 2>&1
set "PYTHON_INSTALL_CODE=%ERRORLEVEL%"
>>"%LOG_FILE%" echo install_python.ps1 exit code: %PYTHON_INSTALL_CODE%
if not "%PYTHON_INSTALL_CODE%"=="0" goto python_install_failed
call :find_supported_python
if errorlevel 1 goto python_missing_after_install
echo OK: Python %PINNED_PYTHON_TAG% wurde fuer Ihr Benutzerkonto installiert.
goto create_venv_with_base_python

:python_missing_after_install
echo FEHLER: Python wurde installiert, ist aber nicht nutzbar.
goto python_manual_instructions

:python_install_failed
if "%PYTHON_INSTALL_CODE%"=="70" goto python_download_failed
if "%PYTHON_INSTALL_CODE%"=="71" goto python_signature_invalid
echo FEHLER: Die automatische Python-Installation ist fehlgeschlagen. Code: %PYTHON_INSTALL_CODE%.
goto python_manual_instructions

:python_download_failed
echo FEHLER: Der Python-Installer konnte nicht heruntergeladen werden.
echo Internetverbindung oder Proxy pruefen.
goto python_manual_instructions

:python_signature_invalid
echo FEHLER: Die digitale Signatur des Python-Installers ist ungueltig.
echo Der Installer wurde nicht ausgefuehrt und geloescht.
goto python_manual_instructions

:python_manual_instructions
echo Python manuell installieren: https://www.python.org/downloads/windows/
echo Dabei "tcl/tk and IDLE" auswaehlen und danach setup.bat erneut starten.
if /i not "%SETUP_MODE%"=="interactive" goto critical_failure
echo Ohne Ihre Zustimmung wird keine Systemsoftware installiert.
call :ask_yes_no "Offizielle Python-Downloadseite oeffnen?"
if not errorlevel 1 start "" "https://www.python.org/downloads/windows/"
goto critical_failure

:unc_location
echo FEHLER: Das Programm liegt auf einem Netzwerkpfad:
echo   "%PROJECT_DIR%"
echo Von dort kann es nicht eingerichtet und gestartet werden. Bitte den
echo Programmordner auf diesen Computer kopieren, zum Beispiel nach
echo   "%USERPROFILE%\PDF-Datei-Umbenenner"
echo und setup.bat dort erneut starten.
set "EXIT_CODE=51"
goto finish

:onedrive_location
echo.
echo WARNUNG: Das Programm liegt in einem OneDrive-Ordner:
echo   "%PROJECT_DIR%"
echo OneDrive synchronisiert die mehreren tausend Dateien des lokalen .venv.
echo Das fuehrt zu gesperrten Dateien und Fehlern bei der Paketinstallation.
echo Empfohlen: den Programmordner nach
echo   "%USERPROFILE%\PDF-Datei-Umbenenner"
echo kopieren und setup.bat dort starten.
>>"%LOG_FILE%" echo Location: OneDrive
if /i not "%SETUP_MODE%"=="interactive" goto location_checked
call :ask_yes_no "Trotzdem hier fortfahren?"
if not errorlevel 1 goto location_checked
echo Setup abgebrochen. Es wurde nichts installiert.
set "EXIT_CODE=52"
goto finish

:setup_finished
if "%OCR_STATE%"=="missing" goto setup_incomplete
echo.
echo ================================================================
echo   SETUP ABGESCHLOSSEN
echo ================================================================
if "%OCR_STATE%"=="ready" echo Python-Pakete, Kernpruefung und Tesseract mit deutschen Sprachdaten sind bereit.
if "%OCR_STATE%"=="skipped" echo Python-Pakete und Kernpruefung sind bereit. Tesseract wurde nicht geprueft;
if "%OCR_STATE%"=="skipped" echo die Verarbeitung benoetigt Tesseract OCR mit deutschen Sprachdaten deu.
echo Fuer den Alltag die Verknuepfung "PDF Datei Umbenenner" oder start_app.bat verwenden.
echo Diagnoselog: "%LOG_FILE%"
set "EXIT_CODE=0"
goto finish

:setup_incomplete
echo.
echo ================================================================
echo   SETUP UNVOLLSTAENDIG: VERARBEITUNG NICHT VERFUEGBAR
echo ================================================================
echo Die Python-Pakete sind installiert. Die Anwendung startet; Schuelerverzeichnis
echo und Pruefliste sind nutzbar. PDF-Dateien koennen erst verarbeitet werden, wenn
echo Tesseract OCR mit deutschen Sprachdaten deu eingerichtet ist. Danach setup.bat
echo erneut ausfuehren.
echo Rueckgabecode: %FAIL_CODE%.
echo Diagnoselog: "%LOG_FILE%"
set "EXIT_CODE=%FAIL_CODE%"
goto finish

:critical_failure
if not defined FAIL_CODE set "FAIL_CODE=1"
echo.
echo SETUP FEHLGESCHLAGEN. Rueckgabecode: %FAIL_CODE%.
echo Diagnoselog: "%LOG_FILE%"
set "EXIT_CODE=%FAIL_CODE%"
goto finish

:finish
if "%PAUSE_AT_END%"=="1" (
    echo.
    echo Zum Schliessen dieses Fensters eine beliebige Taste druecken...
    pause >nul
)
exit /b %EXIT_CODE%

:describe_ocr_problem
if "%OCR_CHECK_CODE%"=="31" echo FEHLER: Tesseract OCR wurde nicht gefunden.
if "%OCR_CHECK_CODE%"=="31" exit /b 0
if "%OCR_CHECK_CODE%"=="32" echo FEHLER: Tesseract ist installiert, aber die Sprachdaten deu oder eng fehlen.
if "%OCR_CHECK_CODE%"=="32" exit /b 0
echo FEHLER: Die OCR-Pruefung ist fehlgeschlagen. Code: %OCR_CHECK_CODE%. Details im Diagnoselog.
exit /b 0

:ask_yes_no
rem Exit code 0 only for an explicit yes; Enter or end of input means no.
set "YES_NO_ANSWER="
set /p "YES_NO_ANSWER=%~1 [j/N]: "
if /i "%YES_NO_ANSWER%"=="J" exit /b 0
if /i "%YES_NO_ANSWER%"=="JA" exit /b 0
if /i "%YES_NO_ANSWER%"=="Y" exit /b 0
if /i "%YES_NO_ANSWER%"=="YES" exit /b 0
exit /b 1

:detect_double_click
rem Explorer and shortcuts run cmd.exe /c with this script; that window closes
rem on exit. A console the user opened has no script name in its command line.
set "PAUSE_AT_END=0"
setlocal EnableDelayedExpansion
set "LAUNCH_LINE=!CMDCMDLINE!"
set "LAUNCH_LINE_WITHOUT_SCRIPT=!LAUNCH_LINE:%SCRIPT_NAME%=!"
if /i "!LAUNCH_LINE_WITHOUT_SCRIPT!"=="!LAUNCH_LINE!" (
    endlocal
    exit /b 0
)
endlocal
set "PAUSE_AT_END=1"
exit /b 0

:find_powershell
set "POWERSHELL_COMMAND="
if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" set "POWERSHELL_COMMAND=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if defined POWERSHELL_COMMAND exit /b 0
where powershell.exe >nul 2>&1
if not errorlevel 1 set "POWERSHELL_COMMAND=powershell.exe"
if defined POWERSHELL_COMMAND exit /b 0
where pwsh.exe >nul 2>&1
if not errorlevel 1 set "POWERSHELL_COMMAND=pwsh.exe"
exit /b 0

:prepare_program_files
rem Removes the "downloaded from the internet" mark from program files so the
rem BAT files and shortcuts start without a security warning (.venv and user
rem data are not touched), and reports a OneDrive location.
set "PROGRAM_LOCATION=local"
call :find_powershell
if not defined POWERSHELL_COMMAND exit /b 0
"%POWERSHELL_COMMAND%" -NoLogo -NoProfile -NonInteractive -Command "$root = $env:PROJECT_DIR.TrimEnd('\'); $types = '.bat', '.cmd', '.ps1', '.py', '.pyw', '.ico', '.png', '.txt', '.md'; Get-ChildItem -LiteralPath $root -File | Where-Object { $types -contains $_.Extension.ToLowerInvariant() } | Unblock-File; foreach ($name in 'OneDrive', 'OneDriveCommercial', 'OneDriveConsumer') { $base = [Environment]::GetEnvironmentVariable($name); if ($base -and ($root + '\').StartsWith($base.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { exit 3 } }; exit 0" >>"%LOG_FILE%" 2>&1
set "LOCATION_CODE=%ERRORLEVEL%"
if "%LOCATION_CODE%"=="3" set "PROGRAM_LOCATION=onedrive"
if not "%LOCATION_CODE%"=="0" if not "%LOCATION_CODE%"=="3" >>"%LOG_FILE%" echo Program file preparation failed: %LOCATION_CODE%
exit /b 0

:start_application
rem Own process without a console; the GUI reports start errors itself.
set "GUI_PYTHON=%VENV_PYTHONW%"
if not exist "%GUI_PYTHON%" set "GUI_PYTHON=%VENV_PYTHON%"
rem start keeps an earlier ERRORLEVEL on success; reset it first.
(call )
start "" "%GUI_PYTHON%" "%PROJECT_DIR%start_gui.py" <nul >nul 2>&1
exit /b %ERRORLEVEL%

:find_supported_python
rem Prefer the pinned series, then any supported Python. A Microsoft Store
rem placeholder python.exe exits with 9009 and never counts as Python.
set "BASE_PYTHON="
set "BASE_PYTHON_ARGS="
set "PYTHON_UNSUITABLE=0"
where py >nul 2>&1
if errorlevel 1 goto find_pinned_directory
call :accept_python py -%PINNED_PYTHON_TAG%
if not errorlevel 1 exit /b 0
:find_pinned_directory
if not exist "%PINNED_PYTHON_DIR%\python.exe" goto find_default_py
call :accept_python "%PINNED_PYTHON_DIR%\python.exe"
if not errorlevel 1 exit /b 0
:find_default_py
where py >nul 2>&1
if errorlevel 1 goto find_python_command
call :accept_python py -3
if not errorlevel 1 exit /b 0
:find_python_command
where python >nul 2>&1
if errorlevel 1 exit /b 10
call :accept_python python
if not errorlevel 1 exit /b 0
exit /b 10

:accept_python
"%~1" %2 -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 15) else 11)" >nul 2>&1
set "PYTHON_PROBE_CODE=%ERRORLEVEL%"
if "%PYTHON_PROBE_CODE%"=="11" set "PYTHON_UNSUITABLE=1"
if not "%PYTHON_PROBE_CODE%"=="0" exit /b 1
set "BASE_PYTHON=%~1"
set "BASE_PYTHON_ARGS=%2"
exit /b 0
