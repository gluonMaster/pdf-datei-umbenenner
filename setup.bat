@echo off
setlocal EnableExtensions DisableDelayedExpansion
title PDF Datei Umbenenner - Setup

set "PROJECT_DIR=%~dp0"
set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"
set "LOG_DIR=%~dp0.launcher-logs"
set "LOG_FILE=%~dp0.launcher-logs\setup.log"
set "SETUP_MODE=interactive"
set "INSTALL_OCR=prompt"
set "OCR_REQUIRED=0"
set "AUTO_VC_RUNTIME=0"
set "OCR_READY=0"
set "OCR_REINSTALL_ATTEMPTED=0"
set "VC_RUNTIME_ATTEMPTED=0"

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
if /i not "%SETUP_MODE%"=="interactive" if /i not "%SETUP_MODE%"=="unattended" goto incompatible_modes
set "SETUP_MODE=unattended"
set "INSTALL_OCR=yes"
set "OCR_REQUIRED=1"
set "AUTO_VC_RUNTIME=1"
shift
goto parse_arguments

:select_core_only
if /i not "%SETUP_MODE%"=="interactive" if /i not "%SETUP_MODE%"=="core-only" goto incompatible_modes
set "SETUP_MODE=core-only"
set "INSTALL_OCR=no"
set "OCR_REQUIRED=0"
set "AUTO_VC_RUNTIME=0"
shift
goto parse_arguments

:incompatible_modes
echo FEHLER: --unattended und --core-only duerfen nicht kombiniert werden.
exit /b 64

:show_usage
echo Aufruf:
echo   setup.bat                 Interaktives Setup; OCR ist optional.
echo   setup.bat --unattended    Ohne Rueckfragen; OCR ist erforderlich.
echo   setup.bat --core-only     Ohne Rueckfragen; nur Kernanwendung.
echo.
echo --unattended repariert PyMuPDF automatisch. Bei einer fehlenden
echo Windows-Laufzeit wird das signierte Microsoft VC++ Redistributable
echo still installiert; dafuer muss die Konsole als Administrator laufen.
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
>>"%LOG_FILE%" echo Project: %PROJECT_DIR%
>>"%LOG_FILE%" echo Mode: %SETUP_MODE%

echo ================================================================
echo   PDF Datei Umbenenner - Setup
echo ================================================================
echo.
echo Dieses Setup erstellt ein privates .venv im Anwendungsverzeichnis.
echo Fuer die Kernanwendung sind keine Administratorrechte erforderlich.
if /i "%SETUP_MODE%"=="unattended" echo Automatikmodus: OCR ist erforderlich; Rueckfragen sind deaktiviert.
echo Diagnoselog: "%LOG_FILE%"
echo.

echo [1/5] Suche Python 3.10 oder neuer...
call :find_supported_python
if errorlevel 1 goto python_unavailable
echo OK: Unterstuetztes Python wurde gefunden.
"%BASE_PYTHON%" %BASE_PYTHON_ARGS% --version >>"%LOG_FILE%" 2>&1

echo [2/5] Lokale Umgebung wird erstellt oder geprueft...
if not exist "%VENV_PYTHON%" goto create_venv
"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-python >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto recreate_venv
echo OK: Das vorhandene .venv verwendet ein unterstuetztes Python.
goto install_core

:create_venv
echo .venv wird erstellt...
"%BASE_PYTHON%" %BASE_PYTHON_ARGS% -m venv "%PROJECT_DIR%.venv" >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto venv_failed
goto install_core

:recreate_venv
echo Das vorhandene .venv ist unvollstaendig oder nicht unterstuetzt.
echo Die generierte Umgebung wird neu erstellt...
"%BASE_PYTHON%" %BASE_PYTHON_ARGS% -m venv --clear "%PROJECT_DIR%.venv" >>"%LOG_FILE%" 2>&1
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

echo [3/5] Erforderliche Kernabhaengigkeiten werden installiert...
"%VENV_PYTHON%" -m ensurepip --upgrade >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto ensurepip_failed
"%VENV_PYTHON%" -m pip install --disable-pip-version-check -r "%PROJECT_DIR%requirements.txt" >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto pip_failed

echo Kernpruefung wird ausgefuehrt...
"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-core >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto health_failed
"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" write-requirements-stamp >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto stamp_failed
echo OK: Die Kernanwendung ist bereit.

echo [4/5] Optionale OCR-Unterstuetzung...
if /i "%INSTALL_OCR%"=="yes" goto install_ocr
if /i "%INSTALL_OCR%"=="no" goto skip_ocr
set "OCR_CHOICE="
set /p "OCR_CHOICE=Optionale Python-OCR-Unterstuetzung installieren? [j/N]: "
if /i "%OCR_CHOICE%"=="Y" goto install_ocr
if /i "%OCR_CHOICE%"=="YES" goto install_ocr
if /i "%OCR_CHOICE%"=="J" goto install_ocr
if /i "%OCR_CHOICE%"=="JA" goto install_ocr

:skip_ocr
echo OCR wurde nicht ausgewaehlt. Die Kernanwendung bleibt voll nutzbar.
goto optional_actions

:install_ocr
set "OCR_REINSTALL_ATTEMPTED=0"
set "VC_RUNTIME_ATTEMPTED=0"
echo Python-Installationswerkzeuge fuer OCR werden aktualisiert...
"%VENV_PYTHON%" -m pip install --disable-pip-version-check --upgrade pip setuptools wheel >>"%LOG_FILE%" 2>&1
if errorlevel 1 echo WARNUNG: pip/setuptools/wheel konnten nicht aktualisiert werden; Installation wird trotzdem versucht.
echo Optionale Python-OCR-Abhaengigkeiten werden installiert...
"%VENV_PYTHON%" -m pip install --disable-pip-version-check -r "%PROJECT_DIR%requirements-ocr.txt" >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto repair_ocr_python
goto run_ocr_health

:repair_ocr_python
if "%OCR_REINSTALL_ATTEMPTED%"=="1" goto ocr_python_failed
set "OCR_REINSTALL_ATTEMPTED=1"
echo PyMuPDF wird ohne Paket-Cache neu installiert...
"%VENV_PYTHON%" -m pip install --disable-pip-version-check --upgrade --force-reinstall --no-cache-dir -r "%PROJECT_DIR%requirements-ocr.txt" >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto ocr_python_failed
goto run_ocr_health

:run_ocr_health
"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-ocr >>"%LOG_FILE%" 2>&1
set "OCR_CHECK_CODE=%ERRORLEVEL%"
if "%OCR_CHECK_CODE%"=="0" goto ocr_ready
if "%OCR_CHECK_CODE%"=="30" goto repair_ocr_python
if "%OCR_CHECK_CODE%"=="35" goto repair_ocr_python
if "%OCR_CHECK_CODE%"=="34" goto handle_pymupdf_runtime
goto ocr_external_failed

:handle_pymupdf_runtime
if "%OCR_REINSTALL_ATTEMPTED%"=="0" goto repair_ocr_python
if "%VC_RUNTIME_ATTEMPTED%"=="1" goto ocr_unavailable
if "%AUTO_VC_RUNTIME%"=="1" goto install_vc_runtime
echo PyMuPDF benoetigt die aktuelle Microsoft Visual C++ Windows-Laufzeit.
echo Das Microsoft-Paket wird nur nach gueltiger digitaler Signatur ausgefuehrt.
set "VC_RUNTIME_CHOICE="
set /p "VC_RUNTIME_CHOICE=VC++ Redistributable automatisch installieren? [j/N]: "
if /i "%VC_RUNTIME_CHOICE%"=="Y" goto install_vc_runtime
if /i "%VC_RUNTIME_CHOICE%"=="YES" goto install_vc_runtime
if /i "%VC_RUNTIME_CHOICE%"=="J" goto install_vc_runtime
if /i "%VC_RUNTIME_CHOICE%"=="JA" goto install_vc_runtime
goto ocr_unavailable

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
goto run_ocr_health

:powershell_unavailable
set "OCR_CHECK_CODE=60"
echo FEHLER: PowerShell ist fuer die sichere Windows-Laufzeitinstallation erforderlich.
goto ocr_unavailable

:vc_runtime_failed
set "OCR_CHECK_CODE=%VC_RUNTIME_CODE%"
echo FEHLER: Die Microsoft VC++ Laufzeit konnte nicht automatisch eingerichtet werden.
if "%VC_RUNTIME_CODE%"=="61" echo Starten Sie den Automatikmodus aus einer administrativen Konsole.
goto ocr_unavailable

:ocr_python_failed
set "OCR_CHECK_CODE=30"
echo FEHLER: PyMuPDF konnte auch nach der automatischen Neuinstallation nicht geladen werden.
goto ocr_unavailable

:ocr_external_failed
if "%OCR_CHECK_CODE%"=="31" goto tesseract_unavailable
if "%OCR_CHECK_CODE%"=="32" goto tesseract_languages_unavailable
echo FEHLER: Die OCR-Gesamtpruefung ist fehlgeschlagen. Rueckgabecode: %OCR_CHECK_CODE%.
goto ocr_unavailable

:tesseract_unavailable
echo FEHLER: Tesseract wurde nicht gefunden.
goto maybe_offer_tesseract_instructions

:tesseract_languages_unavailable
echo FEHLER: Die Tesseract-Sprachdaten deu und/oder eng fehlen.
goto maybe_offer_tesseract_instructions

:maybe_offer_tesseract_instructions
if "%OCR_REQUIRED%"=="1" goto ocr_unavailable
goto offer_tesseract_instructions

:ocr_unavailable
if "%OCR_REQUIRED%"=="1" goto required_ocr_failure
echo WARNUNG: OCR ist nicht vollstaendig verfuegbar. Der Kernstart ist nicht betroffen.
echo Details und der maschinenlesbare Grund stehen im Diagnoselog.
goto optional_actions

:required_ocr_failure
set "FAIL_CODE=%OCR_CHECK_CODE%"
echo FEHLER: OCR ist im Automatikmodus erforderlich.
goto critical_failure

:ocr_ready
set "OCR_READY=1"
echo OK: OCR-Renderer, Tesseract und Sprachdaten sind bereit.
goto optional_actions

:offer_tesseract_instructions
echo.
echo Tesseract ist eine separate Systemkomponente und kann IT-Freigabe erfordern.
echo Der interaktive Modus installiert sie nicht still und fordert keine hoeheren Rechte an.
set "OPEN_TESSERACT_GUIDE="
set /p "OPEN_TESSERACT_GUIDE=Offizielle Tesseract-Anleitung oeffnen? [j/N]: "
if /i "%OPEN_TESSERACT_GUIDE%"=="Y" goto open_tesseract_guide
if /i "%OPEN_TESSERACT_GUIDE%"=="YES" goto open_tesseract_guide
if /i "%OPEN_TESSERACT_GUIDE%"=="J" goto open_tesseract_guide
if /i "%OPEN_TESSERACT_GUIDE%"=="JA" goto open_tesseract_guide
goto optional_actions

:open_tesseract_guide
start "" "https://tesseract-ocr.github.io/tessdoc/Installation.html"
echo Installieren Sie bei Freigabe Tesseract sowie die Sprachdaten deu und eng.
set "RECHECK_OCR="
set /p "RECHECK_OCR=OCR nach der Systemeinrichtung erneut pruefen? [j/N]: "
if /i "%RECHECK_OCR%"=="Y" goto recheck_ocr
if /i "%RECHECK_OCR%"=="YES" goto recheck_ocr
if /i "%RECHECK_OCR%"=="J" goto recheck_ocr
if /i "%RECHECK_OCR%"=="JA" goto recheck_ocr
goto optional_actions

:recheck_ocr
"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-ocr >>"%LOG_FILE%" 2>&1
set "OCR_CHECK_CODE=%ERRORLEVEL%"
if not "%OCR_CHECK_CODE%"=="0" goto ocr_unavailable
goto ocr_ready

:optional_actions
echo [5/5] Optionale Komfortfunktionen...
if /i not "%SETUP_MODE%"=="interactive" goto setup_success
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
goto setup_success

:test_run
"%VENV_PYTHON%" "%PROJECT_DIR%start_gui.py" >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto test_failed
goto setup_success

:ensurepip_failed
set "FAIL_CODE=%ERRORLEVEL%"
echo FEHLER: pip ist im lokalen .venv nicht verfuegbar.
goto critical_failure

:pip_failed
set "FAIL_CODE=%ERRORLEVEL%"
echo FEHLER: Erforderliche Abhaengigkeiten konnten nicht installiert werden.
goto critical_failure

:health_failed
set "FAIL_CODE=%ERRORLEVEL%"
echo FEHLER: Die erforderliche Kernpruefung ist fehlgeschlagen.
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
echo FEHLER: Python 3.10 oder neuer wurde nicht gefunden.
echo Python muss separat aus offizieller Quelle installiert werden.
if /i not "%SETUP_MODE%"=="interactive" exit /b 10
echo Ohne Ihre Aktion wird keine Systemsoftware installiert.
set "OPEN_PYTHON_GUIDE="
set /p "OPEN_PYTHON_GUIDE=Offizielle Python-Downloadseite oeffnen? [j/N]: "
if /i "%OPEN_PYTHON_GUIDE%"=="Y" start "" "https://www.python.org/downloads/windows/"
if /i "%OPEN_PYTHON_GUIDE%"=="YES" start "" "https://www.python.org/downloads/windows/"
if /i "%OPEN_PYTHON_GUIDE%"=="J" start "" "https://www.python.org/downloads/windows/"
if /i "%OPEN_PYTHON_GUIDE%"=="JA" start "" "https://www.python.org/downloads/windows/"
exit /b 10

:setup_success
echo.
echo ================================================================
echo   SETUP ABGESCHLOSSEN
echo ================================================================
if "%OCR_READY%"=="1" echo Kernpruefung und OCR-Pruefung sind OK.
if not "%OCR_READY%"=="1" echo Die Kernpruefung ist OK. OCR wurde nicht aktiviert.
echo Fuer den Alltag start_app.bat verwenden.
echo Diagnoselog: "%LOG_FILE%"
exit /b 0

:critical_failure
if not defined FAIL_CODE set "FAIL_CODE=1"
echo.
echo SETUP FEHLGESCHLAGEN. Rueckgabecode: %FAIL_CODE%.
echo Diagnoselog: "%LOG_FILE%"
exit /b %FAIL_CODE%

:find_supported_python
set "FOUND_PYTHON="
set "BASE_PYTHON="
set "BASE_PYTHON_ARGS="
where py >nul 2>&1
if errorlevel 1 goto try_python_command
set "FOUND_PYTHON=1"
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 10)" >nul 2>&1
if errorlevel 1 goto try_python_command
set "BASE_PYTHON=py"
set "BASE_PYTHON_ARGS=-3"
exit /b 0

:try_python_command
where python >nul 2>&1
if errorlevel 1 exit /b 10
set "FOUND_PYTHON=1"
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 10)" >nul 2>&1
if errorlevel 1 exit /b 10
set "BASE_PYTHON=python"
set "BASE_PYTHON_ARGS="
exit /b 0
