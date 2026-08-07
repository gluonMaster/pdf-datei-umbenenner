@echo off
setlocal EnableExtensions DisableDelayedExpansion
title PDF Datei Umbenenner - Quick Start

set "PROJECT_DIR=%~dp0"
set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo FEHLER: Das Anwendungsverzeichnis kann nicht geoeffnet werden.
    exit /b 50
)

if not exist "%VENV_PYTHON%" (
    echo FEHLER: Das vorbereitete lokale .venv wurde nicht gefunden.
    echo start_app.bat erstellt oder repariert die Umgebung.
    exit /b 2
)

if not exist "%PROJECT_DIR%start_gui.py" (
    echo FEHLER: start_gui.py wurde nicht gefunden.
    echo Fuer eine vollstaendige Diagnose start_app.bat ausfuehren.
    exit /b 50
)

"%VENV_PYTHON%" "%PROJECT_DIR%start_gui.py"
exit /b %ERRORLEVEL%
