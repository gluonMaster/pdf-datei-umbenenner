@echo off
title PDF Datei Umbenenner - Quick Start

echo.
echo ================================================================
echo   PDF Datei Umbenenner - Quick Start
echo ================================================================
echo.

REM Wechsle in das Verzeichnis der Batch-Datei
cd /d "%~dp0"

REM Schnelle Python-Prüfung
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python nicht gefunden! Verwenden Sie start_app.bat für eine vollständige Prüfung.
    pause
    exit /b 1
)

echo ✅ Python gefunden
echo 🚀 Starte PDF Datei Umbenenner...
echo.

REM Versuche GUI zu starten (in Reihenfolge der Priorität)
if exist "start_gui.py" (
    python start_gui.py
) else if exist "gui_main.py" (
    python gui_main.py
) else if exist "gui_minimal.py" (
    python gui_minimal.py
) else if exist "main.py" (
    python main.py --gui
) else (
    echo ❌ Keine GUI-Datei gefunden!
    echo    Verwenden Sie start_app.bat für eine vollständige Diagnose.
    pause
    exit /b 1
)

if errorlevel 1 (
    echo.
    echo ❌ Fehler beim Starten!
    echo    Mögliche Ursachen: Fehlende Abhängigkeiten
    echo    Lösung: Verwenden Sie start_app.bat für automatische Installation
    echo.
    pause
)

echo.
echo Anwendung beendet.
timeout /t 2 /nobreak >nul