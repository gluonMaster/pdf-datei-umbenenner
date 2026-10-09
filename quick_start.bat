@echo off
setlocal EnableExtensions DisableDelayedExpansion
title PDF Datei Umbenenner - Quick Start

set "PROJECT_DIR=%~dp0"
set "SCRIPT_NAME=%~nx0"
set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"
set "VENV_PYTHONW=%~dp0.venv\Scripts\pythonw.exe"
set "PYTHONIOENCODING=utf-8"
set "PAUSE_ON_ERROR=auto"

if "%~1"=="" goto arguments_done
if /i "%~1"=="--no-pause" if "%~2"=="" set "PAUSE_ON_ERROR=0"
if "%PAUSE_ON_ERROR%"=="0" goto arguments_done
echo FEHLER: Erlaubter Parameter: --no-pause
exit /b 64

:arguments_done
if "%PAUSE_ON_ERROR%"=="auto" call :detect_double_click

rem cmd.exe cannot use a UNC path as the current directory.
if "%PROJECT_DIR:~0,2%"=="\\" (
    echo FEHLER: Das Programm liegt auf einem Netzwerkpfad und kann von dort nicht
    echo gestartet werden. Bitte den Programmordner auf diesen Computer kopieren.
    set "FAIL_CODE=51"
    goto finish_failure
)

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo FEHLER: Das Anwendungsverzeichnis kann nicht geoeffnet werden.
    set "FAIL_CODE=50"
    goto finish_failure
)

if not exist "%VENV_PYTHON%" (
    echo FEHLER: Das vorbereitete lokale .venv wurde nicht gefunden.
    echo start_app.bat erstellt oder repariert die Umgebung.
    set "FAIL_CODE=2"
    goto finish_failure
)

if not exist "%PROJECT_DIR%start_gui.py" (
    echo FEHLER: start_gui.py wurde nicht gefunden.
    echo Fuer eine vollstaendige Diagnose start_app.bat ausfuehren.
    set "FAIL_CODE=50"
    goto finish_failure
)
if not exist "%PROJECT_DIR%bootstrap.py" (
    echo FEHLER: bootstrap.py wurde nicht gefunden.
    echo Fuer eine vollstaendige Diagnose start_app.bat ausfuehren.
    set "FAIL_CODE=50"
    goto finish_failure
)

rem Only import the mandatory packages; Tesseract is reported by the GUI.
"%VENV_PYTHON%" "%PROJECT_DIR%bootstrap.py" check-packages
set "PACKAGES_CODE=%ERRORLEVEL%"
if not "%PACKAGES_CODE%"=="0" goto packages_failed

rem Own process without a console; start errors go to .launcher-logs\gui.log
rem and a message box.
set "GUI_PYTHON=%VENV_PYTHONW%"
if not exist "%GUI_PYTHON%" set "GUI_PYTHON=%VENV_PYTHON%"
rem start keeps an earlier ERRORLEVEL on success; reset it first.
(call )
start "" "%GUI_PYTHON%" "%PROJECT_DIR%start_gui.py" <nul >nul 2>&1
if not errorlevel 1 exit /b 0
echo FEHLER: Die Anwendung konnte nicht gestartet werden. Diagnose mit start_app.bat.
set "FAIL_CODE=50"
goto finish_failure

:packages_failed
echo FEHLER: Ein erforderliches Python-Paket fehlt oder ist beschaedigt. Code: %PACKAGES_CODE%.
echo quick_start.bat installiert und repariert nichts. start_app.bat ausfuehren:
echo es repariert die Pakete oder nennt die naechsten Schritte.
set "FAIL_CODE=%PACKAGES_CODE%"

:finish_failure
if "%PAUSE_ON_ERROR%"=="1" (
    echo.
    echo Zum Schliessen dieses Fensters eine beliebige Taste druecken...
    pause >nul
)
exit /b %FAIL_CODE%

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
