@echo off
setlocal EnableExtensions DisableDelayedExpansion
title PDF Datei Umbenenner - Setup

set "PROJECT_DIR=%~dp0"
set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"
set "LOG_DIR=%~dp0.launcher-logs"
set "LOG_FILE=%~dp0.launcher-logs\setup.log"

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

echo ================================================================
echo   PDF Datei Umbenenner - Setup
echo ================================================================
echo.
echo Dieses Setup erstellt ein privates .venv im Anwendungsverzeichnis.
echo Fuer die Kernanwendung sind keine Administratorrechte erforderlich.
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
set "OCR_CHOICE="
set /p "OCR_CHOICE=Optionale Python-OCR-Unterstuetzung installieren? [j/N]: "
if /i "%OCR_CHOICE%"=="Y" goto install_ocr
if /i "%OCR_CHOICE%"=="YES" goto install_ocr
if /i "%OCR_CHOICE%"=="J" goto install_ocr
if /i "%OCR_CHOICE%"=="JA" goto install_ocr
echo OCR wurde nicht ausgewaehlt. Die Kernanwendung bleibt voll nutzbar.
goto optional_actions

:install_ocr
echo Optionale Python-OCR-Abhaengigkeiten werden installiert...
"%VENV_PYTHON%" -m pip install --disable-pip-version-check -r "%PROJECT_DIR%requirements-ocr.txt" >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto optional_ocr_pip_failed

"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-ocr >>"%LOG_FILE%" 2>&1
set "OCR_CHECK_CODE=%ERRORLEVEL%"
if "%OCR_CHECK_CODE%"=="0" (
    echo OK: OCR-Renderer, Tesseract und Sprachdaten sind bereit.
    goto optional_actions
)
if "%OCR_CHECK_CODE%"=="30" (
    echo WARNUNG: Optionale Python-OCR-Abhaengigkeiten fehlen.
    echo Details stehen im Diagnoselog. Der Kernstart ist nicht betroffen.
    goto optional_actions
)
echo Tesseract oder die Sprachdaten deu/eng sind nicht bereit.
echo Der Kernstart ist nicht betroffen.
goto offer_tesseract_instructions

:optional_ocr_pip_failed
echo WARNUNG: Optionale OCR-Pakete konnten nicht installiert werden.
echo Details stehen im Diagnoselog. Das Kern-Setup wird fortgesetzt.
goto optional_actions

:offer_tesseract_instructions
echo.
echo Tesseract ist eine separate Systemkomponente und kann IT-Freigabe erfordern.
echo Dieses Setup installiert sie nicht still und fordert keine hoeheren Rechte an.
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
if errorlevel 1 (
    echo WARNUNG: OCR ist weiterhin nicht verfuegbar. Siehe Diagnoselog.
) else (
    echo OK: OCR ist bereit.
)
goto optional_actions

:optional_actions
echo [5/5] Optionale Komfortfunktionen...
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
echo Die Kernpruefung ist OK. Fuer den Alltag start_app.bat verwenden.
echo OCR ist optional und blockiert keine PDF-Dateien mit Textebene.
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
