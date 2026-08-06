@echo off
title PDF Datei Umbenenner - Desktop Verknüpfung erstellen

echo.
echo ================================================================
echo   PDF Datei Umbenenner - Desktop Verknüpfung
echo ================================================================
echo.

REM Aktueller Pfad
set "CURRENT_DIR=%~dp0"

REM Desktop-Pfad ermitteln
set "DESKTOP=%USERPROFILE%\Desktop"

echo Erstelle Desktop-Verknüpfung...
echo.
echo Aktueller Ordner: %CURRENT_DIR%
echo Desktop: %DESKTOP%
echo.

REM VBScript zur Erstellung der Verknüpfung
set "VBS_FILE=%TEMP%\create_shortcut.vbs"

echo Set oWS = WScript.CreateObject("WScript.Shell") > "%VBS_FILE%"
echo sLinkFile = "%DESKTOP%\PDF Datei Umbenenner.lnk" >> "%VBS_FILE%"
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%VBS_FILE%"
echo oLink.TargetPath = "%CURRENT_DIR%start_app.bat" >> "%VBS_FILE%"
echo oLink.WorkingDirectory = "%CURRENT_DIR%" >> "%VBS_FILE%"
echo oLink.Description = "PDF Datei Umbenenner - Automatisches Umbenennen von PDF-Formularen" >> "%VBS_FILE%"
echo oLink.IconLocation = "%%SystemRoot%%\System32\shell32.dll,70" >> "%VBS_FILE%"
echo oLink.Save >> "%VBS_FILE%"

REM VBScript ausführen
cscript //nologo "%VBS_FILE%"

REM Temporäre Datei löschen
del "%VBS_FILE%"

if exist "%DESKTOP%\PDF Datei Umbenenner.lnk" (
    echo ✅ Desktop-Verknüpfung erfolgreich erstellt!
    echo.
    echo Die Verknüpfung "PDF Datei Umbenenner" wurde auf Ihrem Desktop erstellt.
    echo Sie können jetzt die Anwendung direkt vom Desktop starten.
) else (
    echo ❌ Fehler beim Erstellen der Verknüpfung!
    echo.
    echo Mögliche Ursachen:
    echo 1. Keine Berechtigung zum Schreiben auf den Desktop
    echo 2. Antivirus-Software blockiert die Erstellung
    echo.
    echo Versuchen Sie:
    echo 1. Diese Batch-Datei als Administrator auszuführen
    echo 2. Antivirus-Software temporär zu deaktivieren
    echo 3. Manuell eine Verknüpfung zu erstellen
)

echo.
echo Drücken Sie eine beliebige Taste zum Beenden...
pause >nul