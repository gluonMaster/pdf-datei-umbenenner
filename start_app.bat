@echo off
title PDF Renamer - Smart Launcher

echo ===============================================
echo    PDF RENAMER - SMART LAUNCHER v2.0
echo ===============================================
echo.

cd /d "%~dp0"

echo [1/4] Pruefe Python Installation...
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo FEHLER: Python nicht installiert oder nicht im PATH!
    echo Bitte installieren Sie Python von https://python.org
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do (
    echo OK - Python %%i gefunden
)

echo [2/4] Pruefe pip...
pip --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo FEHLER: pip nicht verfuegbar!
    pause
    exit /b 1
)
echo OK - pip verfuegbar

echo [3/4] Installiere/pruefe Dependencies...
echo   pdfplumber...
pip install pdfplumber --quiet >nul 2>&1
echo   cryptography...
pip install cryptography --quiet >nul 2>&1
echo   Pillow...
pip install Pillow --quiet >nul 2>&1
echo OK - Dependencies installiert

echo [4/4] Suche GUI-Datei...
set GUI_FILE=""
if exist "start_gui.py" (
    set GUI_FILE=start_gui.py
    echo OK - start_gui.py gefunden
) else if exist "gui_main.py" (
    set GUI_FILE=gui_main.py
    echo OK - gui_main.py gefunden
) else if exist "gui_minimal.py" (
    set GUI_FILE=gui_minimal.py
    echo OK - gui_minimal.py gefunden
) else if exist "gui_standalone.py" (
    set GUI_FILE=gui_standalone.py
    echo OK - gui_standalone.py gefunden
) else if exist "main.py" (
    set GUI_FILE=main.py
    echo OK - main.py gefunden
) else (
    echo FEHLER: Keine GUI-Datei gefunden!
    echo Benoetigte Dateien: start_gui.py, gui_main.py, gui_minimal.py, gui_standalone.py oder main.py
    pause
    exit /b 1
)

echo.
echo ===============================================
echo   Starte PDF Renamer Application...
echo ===============================================
echo.
echo HINWEIS: Die Anwendung oeffnet sich in einem separaten Fenster.
echo          Dieses Launcher-Fenster kann danach geschlossen werden.
echo.

:: Starte in neuem Fenster
start "PDF Renamer Application" python "%GUI_FILE%"

if %ERRORLEVEL% equ 0 (
    echo Anwendung wurde erfolgreich gestartet!
    echo.
    echo Falls das Anwendungsfenster nicht erscheint:
    echo 1. Verwenden Sie quick_start.bat
    echo 2. Oder starten Sie manuell: python %GUI_FILE%
    echo.
) else (
    echo Fehler beim Starten in neuem Fenster!
    echo Versuche direkten Start...
    echo.
    python "%GUI_FILE%"
)

echo Druecken Sie eine beliebige Taste zum Beenden...
pause >nul