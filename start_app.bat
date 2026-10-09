@echo off
setlocal EnableExtensions DisableDelayedExpansion
title PDF Datei Umbenenner - Smart Launcher

set "PROJECT_DIR=%~dp0"
set "SCRIPT_NAME=%~nx0"
set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"
set "VENV_PYTHONW=%~dp0.venv\Scripts\pythonw.exe"
set "LOG_DIR=%~dp0.launcher-logs"
set "LOG_FILE=%~dp0.launcher-logs\start_app.log"
set "PYTHONIOENCODING=utf-8"
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"
rem Python installed by setup.bat on request (see install_python.ps1).
set "PINNED_PYTHON_TAG=3.14"
set "PINNED_PYTHON_DIR=%LOCALAPPDATA%\Programs\Python\Python314"
set "REINSTALL_FLAGS="
set "HEALTH_ONLY=0"
set "PAUSE_ON_ERROR=auto"

:parse_arguments
if "%~1"=="" goto arguments_done
if /i "%~1"=="--health-only" goto select_health_only
if /i "%~1"=="--no-pause" goto select_no_pause
echo FEHLER: Erlaubte Parameter: --health-only, --no-pause
exit /b 64

:select_health_only
set "HEALTH_ONLY=1"
shift
goto parse_arguments

:select_no_pause
set "PAUSE_ON_ERROR=0"
shift
goto parse_arguments

:arguments_done
if "%PAUSE_ON_ERROR%"=="auto" call :detect_double_click

rem cmd.exe cannot use a UNC path as the current directory.
if "%PROJECT_DIR:~0,2%"=="\\" goto unc_location

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo FEHLER: Das Anwendungsverzeichnis kann nicht geoeffnet werden.
    set "FAIL_CODE=50"
    goto finish_failure
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
if not exist "%LOG_DIR%" (
    echo FEHLER: Diagnoseverzeichnis kann nicht erstellt werden: "%LOG_DIR%"
    set "FAIL_CODE=50"
    goto finish_failure
)

>"%LOG_FILE%" echo PDF Datei Umbenenner launcher diagnostics
>>"%LOG_FILE%" echo Project: "%PROJECT_DIR%"

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

"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-python >>"%LOG_FILE%" 2>&1
if not "%ERRORLEVEL%"=="0" goto repair_failed_health

"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" requirements-current >>"%LOG_FILE%" 2>&1
set "REQUIREMENTS_CODE=%ERRORLEVEL%"
if "%REQUIREMENTS_CODE%"=="40" goto repair_changed_requirements
if not "%REQUIREMENTS_CODE%"=="0" goto requirements_check_failed

"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-core >>"%LOG_FILE%" 2>&1
set "CORE_CODE=%ERRORLEVEL%"
if "%CORE_CODE%"=="10" goto repair_failed_health
if "%CORE_CODE%"=="20" goto repair_failed_health
if "%CORE_CODE%"=="21" goto tkinter_failed
if "%CORE_CODE%"=="22" goto repair_failed_health
if "%CORE_CODE%"=="34" goto pymupdf_runtime_failed
if "%CORE_CODE%"=="35" goto repair_failed_health
if not "%CORE_CODE%"=="0" goto core_data_failed

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
set "REINSTALL_FLAGS=--force-reinstall --no-cache-dir"
goto repair_environment

:requirements_check_failed
set "FAIL_CODE=%REQUIREMENTS_CODE%"
echo FEHLER: Der Requirements-Stand konnte nicht geprueft werden.
echo Es wird keine Reparaturinstallation gestartet. Siehe Diagnoselog.
goto launcher_failure

:core_data_failed
set "FAIL_CODE=%CORE_CODE%"
echo FEHLER: Die Kernpruefung ist fehlgeschlagen. Code: %CORE_CODE%.
echo Pruefen Sie Konfiguration und Schuelerverzeichnis anhand des Diagnoselogs.
goto launcher_failure

:tkinter_failed
set "FAIL_CODE=21"
echo FEHLER: tkinter/Tcl fehlt oder ist beschaedigt. Python mit Tcl/Tk-Unterstuetzung reparieren.
goto launcher_failure

:pymupdf_runtime_failed
set "FAIL_CODE=34"
echo FEHLER: PyMuPDF kann nicht geladen werden: Die Microsoft Visual C++ Laufzeit fehlt
echo oder ist beschaedigt. Eine Paketinstallation behebt das nicht.
echo Abhilfe: setup.bat ausfuehren; es bietet die signierte Microsoft-Laufzeit an.
goto launcher_failure

:repair_environment
echo Reparatur erforderlich: %REPAIR_REASON%
call :repair_core
if errorlevel 1 goto repair_failure
echo OK: Die lokale Umgebung wurde repariert.
goto environment_ready

:repair_failure
set "FAIL_CODE=%ERRORLEVEL%"
if "%FAIL_CODE%"=="34" goto pymupdf_runtime_failed
echo FEHLER: Die lokale Umgebung konnte nicht repariert werden.
if "%FAIL_CODE%"=="10" echo Python fehlt: setup.bat ausfuehren, es installiert Python auf Wunsch.
goto launcher_failure

:environment_ready
rem Missing Tesseract never triggers pip: the GUI starts and blocks processing.
"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-ocr >>"%LOG_FILE%" 2>&1
set "OCR_CODE=%ERRORLEVEL%"
if "%OCR_CODE%"=="0" goto ocr_ready
echo WARNUNG: Tesseract OCR mit deutschen Sprachdaten deu ist nicht verfuegbar. Code: %OCR_CODE%.
echo Die Anwendung startet; Schuelerverzeichnis und Pruefliste sind nutzbar.
echo Die Verarbeitung von PDF-Dateien bleibt gesperrt. Einrichtung: setup.bat ausfuehren.
goto launch_application

:ocr_ready
echo OK: Tesseract OCR mit deutschen Sprachdaten ist bereit.

:launch_application
if "%HEALTH_ONLY%"=="1" (
    echo Reine Zustandspruefung abgeschlossen. Die GUI wurde nicht gestartet.
    exit /b 0
)

if not exist "%PROJECT_DIR%start_gui.py" (
    set "FAIL_CODE=50"
    echo FEHLER: start_gui.py fehlt.
    goto launcher_failure
)

rem The GUI runs as its own process without a console, so this window closes
rem now; start errors go to .launcher-logs\gui.log and a message box.
echo Anwendung wird gestartet...
set "GUI_PYTHON=%VENV_PYTHONW%"
if not exist "%GUI_PYTHON%" set "GUI_PYTHON=%VENV_PYTHON%"
rem start keeps an earlier ERRORLEVEL on success; reset it first.
(call )
start "" "%GUI_PYTHON%" "%PROJECT_DIR%start_gui.py" <nul >nul 2>&1
if errorlevel 1 goto application_start_failed
>>"%LOG_FILE%" echo GUI process started: "%GUI_PYTHON%"
exit /b 0

:application_start_failed
set "FAIL_CODE=50"
echo FEHLER: Die Anwendung konnte nicht gestartet werden.
goto launcher_failure

:unc_location
echo FEHLER: Das Programm liegt auf einem Netzwerkpfad:
echo   "%PROJECT_DIR%"
echo Von dort kann es nicht gestartet werden. Bitte den Programmordner auf diesen
echo Computer kopieren, zum Beispiel nach "%USERPROFILE%\PDF-Datei-Umbenenner",
echo und dort setup.bat ausfuehren.
set "FAIL_CODE=51"
goto finish_failure

:launcher_failure
if not defined FAIL_CODE set "FAIL_CODE=1"
echo Diagnoselog: "%LOG_FILE%"

:finish_failure
if "%PAUSE_ON_ERROR%"=="1" (
    echo.
    echo Zum Schliessen dieses Fensters eine beliebige Taste druecken...
    pause >nul
)
exit /b %FAIL_CODE%

:repair_core
if not exist "%VENV_PYTHON%" goto repair_create_venv

"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-python >>"%LOG_FILE%" 2>&1
if not "%ERRORLEVEL%"=="0" goto repair_create_venv
goto repair_install_core

:repair_create_venv
call :find_supported_python
if errorlevel 1 exit /b 10
"%BASE_PYTHON%" %BASE_PYTHON_ARGS% "%PROJECT_DIR%bootstrap.py" prepare-venv >>"%LOG_FILE%" 2>&1
if errorlevel 1 exit /b %ERRORLEVEL%
goto repair_install_core

:repair_install_core
if not exist "%VENV_PYTHON%" exit /b 50
"%VENV_PYTHON%" -m ensurepip --upgrade >>"%LOG_FILE%" 2>&1
if errorlevel 1 exit /b %ERRORLEVEL%
"%VENV_PYTHON%" -m pip install --disable-pip-version-check --only-binary=:all: %REINSTALL_FLAGS% -r "%PROJECT_DIR%requirements.txt" >>"%LOG_FILE%" 2>&1
if errorlevel 1 exit /b %ERRORLEVEL%
"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-core >>"%LOG_FILE%" 2>&1
if "%ERRORLEVEL%"=="20" goto repair_imports_retry
if "%ERRORLEVEL%"=="22" goto repair_imports_retry
if "%ERRORLEVEL%"=="35" goto repair_imports_retry
if errorlevel 1 exit /b %ERRORLEVEL%
goto repair_write_stamp

:repair_imports_retry
if defined REINSTALL_FLAGS exit /b %ERRORLEVEL%
set "REINSTALL_FLAGS=--force-reinstall --no-cache-dir"
goto repair_install_core

:repair_write_stamp
"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" write-requirements-stamp >>"%LOG_FILE%" 2>&1
if errorlevel 1 exit /b %ERRORLEVEL%
exit /b 0

:detect_double_click
rem Explorer and shortcuts run cmd.exe /c with this script; that window closes
rem on exit. A console the user opened has no script name in its command line.
set "PAUSE_ON_ERROR=0"
setlocal EnableDelayedExpansion
set "LAUNCH_LINE=!CMDCMDLINE!"
set "LAUNCH_LINE_WITHOUT_SCRIPT=!LAUNCH_LINE:%SCRIPT_NAME%=!"
if /i "!LAUNCH_LINE_WITHOUT_SCRIPT!"=="!LAUNCH_LINE!" (
    endlocal
    exit /b 0
)
endlocal
set "PAUSE_ON_ERROR=1"
exit /b 0

:find_supported_python
echo Suche Python 3.10 bis 3.15. Falls es fehlt: setup.bat starten.
rem Same order as setup.bat; a Microsoft Store placeholder never counts.
set "BASE_PYTHON="
set "BASE_PYTHON_ARGS="
where py >nul 2>&1
if errorlevel 1 goto launcher_find_pinned_directory
call :launcher_accept_python py -%PINNED_PYTHON_TAG%
if not errorlevel 1 exit /b 0
:launcher_find_pinned_directory
if not exist "%PINNED_PYTHON_DIR%\python.exe" goto launcher_find_default_py
call :launcher_accept_python "%PINNED_PYTHON_DIR%\python.exe"
if not errorlevel 1 exit /b 0
:launcher_find_default_py
where py >nul 2>&1
if errorlevel 1 goto launcher_try_python
call :launcher_accept_python py -3
if not errorlevel 1 exit /b 0
:launcher_try_python
where python >nul 2>&1
if errorlevel 1 exit /b 10
call :launcher_accept_python python
if not errorlevel 1 exit /b 0
exit /b 10

:launcher_accept_python
"%~1" %2 -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 15) else 11)" >nul 2>&1
if not "%ERRORLEVEL%"=="0" exit /b 1
set "BASE_PYTHON=%~1"
set "BASE_PYTHON_ARGS=%2"
exit /b 0
