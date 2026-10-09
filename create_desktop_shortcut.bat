@echo off
setlocal EnableExtensions DisableDelayedExpansion
title PDF Datei Umbenenner - Verknuepfungen

set "PROJECT_DIR=%~dp0"
set "SCRIPT_NAME=%~nx0"
set "PAUSE_AT_END=auto"
set "PDF_RENAMER_SHORTCUT_TARGET=%~dp0start_app.bat"
set "PDF_RENAMER_SHORTCUT_WORKDIR=%~dp0"
set "PDF_RENAMER_SHORTCUT_ICON=%~dp0app_icon.ico,0"
if not exist "%~dp0app_icon.ico" set "PDF_RENAMER_SHORTCUT_ICON=%SystemRoot%\System32\shell32.dll,70"

if /i "%~1"=="--no-pause" set "PAUSE_AT_END=0"
if "%PAUSE_AT_END%"=="auto" call :detect_double_click

if "%PROJECT_DIR:~0,2%"=="\\" (
    echo FEHLER: Das Programm liegt auf einem Netzwerkpfad. Bitte den Programmordner
    echo zuerst auf diesen Computer kopieren und setup.bat dort ausfuehren.
    set "EXIT_CODE=51"
    goto finish
)

if not exist "%PDF_RENAMER_SHORTCUT_TARGET%" (
    echo FEHLER: start_app.bat wurde in "%PROJECT_DIR%" nicht gefunden.
    set "EXIT_CODE=2"
    goto finish
)

set "POWERSHELL_COMMAND=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL_COMMAND%" set "POWERSHELL_COMMAND=powershell.exe"

echo Verknuepfungen "PDF Datei Umbenenner" auf dem Desktop und im Startmenue werden erstellt...
rem DesktopDirectory follows a desktop redirected into OneDrive.
"%POWERSHELL_COMMAND%" -NoLogo -NoProfile -NonInteractive -Command "$ErrorActionPreference = 'Stop'; $shell = New-Object -ComObject WScript.Shell; foreach ($folder in @([Environment]::GetFolderPath('DesktopDirectory'), [Environment]::GetFolderPath('Programs'))) { if ([string]::IsNullOrWhiteSpace($folder)) { throw 'Zielordner wurde nicht gefunden.' }; $shortcut = $shell.CreateShortcut((Join-Path $folder 'PDF Datei Umbenenner.lnk')); $shortcut.TargetPath = $env:PDF_RENAMER_SHORTCUT_TARGET; $shortcut.WorkingDirectory = $env:PDF_RENAMER_SHORTCUT_WORKDIR; $shortcut.Description = 'PDF Datei Umbenenner'; $shortcut.IconLocation = $env:PDF_RENAMER_SHORTCUT_ICON; $shortcut.Save(); Write-Output ('OK: ' + $shortcut.FullName) }"
if errorlevel 1 goto shortcut_failed
set "EXIT_CODE=0"
goto finish

:shortcut_failed
set "EXIT_CODE=%ERRORLEVEL%"
echo FEHLER: Die Verknuepfungen konnten nicht erstellt werden.
echo Moegliche Ursachen: PowerShell oder Windows Script Host ist durch eine
echo Richtlinie gesperrt, oder der Desktop-Ordner ist schreibgeschuetzt.
echo Abhilfe: Rechtsklick auf start_app.bat, "Senden an", "Desktop (Verknuepfung erstellen)".

:finish
if "%PAUSE_AT_END%"=="1" (
    echo.
    echo Zum Schliessen dieses Fensters eine beliebige Taste druecken...
    pause >nul
)
exit /b %EXIT_CODE%

:detect_double_click
rem Explorer and shortcuts run cmd.exe /c with this script; that window closes
rem on exit. A console the user opened has no script name in its command line.
set "PAUSE_AT_END=0"
setlocal EnableDelayedExpansion
set "LAUNCH_LINE=!CMDCMDLINE!"
set "LAUNCH_LINE_WITHOUT_SCRIPT=!LAUNCH_LINE:%SCRIPT_NAME%=!"
if /i "!LAUNCH_LINE_WITHOUT_SCRIPT!"=="!LAUNCH_LINE!" (
    endlocal
    exit /b 0
)
endlocal
set "PAUSE_AT_END=1"
exit /b 0
