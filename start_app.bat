@echo off
setlocal EnableExtensions DisableDelayedExpansion
title PDF Datei Umbenenner - Smart Launcher

set "PROJECT_DIR=%~dp0"
set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"
set "LOG_DIR=%~dp0.launcher-logs"
set "LOG_FILE=%~dp0.launcher-logs\start_app.log"

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

>"%LOG_FILE%" echo PDF Datei Umbenenner launcher diagnostics
>>"%LOG_FILE%" echo Project: %PROJECT_DIR%

echo ================================================================
echo   PDF Datei Umbenenner - Smart Launcher
echo ================================================================
echo Diagnoselog: "%LOG_FILE%"
echo.

if not exist "%PROJECT_DIR%bootstrap.py" (
    set "FAIL_CODE=50"
    echo FEHLER: bootstrap.py fehlt.
    goto launcher_failure
)

if not exist "%VENV_PYTHON%" goto repair_missing_environment

"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" requirements-current >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto repair_changed_requirements

"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-core >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto repair_failed_health

echo OK: Das vorhandene .venv hat die schnelle Kernpruefung bestanden.
goto environment_ready

:repair_missing_environment
set "REPAIR_REASON=Das lokale .venv fehlt."
goto repair_environment

:repair_changed_requirements
set "REPAIR_REASON=requirements.txt wurde geaendert oder der Installationsstempel fehlt."
goto repair_environment

:repair_failed_health
set "REPAIR_REASON=Die Kernpruefung ist fehlgeschlagen."
goto repair_environment

:repair_environment
echo Reparatur erforderlich: %REPAIR_REASON%
call :repair_core
if errorlevel 1 goto repair_failure
echo OK: Die lokale Umgebung wurde repariert.
goto environment_ready

:repair_failure
set "FAIL_CODE=%ERRORLEVEL%"
echo FEHLER: Die lokale Umgebung konnte nicht repariert werden.
goto launcher_failure

:environment_ready
"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-ocr >>"%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo WARNUNG: OCR ist nicht verfuegbar. PDF-Dateien mit Textebene funktionieren.
    echo Bei Bedarf setup.bat fuer die optionale OCR-Unterstuetzung ausfuehren.
) else (
    echo OK: Die optionale OCR-Unterstuetzung ist bereit.
)

if /i "%~1"=="--health-only" (
    echo Reine Zustandspruefung abgeschlossen. Die GUI wurde nicht gestartet.
    exit /b 0
)

if not exist "%PROJECT_DIR%start_gui.py" (
    set "FAIL_CODE=50"
    echo FEHLER: start_gui.py fehlt.
    goto launcher_failure
)

echo Anwendung wird gestartet...
"%VENV_PYTHON%" "%PROJECT_DIR%start_gui.py" >>"%LOG_FILE%" 2>&1
set "APP_EXIT_CODE=%ERRORLEVEL%"
if not "%APP_EXIT_CODE%"=="0" goto application_failure

echo Anwendung wurde normal beendet.
exit /b 0

:application_failure
set "FAIL_CODE=%APP_EXIT_CODE%"
echo FEHLER: Die Anwendung wurde mit Code %FAIL_CODE% beendet.
goto launcher_failure

:launcher_failure
if not defined FAIL_CODE set "FAIL_CODE=1"
echo Diagnoselog: "%LOG_FILE%"
exit /b %FAIL_CODE%

:repair_core
if not exist "%VENV_PYTHON%" goto repair_create_venv

"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-python >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto repair_recreate_venv
goto repair_install_core

:repair_create_venv
call :find_supported_python
if errorlevel 1 exit /b 10
"%BASE_PYTHON%" %BASE_PYTHON_ARGS% -m venv "%PROJECT_DIR%.venv" >>"%LOG_FILE%" 2>&1
if errorlevel 1 exit /b 50
goto repair_install_core

:repair_recreate_venv
call :find_supported_python
if errorlevel 1 exit /b 10
"%BASE_PYTHON%" %BASE_PYTHON_ARGS% -m venv --clear "%PROJECT_DIR%.venv" >>"%LOG_FILE%" 2>&1
if errorlevel 1 exit /b 50
goto repair_install_core

:repair_install_core
if not exist "%VENV_PYTHON%" exit /b 50
"%VENV_PYTHON%" -m ensurepip --upgrade >>"%LOG_FILE%" 2>&1
if errorlevel 1 exit /b 50
"%VENV_PYTHON%" -m pip install --disable-pip-version-check -r "%PROJECT_DIR%requirements.txt" >>"%LOG_FILE%" 2>&1
if errorlevel 1 exit /b 50
"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-core >>"%LOG_FILE%" 2>&1
if errorlevel 1 exit /b 50
"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" write-requirements-stamp >>"%LOG_FILE%" 2>&1
if errorlevel 1 exit /b 50
exit /b 0

:find_supported_python
set "BASE_PYTHON="
set "BASE_PYTHON_ARGS="
where py >nul 2>&1
if errorlevel 1 goto launcher_try_python
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 10)" >nul 2>&1
if errorlevel 1 goto launcher_try_python
set "BASE_PYTHON=py"
set "BASE_PYTHON_ARGS=-3"
exit /b 0

:launcher_try_python
where python >nul 2>&1
if errorlevel 1 exit /b 10
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 10)" >nul 2>&1
if errorlevel 1 exit /b 10
set "BASE_PYTHON=python"
set "BASE_PYTHON_ARGS="
exit /b 0
