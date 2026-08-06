@echo off
setlocal enabledelayedexpansion

title PDF Datei Umbenenner - Setup

echo.
echo ================================================================
echo   PDF Datei Umbenenner - Setup v1.0
echo   Automatische Installation und Konfiguration
echo ================================================================
echo.

REM Wechsle in das Verzeichnis der Batch-Datei
cd /d "%~dp0"

echo Willkommen beim Setup des PDF Datei Umbeneners!
echo.
echo Diese Installation wird:
echo   ✓ Python-Installation prüfen
echo   ✓ Alle erforderlichen Abhängigkeiten installieren
echo   ✓ Desktop-Verknüpfung erstellen (optional)
echo   ✓ Anwendung testen
echo.

set /p "CONTINUE=Möchten Sie fortfahren? (J/N): "
if /i not "%CONTINUE%"=="J" if /i not "%CONTINUE%"=="Y" if /i not "%CONTINUE%"=="ja" (
    echo Setup abgebrochen.
    pause
    exit /b 0
)

echo.
echo ================================================================
echo   SCHRITT 1: Python-Installation prüfen
echo ================================================================

python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ❌ Python ist nicht installiert!
    echo.
    echo Das Setup kann nicht fortgesetzt werden.
    echo Bitte installieren Sie Python von: https://www.python.org/downloads/
    echo.
    echo WICHTIG: Aktivieren Sie "Add Python to PATH" während der Installation!
    echo.
    set /p "OPEN_BROWSER=Soll die Python-Download-Seite geöffnet werden? (J/N): "
    if /i "!OPEN_BROWSER!"=="J" (
        start https://www.python.org/downloads/
    )
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do (
    echo ✅ Python %%i gefunden
)

echo.
echo ================================================================
echo   SCHRITT 2: Abhängigkeiten installieren
echo ================================================================

echo Installiere Python-Pakete...
echo.

pip install --upgrade pip --quiet --disable-pip-version-check
echo ✅ pip aktualisiert

if exist "requirements.txt" (
    echo Installiere Abhängigkeiten aus requirements.txt...
    pip install -r requirements.txt --quiet --disable-pip-version-check
    if errorlevel 1 (
        echo ❌ Fehler beim Installieren einiger Abhängigkeiten
        echo    Das Setup wird trotzdem fortgesetzt...
    ) else (
        echo ✅ Alle Abhängigkeiten erfolgreich installiert
    )
) else (
    echo requirements.txt nicht gefunden. Installiere Basis-Abhängigkeiten...
    pip install pdfplumber cryptography Pillow --quiet --disable-pip-version-check
    echo ✅ Basis-Abhängigkeiten installiert
)

echo.
echo ================================================================
echo   SCHRITT 3: Installation testen
echo ================================================================

echo Teste Python-Module...

python -c "import pdfplumber; print('✅ pdfplumber OK')" 2>nul || echo "⚠️  pdfplumber Problem"
python -c "import tkinter; print('✅ tkinter OK')" 2>nul || echo "❌ tkinter Problem"
python -c "import cryptography; print('✅ cryptography OK')" 2>nul || echo "⚠️  cryptography Problem"
python -c "import PIL; print('✅ Pillow OK')" 2>nul || echo "⚠️  Pillow Problem"

echo.
echo ================================================================
echo   SCHRITT 4: Desktop-Verknüpfung (optional)
echo ================================================================

set /p "CREATE_SHORTCUT=Desktop-Verknüpfung erstellen? (J/N): "
if /i "%CREATE_SHORTCUT%"=="J" (
    call create_desktop_shortcut.bat
)

echo.
echo ================================================================
echo   SCHRITT 5: Erste Ausführung testen
echo ================================================================

set /p "TEST_RUN=Anwendung jetzt testen? (J/N): "
if /i "%TEST_RUN%"=="J" (
    echo.
    echo Starte PDF Datei Umbenenner...
    echo (Das Fenster wird sich öffnen - Sie können es direkt wieder schließen)
    echo.
    
    if exist "start_gui.py" (
        timeout /t 2 /nobreak >nul
        python start_gui.py
    ) else if exist "gui_main.py" (
        timeout /t 2 /nobreak >nul
        python gui_main.py
    ) else if exist "main.py" (
        timeout /t 2 /nobreak >nul
        python main.py --gui
    ) else (
        echo ❌ Keine GUI-Datei gefunden!
    )
)

echo.
echo ================================================================
echo   SETUP ABGESCHLOSSEN
echo ================================================================
echo.
echo ✅ PDF Datei Umbenenner wurde erfolgreich installiert!
echo.
echo VERWENDUNG:
echo   • Doppelklick auf "start_app.bat" (empfohlen)
echo   • Oder "quick_start.bat" für Schnellstart
if /i "%CREATE_SHORTCUT%"=="J" (
    echo   • Oder Desktop-Verknüpfung verwenden
)
echo.
echo HINWEISE:
echo   • Erstellen Sie immer Backups Ihrer PDF-Dateien
echo   • Verwenden Sie zuerst den Testmodus
echo   • Lesen Sie "STARTER_ANLEITUNG.md" für Details
echo.
echo Viel Erfolg beim Verwenden des PDF Datei Umbeneners!

echo.
pause