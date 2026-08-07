@echo off
setlocal EnableExtensions DisableDelayedExpansion
title PDF Datei Umbenenner - Desktop Shortcut

set "PROJECT_DIR=%~dp0"
set "PDF_RENAMER_SHORTCUT_TARGET=%~dp0start_app.bat"
set "PDF_RENAMER_SHORTCUT_WORKDIR=%~dp0"

if not exist "%PDF_RENAMER_SHORTCUT_TARGET%" (
    echo FEHLER: start_app.bat wurde in "%PROJECT_DIR%" nicht gefunden.
    exit /b 2
)

echo Desktop-Verknuepfung fuer start_app.bat wird erstellt...
powershell.exe -NoProfile -NonInteractive -Command "$ErrorActionPreference = 'Stop'; $shell = New-Object -ComObject WScript.Shell; $desktop = [Environment]::GetFolderPath('DesktopDirectory'); if ([string]::IsNullOrWhiteSpace($desktop)) { throw 'Desktop directory was not found.' }; $shortcut = $shell.CreateShortcut((Join-Path $desktop 'PDF Datei Umbenenner.lnk')); $shortcut.TargetPath = $env:PDF_RENAMER_SHORTCUT_TARGET; $shortcut.WorkingDirectory = $env:PDF_RENAMER_SHORTCUT_WORKDIR; $shortcut.Description = 'PDF Datei Umbenenner'; $shortcut.IconLocation = (Join-Path $env:SystemRoot 'System32\shell32.dll') + ',70'; $shortcut.Save()"
if errorlevel 1 goto shortcut_failed

echo OK: Desktop-Verknuepfung fuer "%PDF_RENAMER_SHORTCUT_TARGET%" erstellt.
exit /b 0

:shortcut_failed
set "SHORTCUT_EXIT=%ERRORLEVEL%"
echo FEHLER: Die Desktop-Verknuepfung konnte nicht erstellt werden.
echo Pruefen Sie die Schreibberechtigung fuer den Desktop-Ordner.
exit /b %SHORTCUT_EXIT%
