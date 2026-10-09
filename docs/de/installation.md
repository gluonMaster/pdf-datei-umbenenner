# Installation und Betrieb

[English](../en/installation.md) · **Deutsch** · [Русский](../ru/installation.md) · [← README](../../README.de.md)

PDF Datei Umbenenner läuft auf gewöhnlichen Büro-PCs mit Windows 10 und 11. Für die Installation sind keine Administratorrechte nötig: Das Programm liegt in einem eigenen Ordner mit einer eigenen Python-Umgebung (`.venv`). Nur Tesseract OCR wird als eigenständiges Programm installiert.

- [Voraussetzungen](#voraussetzungen)
- [1. Speicherort wählen](#1-speicherort-wählen)
- [2. Tesseract OCR installieren](#2-tesseract-ocr-installieren)
- [3. setup.bat ausführen](#3-setupbat-ausführen)
- [4. Täglicher Start](#4-täglicher-start)
- [5. Installation aktualisieren](#5-installation-aktualisieren)
- [Rückgabecodes](#rückgabecodes)
- [Fehlerbehebung](#fehlerbehebung)

## Voraussetzungen

| Komponente | Einzelheiten |
| --- | --- |
| Windows | 10 oder 11. Die automatische Python-Installation wird unter 64-Bit-Windows für Intel-/AMD-Prozessoren angeboten. |
| Internet | Nur während der Installation (Python, Python-Pakete, Tesseract). |
| Python | 3.10–3.15 mit Tcl/Tk. Fehlt es, installiert `setup.bat` nach Rückfrage Python 3.14. |
| Tesseract OCR | Version 5 mit den deutschen Sprachdaten `deu` (`eng` gehört zur Standardinstallation, `osd` wird empfohlen). |
| Schülerverzeichnis | Die eigene `Schuler.csv` im Programmordner (siehe [Benutzerhandbuch](user-guide.md#schülerverzeichnis)). |

Ohne Tesseract startet das Programm, Schülerverzeichnis und Prüfliste funktionieren, die Verarbeitung — auch im Testmodus — bleibt aber gesperrt.

## 1. Speicherort wählen

1. **Das ZIP vor dem Entpacken entsperren:** Rechtsklick auf die heruntergeladene Datei → *Eigenschaften* → *Zulassen* ankreuzen → *OK*. Sonst markiert Windows jede entpackte Datei als „aus dem Internet“ und zeigt bei jeder BAT-Datei eine Sicherheitswarnung. `setup.bat` entfernt diese Markierung von den Programmdateien zwar selbst, der allererste Start von `setup.bat` fragt aber trotzdem nach.
2. **Empfohlener Ordner:** ein lokaler Ordner des Benutzers, z. B. `C:\Users\<Name>\PDF-Datei-Umbenenner` (`%USERPROFILE%\PDF-Datei-Umbenenner`).
3. **Keine Netzwerkpfade.** Ein Ordner wie `\\server\freigabe\…` kann nicht als Arbeitsverzeichnis von `cmd.exe` dienen; alle Starter brechen sofort mit Rückgabecode `51` ab und empfehlen, das Programm auf den PC zu kopieren.
4. **Keine OneDrive-Ordner** (auch kein nach OneDrive umgeleiteter Desktop). Die Synchronisierung der tausenden Dateien in `.venv` führt zu gesperrten Dateien und fehlgeschlagenen Paketinstallationen. `setup.bat` erkennt einen Speicherort innerhalb von `%OneDrive%`, `%OneDriveCommercial%` oder `%OneDriveConsumer%`, erklärt das Problem und fragt, ob trotzdem fortgefahren werden soll; bei *Nein* endet das Setup mit Code `52`, bevor etwas installiert wird.
5. Die eigene `Schuler.csv` in den Programmordner legen. Ohne sie schlägt die Prüfung am Ende des Setups mit Code `24` fehl. Zum Ausprobieren kann man mit einer Kopie von [`demo/Schuler.csv`](../../demo/Schuler.csv) beginnen.

## 2. Tesseract OCR installieren

Die [Tesseract-Dokumentation](https://tesseract-ocr.github.io/tessdoc/Installation.html) verweist für Windows auf den Installer der UB Mannheim:

1. Die [Seite der UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki) öffnen und den aktuellen 64-Bit-Installer `tesseract-ocr-w64-setup-….exe` herunterladen.
2. Den Installer starten und den vorgeschlagenen Installationsordner beibehalten.
3. In der Komponentenauswahl unter *Additional language data* den Eintrag **German** ankreuzen. Englisch und `osd` sind standardmäßig enthalten.
4. Ist Tesseract schon ohne Deutsch installiert: Installer erneut starten und *German* nachwählen oder `deu.traineddata` aus [tessdata](https://github.com/tesseract-ocr/tessdata) in den Ordner `tessdata` neben `tesseract.exe` kopieren.

Das Programm sucht `tesseract.exe` in `TESSERACT_CMD`, im `PATH`, in `%ProgramFiles%\Tesseract-OCR`, `%ProgramFiles(x86)%\Tesseract-OCR`, `%LOCALAPPDATA%\Programs\Tesseract-OCR` (Installation nur für den aktuellen Benutzer) und `%LOCALAPPDATA%\Tesseract-OCR`. Für jeden anderen Ordner die Umgebungsvariable `TESSERACT_CMD` auf den vollständigen Pfad von `tesseract.exe` setzen. Ohne `osd` werden seitlich liegende Scans schlechter erkannt; die Prüfung zeigt dann nur eine Warnung.

`setup.bat` startet den Tesseract-Installer nicht selbst: Die offizielle Dokumentation beschreibt nur die manuelle Installation, und die Signatur dieses Installers lässt sich nicht so prüfen wie die der Python- und Microsoft-Pakete.

## 3. setup.bat ausführen

`setup.bat` doppelklicken. Das interaktive Setup:

1. **sucht Python** in dieser Reihenfolge: `py -3.14`, `%LOCALAPPDATA%\Programs\Python\Python314\python.exe`, `py -3`, `python` im `PATH`. Ein Kandidat zählt nur, wenn er tatsächlich startet und Version 3.10–3.15 meldet — der Platzhalter des Microsoft Store `WindowsApps\python.exe` (Rückgabecode 9009) wird ignoriert. Gibt es nur ein neueres Python, sagt das Setup das und bietet die festgelegte Version an;
2. **installiert Python bei Bedarf** (nur nach ausdrücklichem *j*): siehe [Automatische Python-Installation](#automatische-python-installation);
3. **legt die eigene Umgebung** `.venv` an (keine Administratorrechte nötig);
4. **installiert die Pakete** aus `requirements.txt` (`pdfplumber`, `PyMuPDF`, `Pillow`) ausschließlich als fertige Wheels (`--only-binary=:all:`) — ein Bau aus dem Quellcode würde auf Büro-PCs ohne Compiler ohnehin scheitern. Danach prüft es die Pakete, die Programmimporte, `subjects_config.json` und `Schuler.csv`. Beschädigte Pakete werden einmal ohne Cache neu installiert. Erst nach erfolgreicher Prüfung schreibt das Setup den Stempel `.venv\.requirements.sha256`;
5. **prüft Tesseract** mit `deu` und `eng`. Fehlt etwas, zeigt es eine deutsche Schritt-für-Schritt-Anleitung, öffnet nach Zustimmung die Download-Seite, wartet auf *Enter* und prüft erneut. Ist Tesseract danach immer noch nicht bereit, endet das Setup mit Code `31`, `32` oder `33` und der Meldung `SETUP UNVOLLSTAENDIG: VERARBEITUNG NICHT VERFUEGBAR`;
6. **bietet Komfortfunktionen an:** Verknüpfungen auf dem Desktop und im Startmenü sowie den Start des Programms;
7. **wartet am Ende auf eine Taste**, wenn es per Doppelklick oder Verknüpfung gestartet wurde, damit Ergebnis und Fehler lesbar bleiben. Aus einer geöffneten Konsole gestartet, wartet es nicht.

Kann PyMuPDF wegen einer fehlenden Microsoft-Visual-C++-Laufzeit nicht geladen werden (Code `34`), werden die Pakete zunächst einmal neu installiert. Hilft das nicht, fragt das interaktive Setup gesondert, ob das Microsoft VC++ Redistributable installiert werden soll; dessen digitale Signatur wird geprüft, und die Konsole muss als Administrator gestartet worden sein (es werden keine Rechte angefordert). Ablehnung oder Fehler enden mit Code `34`.

### Automatische Python-Installation

Wird kein geeignetes Python gefunden, bietet das Setup die Installation an. Nach Zustimmung erledigt `install_python.ps1` Folgendes:

- lädt den festgelegten Installer **Python 3.14.8 (64-Bit)** von python.org: `https://www.python.org/ftp/python/3.14.8/python-3.14.8-amd64.exe`;
- prüft vor dem Start die Authenticode-Signatur: Status *Valid* und Herausgeber `CN=Python Software Foundation`. Andernfalls wird die Datei gelöscht und nie ausgeführt;
- installiert still nur für den aktuellen Benutzer nach `%LOCALAPPDATA%\Programs\Python\Python314` mit Tcl/Tk: `/quiet InstallAllUsers=0 PrependPath=0 Include_launcher=0 AssociateFiles=0 Shortcuts=0 Include_tcltk=1 Include_pip=1 Include_doc=0 Include_test=0`. Es sind keine Administratorrechte nötig, und der `PATH` bleibt unverändert. Das Protokoll des Installers liegt in `.launcher-logs\python-installer.log`;
- danach findet das Setup den Interpreter unter diesem Pfad und fährt fort.

Der Starter `py` wird bewusst nicht installiert: Seine Variante „nur für mich“ setzt ihren Ordner an den Anfang des Benutzer-`PATH` und würde den Befehl `py` des neuen [Python install manager](https://docs.python.org/3/using/windows.html#python-install-manager) verdecken. Den klassischen Installer gibt es für Python 3.14 und 3.15; ab 3.16 bietet python.org nur noch den Install Manager an. Implizite Laufzeit-Downloads durch den Install Manager sind in allen Startern abgeschaltet (`PYTHON_MANAGER_AUTOMATIC_INSTALL=false`).

Ablehnung, ein Download-Fehler, eine ungültige Signatur oder eine fehlgeschlagene Installation führen zur Anleitung für die manuelle Installation, zum Angebot, die [Python-Downloadseite](https://www.python.org/downloads/windows/) zu öffnen, und zu Rückgabecode `10`. Unter 32-Bit-Windows und ARM64 erklärt das Setup, dass die automatische Installation dort nicht verfügbar ist. `--unattended` und `--core-only` installieren nie Python.

Die benötigten Wheels gibt es für Python 3.10, 3.14 und 3.15 unter `win_amd64`, für 3.14 außerdem unter `win32` und `win_arm64` (geprüft mit `pip download --only-binary=:all:`).

### Betrieb ohne Rückfragen

```cmd
setup.bat --unattended
setup.bat --core-only
setup.bat --no-pause
```

- `--unattended` — ohne Rückfragen, ohne Systeminstallationen (Python, Tesseract, VC++-Laufzeit) und ohne Warten auf eine Taste. In `.venv` werden nur Python-Pakete installiert. Rückgabecode `0` nur, wenn Pakete, Tesseract und `deu` bereit sind; sonst ein bestimmter Code ungleich null.
- `--core-only` — nur Python-Pakete; Tesseract wird nicht geprüft, die Zusammenfassung erinnert daran, dass die Verarbeitung ihn braucht. Rückgabecode `0`, wenn die Pakete bereit sind.
- `--no-pause` — für Automatisierung: am Ende nicht auf eine Taste warten (mit den anderen Optionen kombinierbar).

## 4. Täglicher Start

Die Verknüpfung **PDF Datei Umbenenner** oder `start_app.bat` verwenden. Der Starter

- nutzt nur die eigene `.venv`, vergleicht den Stempel von `requirements.txt` und prüft Pakete und Daten;
- repariert die Umgebung nur bei Problemen mit Python-Paketen: nach geänderter `requirements.txt` eine normale Installation und ein neuer Stempel, bei beschädigtem Paket (`20`, `22`, `35`) eine erzwungene Neuinstallation mit erneuter Prüfung. Probleme mit `subjects_config.json`, `Schuler.csv` oder tkinter/Tcl starten nie `pip`; eine fehlende VC++-Laufzeit (`34`) verweist auf `setup.bat`;
- startet nie `pip`, weil Tesseract fehlt: Er zeigt eine Warnung, das Programm öffnet sich, und die Registerkarte *Verarbeitung* erklärt, dass die Verarbeitung gesperrt ist. Nach der Installation von Tesseract ist kein Neustart nötig;
- startet das Programm als eigenen Prozess über `.venv\Scripts\pythonw.exe` und schließt sich sofort — die Konsole ist nur während Prüfungen und Reparaturen sichtbar, und ihr Schließen beendet das Programm nicht mehr. Ausgaben und Startfehler des Programms stehen in `.launcher-logs\gui.log`; kann das Programm nicht starten, erscheint zusätzlich ein Meldungsfenster;
- wartet auf eine Taste, wenn er per Doppelklick oder Verknüpfung gestartet wurde und ein Fehler auftritt (`--no-pause` schaltet das ab).

Ein zweiter Start bei bereits geöffnetem Fenster (etwa ein zweiter Doppelklick auf die Verknüpfung) öffnet keine zweite Instanz, die mit denselben Dateien arbeitet: Es erscheint die Meldung *PDF Datei Umbenenner ist bereits geöffnet*, und nach *OK* wird das geöffnete Fenster wiederhergestellt und in den Vordergrund geholt. Die Sperre ist ein benannter Windows-Mutex für den Programmordner; nach einem Absturz muss nichts aufgeräumt werden, und Kopien des Programms in anderen Ordnern behindern sich nicht.

Das Fenster öffnet sich mit 1200×760 in der Bildschirmmitte. Passt es nicht — etwa bei 1366×768 oder bei 1920×1080 mit 150 % Skalierung (etwa 1280×720 logische Pixel) —, öffnet es sich maximiert, die Überschrift über den Registerkarten entfällt und die Schülerliste in der Registerkarte *Prüfung* wird kleiner, sodass alle Felder und Schaltflächen sichtbar bleiben. Das Programm meldet keine Hoch-DPI-Unterstützung an: Bei 125/150 % skaliert Windows das ganze Fenster (etwas weichere Schrift, gleiche Anordnung).

Starter prüfen, ohne das Programm zu öffnen (Code `0`, wenn das Programm starten kann; fehlende OCR ist nur eine Warnung):

```cmd
start_app.bat --health-only
```

`quick_start.bat` installiert und repariert nichts. Es prüft nur, ob `.venv` vorhanden ist (sonst Code `2`) und ob sich die Pflichtpakete importieren lassen (`bootstrap.py check-packages`, etwa 0,3 s; sonst eine verständliche Meldung und Code `20`, `34` oder `35`), und startet das Programm dann auf dieselbe Weise. Ist die Umgebung beschädigt, `start_app.bat` verwenden.

`create_desktop_shortcut.bat` (vom Setup angeboten) legt die Verknüpfung **PDF Datei Umbenenner** auf dem Desktop — auch bei nach OneDrive umgeleitetem Desktop — und im Startmenü des Benutzers an. Sie zeigt auf `start_app.bat`, behält den Programmordner als Arbeitsverzeichnis und nutzt das Programmsymbol. Sind PowerShell oder Windows Script Host per Richtlinie gesperrt oder ist der Ordner schreibgeschützt, wird der manuelle Weg erklärt (*Senden an → Desktop*).

## 5. Installation aktualisieren

1. Das Programm schließen.
2. Die Programmdateien ersetzen (`*.py`, `*.bat`, `*.ps1`, `requirements*.txt`, `app_icon.*`, `README*.md`, `docs\`). Die eigenen Daten nicht überschreiben: `Schuler.csv`, `Lehrer.csv`, `student_aliases.json`, `subjects_aliases.json` und die Dateien `manual_review.json` neben den Scans. Wurde die Fächerliste in `subjects_config.json` geändert, die eigene Datei behalten und neue Einträge von Hand oder mit *Neues Fach übernehmen* in der Prüfung ergänzen.
3. Das Programm wie gewohnt starten. Eine geänderte `requirements.txt` wird am Stempel erkannt, die Pakete werden einmal installiert. Starter und Setup ändern nie CSV, JSON, Prüfliste oder Scans.
4. Ist Tesseract mit `deu` noch nicht installiert: installieren und mit `setup.bat` oder `.venv\Scripts\python.exe bootstrap.py check-ocr` prüfen.

## Rückgabecodes

| Code | Bedeutung |
| --- | --- |
| `0` | Bereit / erfolgreich |
| `2` | `quick_start.bat`: `.venv` fehlt |
| `10` | Kein unterstütztes Python gefunden (und keines installiert) |
| `20` | Pflichtpaket fehlt oder ist beschädigt (`pdfplumber`, `PyMuPDF`, `Pillow`) |
| `21` | tkinter/Tcl nicht verfügbar |
| `22` | Import des Programms fehlgeschlagen |
| `23` | `subjects_config.json` unlesbar — die Datei wird nicht geändert, Pakete werden nicht neu installiert |
| `24` | `Schuler.csv` fehlt oder ist unlesbar — die Datei wird nicht geändert, Pakete werden nicht neu installiert |
| `31` | Tesseract nicht gefunden |
| `32` | Sprachdaten `deu` oder `eng` fehlen |
| `33` | OCR-Prüfung fehlgeschlagen |
| `34` | PyMuPDF lädt nicht: Windows-Laufzeit/DLL (Microsoft VC++) |
| `35` | Anderer Importfehler von PyMuPDF |
| `40` | `requirements.txt` geändert oder Stempel fehlt (intern) |
| `50` | Fehler in Bootstrap, Umgebung oder Diagnose, auch ein fehlgeschlagener Start des Programmprozesses |
| `51` | Programm liegt auf einem Netzwerkpfad (UNC) |
| `52` | Setup wegen OneDrive-Speicherort abgebrochen |
| `64` | Ungültiger Parameter eines Starters |

Das Setup liefert `31`–`33`, wenn die Pakete bereit sind, OCR aber nicht; `start_app.bat` und `quick_start.bat` starten das Programm in diesem Fall trotzdem. Fehlercodes von `pip` werden unverändert weitergegeben. Den Rückgabecode des Programms selbst geben die Starter nicht mehr weiter, weil es als eigener Prozess läuft. Die internen Codes von `install_python.ps1` (`70` Download, `71` Signatur, `72` Installer, `73` danach kein `python.exe`) stehen nur im Setup-Protokoll; das Setup selbst liefert `10`. Code `30` (früher „optionales PyMuPDF fehlt“) ist stillgelegt und wird nicht wiederverwendet.

## Fehlerbehebung

| Symptom | Abhilfe |
| --- | --- |
| Windows warnt bei jeder BAT-Datei | Das ZIP wurde nicht entsperrt. `setup.bat` einmal ausführen (es entfernt die Markierung) oder das ZIP entsperren und erneut entpacken. |
| Das Setup meldet fehlendes Python | Mit *j* antworten, um Python 3.14 automatisch zu installieren, oder Python von python.org mit *tcl/tk and IDLE* installieren und das Setup erneut starten. |
| *OCR nicht verfügbar – Verarbeitung gesperrt* | Tesseract mit deutschen Sprachdaten installieren (Abschnitt 2), danach mit `setup.bat` prüfen. |
| Code `34` (Visual C++) | `setup.bat` ohne Parameter in einer als Administrator gestarteten Konsole ausführen und die VC++-Laufzeit annehmen. |
| Das Programm startet nicht | `.launcher-logs\gui.log` lesen; bei Problemen der Starter `.launcher-logs\start_app.log` oder `setup.log`. |
| Die Umgebung ist beschädigt | Programm schließen und `start_app.bat` ausführen. Eine unbrauchbare `.venv` bleibt als `.bootstrap\venv-backup-<id>` erhalten, eine neue wird angelegt. Für einen vollständigen Neuaufbau nur `.venv` löschen und `setup.bat` erneut ausführen — Tesseract bleibt installiert. |
| `manual_review.json` ist beschädigt | Das Programm überschreibt die Datei nicht. Programm schließen, Datei und Ordner `manual_check` an einen sicheren Ort kopieren, jedes PDF seinem Eintrag zuordnen und dann die JSON-Datei reparieren oder durch `{"review_items": []}` ersetzen. |

Alle Diagnoseprotokolle sind UTF-8-Dateien in `.launcher-logs\`: `setup.log`, `start_app.log`, `gui.log` und `python-installer.log`.
