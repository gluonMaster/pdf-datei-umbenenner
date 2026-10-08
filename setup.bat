@echo off
setlocal EnableExtensions DisableDelayedExpansion
title PDF Datei Umbenenner - Setup

set "PROJECT_DIR=%~dp0"
set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"
set "LOG_DIR=%~dp0.launcher-logs"
set "LOG_FILE=%~dp0.launcher-logs\setup.log"
set "SETUP_MODE=interactive"
set "CORE_REPAIR_ATTEMPTED=0"
set "VC_RUNTIME_ATTEMPTED=0"
set "TESSERACT_GUIDE_OFFERED=0"
set "OCR_STATE=unchecked"
set "PYTHONIOENCODING=utf-8"
rem The new Python install manager must not download a runtime implicitly.
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"

:parse_arguments
if "%~1"=="" goto arguments_done
if /i "%~1"=="--unattended" goto select_unattended
if /i "%~1"=="--core-only" goto select_core_only
if /i "%~1"=="--help" goto show_usage
if /i "%~1"=="-h" goto show_usage
echo FEHLER: Unbekannter Parameter: %~1
echo Verwenden Sie setup.bat --help fuer die Aufrufhilfe.
exit /b 64

:select_unattended
set "SETUP_MODE=unattended"
shift
goto parse_arguments

:select_core_only
set "SETUP_MODE=core-only"
shift
goto parse_arguments

:show_usage
echo Aufruf:
echo   setup.bat                 Interaktiv: Python-Pakete installieren, Tesseract OCR
echo                             mit deutschen Sprachdaten pruefen, bei Bedarf Anleitung.
echo   setup.bat --unattended    Ohne Rueckfragen und ohne Systeminstallation. Erfolg nur,
echo                             wenn Pakete, Tesseract und Sprachdaten deu bereit sind.
echo   setup.bat --core-only     Nur Python-Pakete; Tesseract wird nicht geprueft.
echo                             Die Verarbeitung von PDF-Dateien benoetigt ihn trotzdem.
echo.
echo Rueckgabecodes: 0 bereit; 31 Tesseract fehlt; 32 Sprachdaten deu oder eng fehlen;
echo 33 OCR-Pruefung fehlgeschlagen; 34 Microsoft VC++ Laufzeit fuer PyMuPDF fehlt.
echo Alle Codes stehen in README.md.
exit /b 0

:arguments_done
cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo FEHLER: Das Anwendungsverzeichnis kann nicht geoeffnet werden.
    exit /b 50
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
if not exist "%LOG_DIR%" (
    echo FEHLER: Diagnoseverzeichnis kann nicht erstellt werden: "%LOG_DIR%"
    exit /b 50
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
"%VENV_PYTHON%" -m pip install --disable-pip-version-check -r "%PROJECT_DIR%requirements.txt" >>"%LOG_FILE%" 2>&1
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
"%VENV_PYTHON%" -m pip install --disable-pip-version-check --force-reinstall --no-cache-dir -r "%PROJECT_DIR%requirements.txt" >>"%LOG_FILE%" 2>&1
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
set "VC_RUNTIME_CHOICE="
set /p "VC_RUNTIME_CHOICE=VC++ Redistributable automatisch installieren? [j/N]: "
if /i "%VC_RUNTIME_CHOICE%"=="Y" goto install_vc_runtime
if /i "%VC_RUNTIME_CHOICE%"=="YES" goto install_vc_runtime
if /i "%VC_RUNTIME_CHOICE%"=="J" goto install_vc_runtime
if /i "%VC_RUNTIME_CHOICE%"=="JA" goto install_vc_runtime
goto pymupdf_runtime_missing

:install_vc_runtime
set "VC_RUNTIME_ATTEMPTED=1"
set "POWERSHELL_COMMAND="
where powershell.exe >nul 2>&1
if not errorlevel 1 set "POWERSHELL_COMMAND=powershell.exe"
if defined POWERSHELL_COMMAND goto run_vc_runtime_installer
where pwsh.exe >nul 2>&1
if not errorlevel 1 set "POWERSHELL_COMMAND=pwsh.exe"
if not defined POWERSHELL_COMMAND goto powershell_unavailable

:run_vc_runtime_installer
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
set "OPEN_TESSERACT_GUIDE="
set /p "OPEN_TESSERACT_GUIDE=Download-Seite jetzt im Browser oeffnen? [j/N]: "
if /i "%OPEN_TESSERACT_GUIDE%"=="Y" goto open_tesseract_guide
if /i "%OPEN_TESSERACT_GUIDE%"=="YES" goto open_tesseract_guide
if /i "%OPEN_TESSERACT_GUIDE%"=="J" goto open_tesseract_guide
if /i "%OPEN_TESSERACT_GUIDE%"=="JA" goto open_tesseract_guide
goto ocr_missing

:open_tesseract_guide
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
set "SHORTCUT_CHOICE="
set /p "SHORTCUT_CHOICE=Desktop-Verknuepfung erstellen? [j/N]: "
if /i "%SHORTCUT_CHOICE%"=="Y" call "%PROJECT_DIR%create_desktop_shortcut.bat" --no-pause
if /i "%SHORTCUT_CHOICE%"=="YES" call "%PROJECT_DIR%create_desktop_shortcut.bat" --no-pause
if /i "%SHORTCUT_CHOICE%"=="J" call "%PROJECT_DIR%create_desktop_shortcut.bat" --no-pause
if /i "%SHORTCUT_CHOICE%"=="JA" call "%PROJECT_DIR%create_desktop_shortcut.bat" --no-pause

set "TEST_CHOICE="
set /p "TEST_CHOICE=Anwendung fuer einen Testlauf starten? [j/N]: "
if /i "%TEST_CHOICE%"=="Y" goto test_run
if /i "%TEST_CHOICE%"=="YES" goto test_run
if /i "%TEST_CHOICE%"=="J" goto test_run
if /i "%TEST_CHOICE%"=="JA" goto test_run
goto setup_finished

:test_run
"%VENV_PYTHON%" "%PROJECT_DIR%start_gui.py" >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto test_failed
goto setup_finished

:ensurepip_failed
set "FAIL_CODE=%ERRORLEVEL%"
echo FEHLER: pip ist im lokalen .venv nicht verfuegbar.
goto critical_failure

:pip_failed
set "FAIL_CODE=%ERRORLEVEL%"
echo FEHLER: Erforderliche Abhaengigkeiten konnten nicht installiert werden.
goto critical_failure

:stamp_failed
set "FAIL_CODE=%ERRORLEVEL%"
echo FEHLER: Der Requirements-Stand konnte nicht gespeichert werden.
goto critical_failure

:test_failed
set "FAIL_CODE=%ERRORLEVEL%"
echo FEHLER: Der angeforderte Testlauf ist fehlgeschlagen.
goto critical_failure

:python_unavailable
set "FAIL_CODE=10"
echo FEHLER: Python 3.10 oder neuer wurde nicht gefunden.
echo Python muss separat aus offizieller Quelle installiert werden.
if /i not "%SETUP_MODE%"=="interactive" goto critical_failure
echo Ohne Ihre Aktion wird keine Systemsoftware installiert.
set "OPEN_PYTHON_GUIDE="
set /p "OPEN_PYTHON_GUIDE=Offizielle Python-Downloadseite oeffnen? [j/N]: "
if /i "%OPEN_PYTHON_GUIDE%"=="Y" start "" "https://www.python.org/downloads/windows/"
if /i "%OPEN_PYTHON_GUIDE%"=="YES" start "" "https://www.python.org/downloads/windows/"
if /i "%OPEN_PYTHON_GUIDE%"=="J" start "" "https://www.python.org/downloads/windows/"
if /i "%OPEN_PYTHON_GUIDE%"=="JA" start "" "https://www.python.org/downloads/windows/"
goto critical_failure

:setup_finished
if "%OCR_STATE%"=="missing" goto setup_incomplete
echo.
echo ================================================================
echo   SETUP ABGESCHLOSSEN
echo ================================================================
if "%OCR_STATE%"=="ready" echo Python-Pakete, Kernpruefung und Tesseract mit deutschen Sprachdaten sind bereit.
if "%OCR_STATE%"=="skipped" echo Python-Pakete und Kernpruefung sind bereit. Tesseract wurde nicht geprueft;
if "%OCR_STATE%"=="skipped" echo die Verarbeitung benoetigt Tesseract OCR mit deutschen Sprachdaten deu.
echo Fuer den Alltag start_app.bat verwenden.
echo Diagnoselog: "%LOG_FILE%"
exit /b 0

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
exit /b %FAIL_CODE%

:critical_failure
if not defined FAIL_CODE set "FAIL_CODE=1"
echo.
echo SETUP FEHLGESCHLAGEN. Rueckgabecode: %FAIL_CODE%.
echo Diagnoselog: "%LOG_FILE%"
exit /b %FAIL_CODE%

:describe_ocr_problem
if "%OCR_CHECK_CODE%"=="31" echo FEHLER: Tesseract OCR wurde nicht gefunden.
if "%OCR_CHECK_CODE%"=="31" exit /b 0
if "%OCR_CHECK_CODE%"=="32" echo FEHLER: Tesseract ist installiert, aber die Sprachdaten deu oder eng fehlen.
if "%OCR_CHECK_CODE%"=="32" exit /b 0
echo FEHLER: Die OCR-Pruefung ist fehlgeschlagen. Code: %OCR_CHECK_CODE%. Details im Diagnoselog.
exit /b 0

:find_supported_python
set "BASE_PYTHON="
set "BASE_PYTHON_ARGS="
where py >nul 2>&1
if errorlevel 1 goto try_python_command
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 10)" >nul 2>&1
if errorlevel 1 goto try_python_command
set "BASE_PYTHON=py"
set "BASE_PYTHON_ARGS=-3"
exit /b 0

:try_python_command
where python >nul 2>&1
if errorlevel 1 exit /b 10
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 10)" >nul 2>&1
if errorlevel 1 exit /b 10
set "BASE_PYTHON=python"
set "BASE_PYTHON_ARGS="
exit /b 0
