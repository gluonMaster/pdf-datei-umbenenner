# Benutzerhandbuch

[English](../en/user-guide.md) · **Deutsch** · [Русский](../ru/user-guide.md) · [← README](../../README.de.md)

Das Programmfenster hat drei Registerkarten: **Verarbeitung** (einen Ordner mit Scans verarbeiten), **Prüfung** (Dokumente, bei denen sich das Programm nicht sicher war) und **Schüler** (das Schülerverzeichnis).

- [Einen Ordner verarbeiten](#einen-ordner-verarbeiten)
- [Dateinamen](#dateinamen)
- [Unterstützte Formulare](#unterstützte-formulare)
- [Prüfung](#prüfung)
- [Schülerverzeichnis](#schülerverzeichnis)
- [Fächer](#fächer)
- [Dateien des Programms](#dateien-des-programms)

## Einen Ordner verarbeiten

![Registerkarte Verarbeitung nach einem Durchlauf](../images/processing.png)

1. Den Ordner mit den gescannten PDFs wählen (*Eingabeordner*, Schaltfläche *Durchsuchen…*).
2. *Monat* und *Jahr* wählen. Das Fenster öffnet sich mit dem aktuellen Monat; der Zeitraum geht in jeden Dateinamen ein.
3. Bei Bedarf *Testmodus (keine Änderungen)* ankreuzen.
4. **Verarbeitung starten** klicken.

Die Einstellungen werden beim Start des Durchlaufs festgehalten. Während er läuft, sind Ordner, Zeitraum und alle Änderungen am Verzeichnis gesperrt. *Stoppen* beendet das aktuelle PDF und hält zwischen zwei Dateien an; das Schließen des Fensters während eines Durchlaufs bietet dasselbe sichere Anhalten an.

Das Protokoll listet jedes Dokument auf:

- ✅ umbenannt — `Scan_…pdf → Nachname_Vorname_Fach_Monat_Jahr.pdf`, im selben Ordner;
- ⚠️ ein Hinweis, der nicht blockiert, z. B. *Geburtsdatum … in das Schülerverzeichnis übernommen* oder eine automatische OCR-Korrektur des Namens;
- 📋 zur Prüfung — das PDF wird in den Unterordner `manual_check` verschoben, Grund und vorbereiteter Vorschlag werden in `manual_review.json` im Scan-Ordner gespeichert.

Der **Testmodus** berechnet den vollständigen Plan mit denselben Namen, verschiebt aber kein PDF, ändert weder `Schuler.csv` noch Aliasnamen und speichert keine Prüfliste.

Die Verarbeitung braucht Tesseract OCR. Fehlt es, zeigen Protokoll und Statuszeile *OCR nicht verfügbar – Verarbeitung gesperrt*; die anderen Registerkarten funktionieren weiter. Siehe [Installation](installation.md#2-tesseract-ocr-installieren).

## Dateinamen

Das Format ist immer:

```text
Nachname_Vorname_Fach_Monat_Jahr.pdf
```

```text
Beispiel_Lena_Mathematik_September_2026.pdf
```

- Nachname und Vorname stammen aus dem Verzeichnis, nicht aus dem OCR-Text — Lesefehler gelangen so nie in einen Dateinamen.
- Das Fach ist der kanonische Name aus der Fächerliste (`Mathe` → `Mathematik`).
- Der Monat wird auf Deutsch geschrieben. Monat und Jahr stammen **ausschließlich** aus der Auswahl im Programm (oder den Parametern der Kommandozeile) — nie aus Datumsangaben auf dem Formular, der Systemuhr oder dem ursprünglichen Dateinamen.
- Eine vorhandene Datei wird nie überschrieben. Es wird der erste freie Zusatz verwendet: `…_2026.pdf`, `…_2026_1.pdf`, `…_2026_2.pdf`. Namen, die im selben Durchlauf schon vergeben wurden, gelten als belegt; Testmodus und echter Durchlauf planen daher dieselben Namen.

## Unterstützte Formulare

| Anwesenheitsnachweis | Älteres Formular |
| --- | --- |
| <img src="../images/form-attendance.png" width="340" alt="Erfundener Anwesenheitsnachweis"> | <img src="../images/form-legacy.png" width="340" alt="Erfundenes älteres Formular"> |

**Anwesenheitsnachweis**, mit oder ohne Spalte *GR/EZ*:

- der Name steht in der Zeile über der Beschriftung *Name des Schülers*;
- das Geburtsdatum ist der Wert über *Geburtsdatum* (optional, dient als zusätzliches Merkmal);
- das Fach wird aus der Spalte *Fach* der Anwesenheitstabelle gelesen. Es steht meist gedruckt in jeder Zeile; es entscheidet die übereinstimmende Mehrheit. Einträge der Spalte *GR/EZ* (Gruppen- oder Einzelunterricht, oft handschriftlich als `EZ`, `E2`, `&z`) werden nie für das Fach gehalten.

**Älteres Formular:** eine Kopfzeile *Nachname Vorname Fach* über den Feldern *Bewilligungszeitraum*, *Monat*, *Unterrichtsart* und *Lehrer(in)*.

Der Formulartyp wird an den Beschriftungen der Seite erkannt, nie am Dateinamen. Ein Formular gehört zu genau einem Fach. Lehrkraft, Ort, *GR/EZ*, BG-Nummer und Unterschriften werden nie zur Zuordnung des Schülers oder für den Dateinamen verwendet; `Lehrer.csv` spielt beim Umbenennen keine Rolle. Name und Fach sind normalerweise gedruckt; handschriftliche Namen sind selten und gehen in die Prüfung.

## Prüfung

![Prüfung eines neuen Schülers](../images/review-new-student.png)

Ein Dokument kommt in die Prüfung, wenn sich das Programm nicht sicher ist, zum Beispiel:

- der Schüler steht nicht im Verzeichnis (*vermutlich ein neuer Schüler*);
- mehrere Schüler sind ähnlich, oder zwei Lesungen zeigen auf verschiedene Schüler;
- das Geburtsdatum auf dem Formular widerspricht dem Verzeichnis;
- das Fach ist unbekannt, widersprüchlich oder unlesbar;
- die Texterkennung ist für dieses Dokument fehlgeschlagen.

**Links:** die Liste der zu prüfenden Dokumente und darunter Ausschnitte aus dem Original-Scan — Name, Geburtsdatum und die ersten Zeilen der Spalte *Fach* (bei älteren Einträgen ohne Feldpositionen eine verkleinerte Seite). *PDF öffnen* öffnet die Datei im Standard-PDF-Programm.

**Rechts:** Die Statuszeile nennt, was zu entscheiden ist (*Schüler: …*, *Fach: …*), darunter der erkannte Name, Nachname, Vorname, Fach und Geburtsdatum (`TT.MM.JJJJ`, änderbar), die Schülersuche und die Aktionen.

Die **Schülersuche** arbeitet schon während der Eingabe: Teile von Nachname, Vorname oder die Nummer in beliebiger Reihenfolge, ohne Beachtung der Groß- und Kleinschreibung. Ohne Suchbegriff stehen die vorgeschlagenen Kandidaten oben (★, nach Ähnlichkeit); passt nichts wörtlich, markiert ≈ ähnliche Namen. Ein klarer Favorit ist vorausgewählt. *Enter* und die Pfeiltasten wählen nur aus; *Escape* leert die Suche; ein geänderter Suchbegriff oder ein anderes Dokument verwirft die Auswahl.

| Aktion | Wirkung |
| --- | --- |
| **Ausgewählten Schüler übernehmen** | Benennt das PDF für den gewählten Schüler um. Mit *Erkannten Namen als Alias speichern* wird die erkannte Schreibweise gemerkt, sodass der nächste Scan mit derselben Schreibweise automatisch zugeordnet wird. |
| **Als neuen Schüler anlegen** | Legt einen neuen Schüler mit vorausgefülltem Nachnamen, Vornamen und Geburtsdatum an (bei Bedarf korrigieren) und benennt das PDF um. |
| **Neues Fach übernehmen: …** | Erscheint, wenn ein unbekanntes Fach übereinstimmend gedruckt ist. Nach Bestätigung wird das Fach in `subjects_config.json` aufgenommen und gilt sofort. |
| **Nur umbenennen** | Benennt das PDF um, ohne das Verzeichnis zu ändern (nach gesonderter Bestätigung). |
| **Aus Liste entfernen** | Entfernt den Eintrag aus der Prüfliste; das PDF bleibt in `manual_check`. |

![Prüfung: neues Fach bei bekanntem Schüler](../images/review-new-subject.png)

Die umbenannte Datei wird zurück in den Scan-Ordner verschoben. Es gilt der Zeitraum, der mit dem Eintrag gespeichert wurde. Das Geburtsdatum vom Formular wird beim gewählten oder neuen Schüler gespeichert, wenn das Verzeichnis noch keines enthält — ein vorhandenes Datum wird nie überschrieben. Verzeichnis und Aliasnamen werden gespeichert, bevor das PDF verschoben wird; schlägt ein Schritt fehl, bleibt der Eintrag in der Prüfliste und kann erneut angewendet werden, ohne den Schüler doppelt anzulegen. Eine Liste widersprüchlicher Werte (etwa *Deutsch, Englisch*) wird nie als Alias gespeichert.

## Schülerverzeichnis

![Schülerverzeichnis](../images/students.png)

Die Registerkarte **Schüler** erlaubt Suchen und Filtern, Anlegen und Bearbeiten, Aktivieren und Deaktivieren (die Historie bleibt erhalten), das Ansehen und Löschen falscher Aliasnamen, die Suche nach möglichen Dubletten und den Import einer aktualisierten CSV-Datei.

`Schuler.csv` liegt im Programmordner. Minimales Format:

```csv
Nummer;Nachname;Vorname
1;Beispiel;Lena
```

- UTF-8 mit oder ohne BOM; neben `Vorname` wird auch die historische Überschrift `Forname` akzeptiert (sind beide vorhanden, müssen sie übereinstimmen).
- Optionale Spalten: `Aktiv` (Ja/Nein-Werte) und `Geburtsdatum` (`TT.MM.JJJJ`; leere Zelle = unbekannt). Zusätzliche Spalten, ihre Reihenfolge und die BOM bleiben erhalten.
- Der **Import** akzeptiert `;`, `,`, Tabulator und `|` als Trennzeichen und zeigt zuerst einen Vergleich: neue, unveränderte, widersprüchliche, doppelte und fehlende Einträge. Nach Bestätigung werden nur neue, widerspruchsfreie Zeilen ergänzt; Konflikte werden nie überschrieben und fehlende historische Einträge nie gelöscht. Vorher wird eine Sicherung `Schuler.csv.bak` (oder der nächste freie Zusatz) geschrieben, und die Datei wird atomar ersetzt. Dublettensuche und Import laufen im Hintergrund; währenddessen sind andere Änderungen am Verzeichnis und die Verarbeitung gesperrt.
- Wurden CSV oder Aliasdatei außerhalb des Programms geändert, vor dem Bearbeiten *Aktualisieren* klicken.

**Geburtsdatum.** Das optionale `Geburtsdatum` ist ein zusätzliches Merkmal zur Zuordnung: Eine Übereinstimmung stärkt die Entscheidung, ein sicher gelesenes abweichendes Datum verhindert eine automatische Entscheidung sogar bei exaktem Namen (eine einzelne Verwechslung ähnlicher Ziffern wie 5/6 gilt nicht als Widerspruch). Ein Datum wird nur bei Schülern ohne Datum automatisch eingetragen — aus einem Dokument mit exaktem Namen und sauber gelesenem Datum oder durch eine Entscheidung in der *Prüfung*. Die Spalte wird bei Bedarf mit Sicherung ergänzt. Das Verzeichnis mit Geburtsdaten enthält personenbezogene Daten und bleibt lokal.

**Aliasnamen** sind bestätigte andere Schreibweisen und liegen neben der CSV in `student_aliases.json`:

```json
{"version": 1, "aliases": [{"name": "Beispie1 Lena", "student_number": 1}]}
```

Nur eine Entscheidung in der Prüfung legt einen Alias an; automatische Korrekturen tun das nie.

## Fächer

`subjects_config.json` enthält die Fächerliste und übliche Abkürzungen:

```json
{
  "subjects": ["Biologie", "Chemie", "Deutsch", "Englisch", "Geografie", "Informatik",
               "Mathematik", "Physik", "Sachkunde", "Sachunterricht"],
  "aliases": {"Mathe": "Mathematik", "Bio": "Biologie", "Geo": "Geografie", "Sachunt.": "Sachunterricht"}
}
```

*Sachkunde* und *Sachunterricht* sind verschiedene Fächer. Falsch gelesene Fachnamen werden der Liste zugeordnet, wenn genau ein Fach nahe genug ist. Ein neues Fach wird entweder in der Datei ergänzt oder mit *Neues Fach übernehmen* in der Prüfung. Bestätigte Korrekturen von Fachschreibweisen stehen in `subjects_aliases.json`.

## Dateien des Programms

| Datei | Ort | Inhalt | In Git |
| --- | --- | --- | --- |
| `Schuler.csv` | Programmordner | Schülerverzeichnis (personenbezogene Daten) | nein |
| `student_aliases.json` | Programmordner | bestätigte Schreibweisen von Schülernamen | nein |
| `Lehrer.csv` | Programmordner | Liste der Lehrkräfte; wird beim Umbenennen nicht verwendet | nein |
| `subjects_config.json` | Programmordner | Fächerliste und Abkürzungen | ja (Vorgabe) |
| `subjects_aliases.json` | Programmordner | bestätigte Fachschreibweisen | ja (leer) |
| `manual_review.json` | jeder Scan-Ordner | Prüfliste mit Vorschlägen und Belegen | nein |
| `manual_check\` | jeder Scan-Ordner | PDFs, die auf die Prüfung warten | nein |
| `.launcher-logs\` | Programmordner | Diagnoseprotokolle | nein |
| `.venv\`, `.bootstrap\` | Programmordner | eigene Python-Umgebung und deren Sicherungen | nein |

CSV- und JSON-Dateien werden immer über eine temporäre Datei und atomares Ersetzen geschrieben. Eine beschädigte Prüfliste oder Aliasdatei wird nie stillschweigend durch eine leere ersetzt: Das Programm meldet den Fehler und lässt die Datei unverändert.
